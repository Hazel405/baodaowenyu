# -*- coding: utf-8 -*-
"""build_demo_project.py — 生成"打开即惊艳"的演示工程 demo_project.qgz：
街道/地形瓦片底图 + 县市人口分级设色 + 县市界 + 乡镇界 + 主要城市标注 + 高快速路网 + 2026 事件点。
运行：scripts\\run_build_project.bat   产物：D:\\宝岛文舆\\demo_project.qgz + docs/figures/fig_demo_preview.png
"""
import os
if os.environ.get("DEMO_HEADLESS") == "1":      # 无头平台字体库为空，中文标注会变豆腐块，默认用 GUI 平台渲染
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
from qgis.core import (QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer, QgsDataSourceUri,
                       QgsRectangle, QgsCoordinateReferenceSystem, QgsMapSettings,
                       QgsMapRendererSequentialJob, QgsGraduatedSymbolRenderer, QgsRendererRange,
                       QgsFillSymbol, QgsMarkerSymbol, QgsLineSymbol, QgsSingleSymbolRenderer,
                       QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling,
                       QgsTextBackgroundSettings)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import QSize, QSizeF

app = QgsApplication([], False)
app.initQgis()
from qgis.PyQt.QtGui import QFontDatabase
_db = QFontDatabase()
for _fp in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc"):
    if os.path.exists(_fp):
        _db.addApplicationFont(_fp)
LABEL_FONT = next((f for f in ("Microsoft YaHei", "SimHei", "Noto Sans SC", "SimSun")
                   if f in _db.families()), "Sans Serif")
print("font families:", len(_db.families()), "| label font:", LABEL_FONT)
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
# 街道瓦片在低层级（全岛视角）海面填充不一致，会出现棋盘格 → 仅缩放到都会尺度以下才显示
street.setScaleBasedVisibility(True)
street.setMaximumScale(320000)                 # 比例尺分母 >32 万（缩小过度）时不绘制
proj.addMapLayers([terrain, street])

# 县市人口分级设色（6 级，暖色系；半透明以透出下方地形晕渲）
pop = vl("tw", "pop_county")


def rgba(hexcol, alpha):
    c = QColor(hexcol)
    return f"{c.red()},{c.green()},{c.blue()},{alpha}"


brk = [(0, 100000, "#fff5eb"), (100000, 300000, "#fee0c6"), (300000, 600000, "#fdae6b"),
       (600000, 1500000, "#fd8d3c"), (1500000, 2500000, "#e31a1c"), (2500000, 99999999, "#8c0d0e")]
rngs = []
for lo, hi, col in brk:
    s = QgsFillSymbol.createSimple({"color": rgba(col, 205), "outline_color": "60,40,30,200", "outline_width": "0.3"})
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
tf.setFont(QFont(LABEL_FONT, 9))
tf.setSize(9)
tf.setColor(QColor(45, 20, 15))
tf.buffer().setEnabled(True)
tf.buffer().setSize(1.4)
tf.buffer().setColor(QColor(255, 255, 255, 235))
tf.buffer().setJoinStyle(1)          # Qt.PenJoinStyle.RoundJoin
ls.setFormat(tf)
ls.placement = QgsPalLayerSettings.AroundPoint
ls.dist = 1.2
ls.allowDegradedLabels = False
ls.preservePlace = True
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
proj.writeEntry("Gui", "/CanvasColor", "198,224,232")   # 画布背景=海蓝，消除海面空白格
proj.writeEntry("Gui", "/CanvasColorRedPart", "198")
proj.writeEntry("Gui", "/CanvasColorGreenPart", "224")
proj.writeEntry("Gui", "/CanvasColorBluePart", "232")
ms = QgsMapSettings()
ms.setBackgroundColor(QColor(198, 224, 232))      # 预览海面底色（与工程画布色一致）
ms.setLayers([l for l in reversed(list(proj.mapLayers().values())) if l.id() != street.id()])  # 预览不含街道瓦片（低层级海面棋盘格）
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
