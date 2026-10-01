# -*- coding: utf-8 -*-
"""析 · 地形灾害参考工作台：县/乡镇 DEM 分区统计（tw.dem_zonal_stats）+ 暴雨情景受影响聚落。
演示口径：台风暴雨情景 = 高坡度乡镇 或 近 named 河川/运河 100m 的聚落点。"""
import psycopg2
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
                                 QTextBrowser, QLabel)
from qgis.core import QgsProject

PG = dict(host="127.0.0.1", port=5433, user="postgres", dbname="tw")
# 县市 繁中显示名 → boundary(英文) 定位映射
CN2EN = {"基隆": "Keelung", "新北": "New Taipei", "台北": "Taipei", "桃園": "Taoyuan", "台中": "Taichung",
         "台南": "Tainan", "高雄": "Kaohsiung", "新竹县": "Hsinchu", "新竹市": "Hsinchu", "苗栗": "Miaoli",
         "彰化": "Changhua", "南投": "Nantou", "雲林": "Yunlin", "嘉義": "Chiayi", "屏東": "Pingtung",
         "宜蘭": "Yilan", "花蓮": "Hualien", "臺東": "Taitung", "澎湖": "Penghu", "金門": "Kinmen",
         "連江": "Lienchiang"}


class AnalysisDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("宝岛文舆 · 析（地形与灾害参考）")
        self.resize(660, 460)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.combo = QComboBox()
        try:
            c = psycopg2.connect(**PG); cur = c.cursor()
            cur.execute("SELECT DISTINCT unit_name FROM tw.dem_zonal_stats WHERE unit_type='county' ORDER BY 1")
            for (n,) in cur.fetchall():
                self.combo.addItem(n)
            c.close()
        except Exception as e:
            lay.addWidget(QLabel("PostGIS 未连接：" + str(e)[:80]))
        row.addWidget(QLabel("县市：")); row.addWidget(self.combo)
        b1 = QPushButton("分区统计"); b1.clicked.connect(self.zonal)
        b2 = QPushButton("暴雨情景·受影响聚落"); b2.clicked.connect(self.scenario)
        b3 = QPushButton("定位到地图"); b3.clicked.connect(self.locate)
        row.addWidget(b1); row.addWidget(b2); row.addWidget(b3)
        lay.addLayout(row)
        self.out = QTextBrowser()
        lay.addWidget(self.out)
        lay.addWidget(QLabel("口径：70m SRTM（空洞修补）派生层分区统计（dem_zonal_stats，县 21 + 乡镇 201）；"
                             "结果仅作教学/研究参考，非工程选址依据。"))

    def _cur(self):
        c = psycopg2.connect(**PG)
        return c, c.cursor()

    def _prefix(self):
        name = self.combo.currentText() or ""
        return name[:2]

    def zonal(self):
        pfx = self._prefix()
        try:
            c, cur = self._cur()
            cur.execute("""SELECT unit_type, count(*), round(avg(slope_mean)::numeric,1),
                                  round(avg(mobility_mean)::numeric,1), round(max(elev_max)::numeric,0)
                           FROM tw.dem_zonal_stats
                           WHERE (unit_type='county' AND unit_name LIKE %s) OR (unit_type<>'county' AND parent LIKE %s)
                           GROUP BY unit_type ORDER BY unit_type""", (pfx + "%", pfx + "%"))
            rows = cur.fetchall()
            c.close()
            html = ("<table border=1 cellpadding=4><tr><th>层级</th><th>单元数</th><th>平均坡度°</th>"
                    "<th>平均通行难度</th><th>最高点m</th></tr>")
            for r in rows:
                lvl = {"county": "县级", "town": "乡镇级", "township": "乡镇级"}.get(r[0], r[0])
                html += "<tr>" + "".join(f"<td>{x}</td>" for x in [lvl, r[1], r[2], r[3], r[4]]) + "</tr>"
            html += "</table>"
            self.out.setHtml(html if rows else f"<p>『{pfx}』无分区统计（覆盖缺口如实记档）</p>")
        except Exception as e:
            self.out.setPlainText("查询失败：" + str(e)[:200])

    def scenario(self):
        pfx = self._prefix()
        try:
            c, cur = self._cur()
            cur.execute("""SELECT z.unit_name, round(z.slope_mean::numeric,1), round(z.steep30_frac::numeric,3),
                                  round(z.stream_l2_frac::numeric,3), round(z.mobility_mean::numeric,1)
                           FROM tw.dem_zonal_stats
                           WHERE unit_type<>'county' AND parent LIKE %s
                             AND (slope_mean > 25 OR stream_l2_frac > 0.05)
                           ORDER BY slope_mean DESC LIMIT 12""", (pfx + "%",))
            rows = cur.fetchall()
            c.close()
            if not rows:
                self.out.setPlainText(f"{pfx}县市内乡镇均未命中高风险条件（坡度>25° 或 2级河道占比>5%）。")
                return
            txt = "\n".join(f"· {r[0]}：平均坡度 {r[1]}°，>30° 坡占比 {float(r[2])*100:.1f}%，"
                            f"2级河道占比 {float(r[3])*100:.1f}%，通行难度 {r[4]}" for r in rows)
            self.out.setPlainText(f"{pfx}县市 暴雨情景高风险乡镇清单（按坡度降序）：\n" + txt)
        except Exception as e:
            self.out.setPlainText("情景查询失败：" + str(e)[:200])

    def locate(self):
        name = self.combo.currentText() or ""
        en = ""
        for k, v in CN2EN.items():
            if name.startswith(k):
                en = v
                break
        if not en:
            return
        for lyr in QgsProject.instance().mapLayersByName("boundary"):
            lyr.setSubsetString(f"name ~* '{en}'")
            self.iface.mapCanvas().refresh()
            feats = list(lyr.getFeatures())
            if feats:
                from qgis.core import QgsRectangle
                bb = feats[0].geometry().boundingBox()
                bb.scale(1.4)
                self.iface.mapCanvas().setExtent(bb)
                self.iface.mapCanvas().refresh()
            break
