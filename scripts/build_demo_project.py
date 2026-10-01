# -*- coding: utf-8 -*-
"""build_demo_project.py — 生成"打开即惊艳"的演示工程 demo_project.qgz：
街道/地形瓦片底图 + 县市人口分级设色 + 县市界 + 乡镇界 + 主要城市标注 + 高快速路网 + 2026 事件点。
运行：scripts\\run_build_project.bat   产物：D:\\宝岛文舆\\demo_project.qgz + docs/figures/fig_demo_preview.png
"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from qgis.core import (QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer, QgsDataSourceUri,
                       QgsRectangle, QgsCoordinateReferenceSystem, QgsMapSettings,
                       QgsMapRendererSequentialJob, QgsGraduatedSymbolRenderer, QgsRendererRange,
                       QgsFillSymbol, QgsMarkerSymbol, QgsLineSymbol, QgsSingleSymbolRenderer,
                       QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling,
                       QgsTextBackgroundSettings)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import QSize, QSizeF

app = QgsApplication([], False); app.initQgis()
TILE = "http://127.0.0.1:8090"
PG = ("127.0.0.1", "5433", "tw", "postgres", "")
proj = QgsProject.instance()
proj.clear()


def vl(schema, tbl, filt=""):
    u = QgsDataSourceUri()
    u.setConnection(*PG)
    u.setDataSource(schema, tbl, "geom", filt)
    return QgsVectorLayer(u.uri(), tbl, "postgres")


# 底图两层
terrain = QgsRasterLayer(f"type=xyz&url={TILE}/terrain/%7Bz%7D/%7Bx%7D/%7By%7D.png", "地形晕渲", "wms")
street = QgsRasterLayer(f"type=xyz&url={TILE}/street/%7Bz%7D/%7Bx%7D/%7By%7D.png", "街道图", "wms")
proj.addMapLayers([terrain, street])

# 县市人口分级设色（7 级，暖色系）
pop = vl("tw", "pop_county")
brk = [(0, 100000, "#fff5eb"), (100000, 300000, "#fee0c6"), (300000, 600000, "#fdae6b"),
       (600000, 1500000, "#fd8d3c"), (1500000, 2500000, "#e31a1c"), (2500000, 99999999, "#8c0d0e")]
rngs = []
for lo, hi, col in brk:
    s = QgsFillSymbol.createSimple({"color": col + "cc", "outline_color": "60,40,30,200", "outline_width": "0.3"})
    rngs.append(QgsRendererRange(lo, hi, s, f"{lo/10000:.0f}–{hi/10000:.0f}万" if hi < 9e7 else f">{lo/10000:.0f}万"))
pop.setRenderer(QgsGraduatedSymbolRenderer("pop", rngs))
pop.setCustomProperty("legend", True)
proj.addMapLayer(pop)

# 乡镇界（细灰虚线）
town = vl("tw", "admin_osm", "level=8")
town.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
    {"color": "120,120,120,140", "width": "0.15", "line_style": "dash"})))
proj.addMapLayer(town)

# 县市界（粗描边）
cty = vl("tw", "boundary", "level=2")
cty.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
    {"color": "40,60,90,230", "width": "0.5"})))
proj.addMapLayer(cty)

# 路网（高快速，橙色渐细）
rd = vl("tw", "roads", "class in ('motorway','trunk')")
rd.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
    {"color": "230,120,40,200", "width": "0.3"})))
proj.addMapLayer(rd)

# 水系面（淡蓝）
wp = vl("tw", "osm_water_poly", "kind in ('reservoir','lake','water')")
wp.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
    {"color": "140,190,220,160", "outline_color": "90,140,180,120", "outline_width": "0.1"})))
proj.addMapLayer(wp)

# 主要城市点 + 中文标注（vw_cities 视图：优先取库内中文别名）
city = vl("tw", "cities_major")
city.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
    {"name": "circle", "size": "2.2", "color": "180,30,30,255", "outline_color": "255,255,255,255", "outline_width": "0.35"})))
ls = QgsPalLayerSettings()
ls.fieldName = "name_zh"
tf = QgsTextFormat()
tf.setFont(QFont("SimHei", 9))
tf.setSize(9)
tf.setColor(QColor(30, 30, 30))
bg = QgsTextBackgroundSettings()
bg.setEnabled(True)
bg.setSize(QSizeF(2.2, 1.6))
bg.setFillColor(QColor(255, 255, 255, 170))
bg.setStrokeColor(QColor(120, 120, 120, 120))
tf.setBackground(bg)
ls.setFormat(tf)
ls.placement = QgsPalLayerSettings.AroundPoint
ls.dist = 1.2
city.setLabeling(QgsVectorLayerSimpleLabeling(ls))
city.setLabelsEnabled(True)
proj.addMapLayer(city)

# 2026 事件点（紫色小三角）
ev = vl("tw", "events", "sqldate >= '2026-01-01'")
ev.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
    {"name": "triangle", "size": "1.4", "color": "120,70,190,140", "outline_color": "60,30,110,200", "outline_width": "0.1"})))
proj.addMapLayer(ev)

# 工程 CRS 与默认可见范围（打开即定位台湾）
proj.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
proj.writeEntry("Gui", "/CanvasColor", "170,211,223")   # 画布背景=海蓝，消白棋盘格
proj.writeEntry("Gui", "/CanvasColorRedPart", "170")
proj.writeEntry("Gui", "/CanvasColorGreenPart", "211")
proj.writeEntry("Gui", "/CanvasColorBluePart", "223")
ms = QgsMapSettings()
ms.setLayers(list(reversed(list(proj.mapLayers().values()))))  # 树序=自下而上，渲染列表=自上而下
ms.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
ms.setExtent(QgsRectangle(119.3, 21.9, 122.5, 25.3))

qgz = r"D:\宝岛文舆\demo_project.qgz"
proj.write(qgz)
print("saved", qgz)

# 无头渲染预览图（验证效果 + 给用户看）
ms.setOutputSize(QSize(1400, 1050))
job = QgsMapRendererSequentialJob(ms)
job.start(); job.waitForFinished()
img = job.renderedImage()
img.save(r"D:\宝岛文舆\docs\figures\fig_demo_preview.png")
print("preview saved")
app.exitQgis()
