# -*- coding: utf-8 -*-
"""qgis_capture.py — 用 QGIS 自带 Python 离屏渲染四工作台证据图（无需显示器/不弹窗口）。
运行：scripts\\qgis_capture.bat   输出：docs\\figures\\fig_*.png
证据图清单：览·全岛 / 览·台北都会 / 询·问答面板 / 析·分区统计 / 较·底图叠合"""
import os
import sys
import json
import urllib.request

os.environ["QT_QPA_PLATFORM"] = "windows"  # 真实 GUI 平台：离屏模式不绘制对话框内容
OUT = r"D:\宝岛文舆\docs\figures"
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(os.environ["APPDATA"], "QGIS", "QGIS3", "profiles", "default", "python", "plugins"))

from qgis.PyQt.QtWidgets import QApplication, QMainWindow
from qgis.PyQt.QtCore import QSize, QRect
from qgis.core import (QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer,
                       QgsDataSourceUri, QgsMapSettings, QgsRectangle,
                       QgsMapRendererSequentialJob, QgsCoordinateReferenceSystem)

app = QgsApplication([], False)
app.initQgis()
TILE = "http://127.0.0.1:8090"
PG = ("127.0.0.1", "5433", "tw", "postgres", "")


def pg_layer(schema, tbl, uri_geom="geom", filt=""):
    u = QgsDataSourceUri()
    u.setConnection(*PG)
    u.setDataSource(schema, tbl, uri_geom, filt)
    return QgsVectorLayer(u.uri(), tbl, "postgres")


def render(layers, extent, w, h, path, bg=None):
    ms = QgsMapSettings()
    ms.setLayers(layers)
    ms.setOutputSize(QSize(w, h))
    ms.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))  # extent 为经纬度，勿用 XYZ 层的 3857
    ms.setExtent(extent)
    if bg:
        ms.setBackgroundColor(bg)
    job = QgsMapRendererSequentialJob(ms)
    job.start()
    import time
    t0 = time.time()
    while job.isActive() and time.time() - t0 < 60:
        app.processEvents()
        time.sleep(0.02)
    img = job.renderedImage()
    img.save(path)
    print("saved", path)


# ── 底图 + 要素图层 ──
def xyz(name, path):
    return QgsRasterLayer(f"type=xyz&url={TILE}/{path}/%7Bz%7D/%7Bx%7D/%7By%7D.png", name, "wms")

street = xyz("街道图(OSM自渲)", "street")
terrain = xyz("地形(高程色+阴影)", "terrain")
print("street valid:", street.isValid(), "terrain valid:", terrain.isValid())
roads = pg_layer("tw", "roads", filt="class in ('motorway','trunk','primary')")
cities = pg_layer("tw", "gaz_tw", filt="fclass='P' AND population > 100000")
county = pg_layer("tw", "boundary", filt="level=2")

# 览·全岛（地形底图 + 县市界 + 主要城市）
whole = QgsRectangle(119.3, 21.9, 122.5, 25.3)
render([terrain, county, cities], whole, 1200, 900, os.path.join(OUT, "fig_lan_island.png"))
# 览·台北都会（街道底图 + 高速/快速路）
taipei = QgsRectangle(121.30, 24.90, 121.75, 25.20)
render([street, roads], taipei, 1200, 900, os.path.join(OUT, "fig_lan_taipei.png"))
# 较·同域双底图（地形叠街道半透明=卷帘证据）
render([street, terrain], whole, 1200, 900, os.path.join(OUT, "fig_jiao_overlay.png"))

# ── 询·问答面板（真实调用 :8600 一次） ──
from baodao_wenyu.panels.qa import QaDialog


class FakeIface:
    _win = None
    def mainWindow(self):
        if FakeIface._win is None:
            FakeIface._win = QMainWindow()
        return FakeIface._win
    def mapCanvas(self):
        class C:
            def refresh(self): pass
            def setExtent(self, *a): pass
        return C()
    def layerTreeRoot(self):
        class R:
            def findLayer(self, *a):
                class I:
                    def setItemVisibilityChecked(self, *a): pass
                return I()
        return R()
    def messageBar(self):
        class M:
            def pushInfo(self, *a): pass
            def pushWarning(self, *a): pass
        return M()


dlg = QaDialog(FakeIface())
dlg.edit.setText("高雄市有多少人口？")
try:
    req = urllib.request.Request("http://127.0.0.1:8600/api/ask",
        data=json.dumps({"question": "高雄市有多少人口？"}).encode(),
        headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
    dlg.on_answer(d)
except Exception as e:
    dlg.out.setPlainText("服务不可用: " + str(e)[:120])
dlg.resize(560, 430)
dlg.show()
import time as _t
for _ in range(40):
    app.processEvents()
    _t.sleep(0.05)
pix = dlg.grab()
pix.save(os.path.join(OUT, "fig_xun_qa.png"))
print("saved fig_xun_qa.png")

# ── 析·分区统计 ──
from baodao_wenyu.panels.analysis import AnalysisDialog
ad = AnalysisDialog(FakeIface())
if ad.combo.count():
    idx = next((i for i in range(ad.combo.count()) if "南投" in ad.combo.itemText(i)), 0)
    ad.combo.setCurrentIndex(idx)
    ad.zonal()
ad.resize(660, 460)
ad.show()
for _ in range(40):
    app.processEvents()
    _t.sleep(0.05)
ad.grab().save(os.path.join(OUT, "fig_xi_analysis.png"))
print("saved fig_xi_analysis.png")

app.exitQgis()
print("CAPTURE DONE")
