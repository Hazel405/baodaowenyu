# -*- coding: utf-8 -*-
"""图层工作台（实时模式）：勾选即加载、取消即移除；自动配色；提供一键缩放。"""
from qgis.PyQt.QtWidgets import QDockWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QPushButton
from qgis.core import (QgsDataSourceUri, QgsVectorLayer, QgsProject, QgsRectangle,
                       QgsSingleSymbolRenderer, QgsFillSymbol, QgsMarkerSymbol, QgsLineSymbol,
                       QgsCoordinateTransform, QgsCoordinateReferenceSystem)

PG = dict(host="127.0.0.1", port=5433, user="postgres", dbname="tw")
GROUPS = [
    ("政区", [("boundary 县市界", "tw", "boundary", "level=2"), ("admin_osm 乡镇界", "tw", "admin_osm", "level=8")]),
    ("地名", [("主要城市(中文名)", "tw", "cities_major", ""), ("gaz_tw 地形点(山/水)", "tw", "gaz_tw", "fclass IN ('T','H')")]),
    ("人口", [("县市人口(WorldPop)", "tw", "pop_county", ""), ("乡镇人口(WorldPop)", "tw", "pop_town", "")]),
    ("交通", [("roads 高快速", "tw", "roads", "class in ('motorway','trunk')"), ("roads 主次干道", "tw", "roads", "class in ('primary','secondary')"), ("rail 铁路", "tw", "rail", "")]),
    ("水系", [("水系线(河/运河)", "tw", "osm_water_line", "wclass in ('river','canal')"), ("水系面(湖库塘)", "tw", "osm_water_poly", "")]),
    ("聚落建筑", [("osm_buildings 建筑", "tw", "osm_buildings", ""), ("osm_landuse 用地", "tw", "osm_landuse", "")]),
    ("设施", [("power 电力", "tw", "power", ""), ("ports 港口", "tw", "ports", ""), ("airports 机场", "tw", "airports", "")]),
    ("事件", [("events 事件点", "tw", "events", "sqldate >= '2026-01-01'")]),
    ("地形衍生", [("contours 等高线", "tw", "contours", "")]),
]


def style_layer(lyr):
    t = lyr.geometryType()
    if t == 2:
        lyr.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
            {"color": "100,150,200,70", "outline_color": "40,60,90,220", "outline_width": "0.36"})))
    elif t == 0:
        lyr.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
            {"name": "circle", "size": "1.8", "color": "190,50,50,230", "outline_color": "255,255,255,255", "outline_width": "0.2"})))
    elif t == 1:
        lyr.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
            {"color": "220,130,40,220", "width": "0.35"})))


class BrowseDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("宝岛文舆 · 图层工作台")
        self.iface = iface
        self.loaded = {}          # label -> layer id
        w = QWidget(); lay = QVBoxLayout(w)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        for gname, items in GROUPS:
            g = QTreeWidgetItem([gname]); g.setExpanded(True)
            for label, schema, tbl, filt in items:
                it = QTreeWidgetItem([label]); it.setCheckState(0, 0)
                it.setData(0, 32, (schema, tbl, filt))
                g.addChild(it)
            self.tree.addTopLevelItem(g)
        self.tree.itemChanged.connect(self.on_toggle)
        lay.addWidget(self.tree)
        b1 = QPushButton("缩放至已加载数据"); b1.clicked.connect(self.zoom_all)
        b2 = QPushButton("清空已加载图层"); b2.clicked.connect(self.clear_all)
        lay.addWidget(b1); lay.addWidget(b2)
        self.setWidget(w)

    # 把图层范围变换到画布 CRS 后再设置视图（防 3857/4326 混用白屏）
    def _show(self, ext4326):
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        e = QgsRectangle(ext4326)
        src = QgsCoordinateReferenceSystem("EPSG:4326")
        if canvas_crs != src:
            try:
                tr = QgsCoordinateTransform(src, canvas_crs, QgsProject.instance().transformContext())
                e = tr.transformBoundingBox(e)
            except Exception:
                pass
        e.scale(1.25)
        self.iface.mapCanvas().setExtent(e)
        self.iface.mapCanvas().refresh()

    # 勾选=加载，取消=移除
    def on_toggle(self, it, col):
        data = it.data(0, 32)
        if not data:
            return
        label = it.text(0)
        pg = QgsProject.instance()
        if it.checkState(0) == 2:
            if label in self.loaded and pg.mapLayer(self.loaded[label]):
                return
            schema, tbl, filt = data
            uri = QgsDataSourceUri()
            uri.setConnection(PG["host"], str(PG["port"]), PG["dbname"], PG["user"], "")
            uri.setDataSource(schema, tbl, "geom", filt or "")
            lyr = QgsVectorLayer(uri.uri(), label, "postgres")
            if not lyr.isValid():
                self.iface.messageBar().pushWarning("宝岛文舆", f"加载失败: {label}")
                return
            style_layer(lyr)
            first = not any(pg.mapLayer(i) for i in self.loaded.values())
            pg.addMapLayer(lyr)
            self.loaded[label] = lyr.id()
            if first and not lyr.extent().isEmpty():
                self._show(lyr.extent())
        else:
            lid = self.loaded.pop(label, None)
            if lid and pg.mapLayer(lid):
                pg.removeMapLayer(lid)

    def zoom_all(self):
        pg = QgsProject.instance()
        ext = QgsRectangle(); ext.setMinimal(True)
        for lid in self.loaded.values():
            lyr = pg.mapLayer(lid)
            if lyr and not lyr.extent().isEmpty():
                ext.combineExtentWith(lyr.extent())
        if not ext.isEmpty():
            self._show(ext)

    def clear_all(self):
        pg = QgsProject.instance()
        for label in list(self.loaded):
            it = self.find_item(label)
            if it:
                it.setCheckState(0, 0)   # 触发 on_toggle 移除
        self.loaded.clear()

    def find_item(self, label):
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                it = g.child(j)
                if it.text(0) == label:
                    return it
        return None
