# -*- coding: utf-8 -*-
"""图层工作台：PostGIS(tw) 20 表 → 9 组图层树，一键加载。"""
from qgis.PyQt.QtWidgets import QDockWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QPushButton
from qgis.core import QgsDataSourceUri, QgsVectorLayer, QgsProject

PG = dict(host="127.0.0.1", port=5433, user="postgres", dbname="tw")
GROUPS = [
    ("政区", [("boundary", "tw", "boundary", "level=2"), ("admin_osm", "tw", "admin_osm", "level=8")]),
    ("地名", [("gaz_tw 地名点", "tw", "gaz_tw", "true"), ("gaz_alias 别名(无几何)", None, None, None)]),
    ("人口", [("县市人口", "public", "tw_demo_countypop", "true"), ("乡镇人口", "public", "tw_demo_townpop", "true")]),
    ("交通", [("roads 道路", "tw", "roads", "class='motorway'"), ("rail 铁路", "tw", "rail", "true")]),
    ("水系", [("osm_water_line", "tw", "osm_water_line", "wclass='river'"), ("osm_water_poly", "tw", "osm_water_poly", "true")]),
    ("聚落建筑", [("osm_buildings", "tw", "osm_buildings", "true"), ("osm_landuse", "tw", "osm_landuse", "true")]),
    ("设施", [("power 电力", "tw", "power", "true"), ("ports 港口", "tw", "ports", "true"), ("airports 机场", "tw", "airports", "true")]),
    ("事件", [("events 事件", "tw", "events", "true"), ("ts_attention 关注度", None, None, None)]),
    ("地形衍生", [("contours 等高线", "tw", "contours", "true"), ("dem_zonal_stats", None, None, None)]),
]


class BrowseDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("宝岛文舆 · 图层工作台")
        self.iface = iface
        w = QWidget(); lay = QVBoxLayout(w)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        for gname, items in GROUPS:
            g = QTreeWidgetItem([gname]); g.setExpanded(True)
            for label, schema, tbl, filt in items:
                it = QTreeWidgetItem([label]); it.setCheckState(0, 0)
                it.setData(0, 32, (schema, tbl, filt))  # Qt.UserRole
                g.addChild(it)
            self.tree.addTopLevelItem(g)
        lay.addWidget(self.tree)
        btn = QPushButton("加载勾选项"); btn.clicked.connect(self.load_checked)
        lay.addWidget(btn)
        self.setWidget(w)

    def load_checked(self):
        pg = QgsProject.instance()
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                it = g.child(j)
                if it.checkState(0) == 2 and it.data(0, 32):
                    schema, tbl, filt = it.data(0, 32)
                    if not schema:
                        continue
                    uri = QgsDataSourceUri()
                    uri.setConnection(PG["host"], str(PG["port"]), PG["dbname"], PG["user"], "")
                    uri.setDataSource(schema, tbl, "geom", filt or "")
                    lyr = QgsVectorLayer(uri.uri(), it.text(0), "postgres")
                    if lyr.isValid():
                        pg.addMapLayer(lyr)
                    else:
                        self.iface.messageBar().pushWarning("宝岛文舆", f"加载失败: {it.text(0)}")
