# -*- coding: utf-8 -*-
"""宝岛文舆 · QGIS 插件主类（作品主体）。
四个工作台动作对应参赛叙事：览（底图+图层）/ 询（防幻觉地志问答）/ 较（历史舆图叠合）/ 析（地形灾害参考）。
GPL-2.0-or-later"""
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsRasterLayer, QgsProject

from .panels.browse import BrowseDock
from .panels.qa import QaDialog
from .panels.compare import CompareDialog
from .panels.analysis import AnalysisDialog

TILE_BASE = "http://127.0.0.1:8090"


class BaodaoWenyuPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.actions = []

    def initGui(self):
        specs = [("览·图层工作台", self.open_browse),
                 ("询·地志智询", self.open_qa),
                 ("较·历史舆图叠合", self.open_compare),
                 ("析·地形灾害参考", self.open_analysis),
                 ("＋注入自产底图", self.add_basemaps)]
        for text, fn in specs:
            a = QAction(QIcon(), "宝岛文舆｜" + text, self.iface.mainWindow())
            a.triggered.connect(fn)
            self.iface.addToolBarIcon(a)
            self.iface.addPluginToMenu("宝岛文舆", a)
            self.actions.append(a)

    def unload(self):
        for a in self.actions:
            self.iface.removeToolBarIcon(a)
            self.iface.removePluginMenu("宝岛文舆", a)
        if self.dock:
            self.dock.close()

    # 览：自产 XYZ 瓦片底图（street/terrain/hillshade），零第三方瓦片再分发
    def add_basemaps(self):
        pg = QgsProject.instance()
        added = 0
        for name, path in [("街道图(OSM自渲)", "street"), ("地形(高程色+阴影)", "terrain"), ("山体阴影", "hillshade")]:
            uri = f"type=xyz&url={TILE_BASE}/{path}/%7Bz%7D/%7Bx%7D/%7By%7D.png"
            lyr = QgsRasterLayer(uri, name, "wms")
            if lyr.isValid():
                pg.addMapLayer(lyr)
                added += 1
        self.iface.messageBar().pushInfo(
            "宝岛文舆", f"底图注入完成 {added}/3（本地瓦片服务 :8090；若为 0 请先运行 scripts\\start_services.bat）")

    def open_browse(self):
        if not self.dock:
            self.dock = BrowseDock(self.iface)
        self.dock.show()
        self.iface.addDockWidget(2, self.dock)

    def open_qa(self):
        QaDialog(self.iface).exec()

    def open_compare(self):
        CompareDialog(self.iface).exec()

    def open_analysis(self):
        AnalysisDialog(self.iface).exec()
