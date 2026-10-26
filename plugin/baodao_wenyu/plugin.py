# -*- coding: utf-8 -*-
"""宝岛文舆 · QGIS 插件主类（作品主体）。
四个工作台动作对应参赛叙事：览（底图+图层）/ 询（防幻觉地志问答）/ 较（历史舆图叠合）/ 析（地形灾害参考）。
GPL-2.0-or-later"""
import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction, QToolBar
from qgis.core import QgsRasterLayer, QgsProject

from .panels.browse import BrowseDock
from .panels.qa import QaDialog
from .panels.compare import CompareDialog
from .panels.analysis import AnalysisDialog

TILE_BASE = os.environ.get("BAODAO_TILES", "http://127.0.0.1:8090")
ICON = os.path.join(os.path.dirname(__file__), "icon.png")


class BaodaoWenyuPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.actions = []
        self.toolbar = None

    def initGui(self):
        icon = QIcon(ICON)
        specs = [("★ 打开演示工程", self.open_demo),
                 ("览·图层工作台", self.open_browse),
                 ("询·地志智询", self.open_qa),
                 ("较·历史舆图叠合", self.open_compare),
                 ("析·地形灾害参考", self.open_analysis),
                 ("＋注入自产底图", self.add_basemaps)]
        self.toolbar = QToolBar("宝岛文舆")
        self.toolbar.setIconSize(self.toolbar.iconSize())
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for text, fn in specs:
            a = QAction(icon, "宝岛文舆｜" + text, self.iface.mainWindow())
            a.triggered.connect(fn)
            self.toolbar.addAction(a)
            self.iface.addPluginToMenu("宝岛文舆", a)
            self.actions.append(a)
        self.iface.mainWindow().addToolBar(Qt.TopToolBarArea, self.toolbar)
        self.toolbar.show()

    def unload(self):
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
        for a in self.actions:
            self.iface.removePluginMenu("宝岛文舆", a)

    # ★ 一键打开预配好的演示工程（分级设色/标注/事件/路网全配好，打开即满屏）
    @staticmethod
    def _find_project_file(name):
        """自动定位项目根下的文件：BAODAO_HOME 环境变量 → 常见安装位置扫描。"""
        import glob as _g
        home = os.environ.get("BAODAO_HOME")
        cands = []
        if home:
            cands.append(os.path.join(home, name))
        cands += _g.glob(os.path.join(r"D:\宝岛文娱", "*", name))
        cands.append(os.path.join(r"D:\宝岛文舆", name))
        for c in cands:
            if os.path.exists(c):
                return c
        return None

    def open_demo(self):
        try:
            qgz = self._find_project_file("demo_project.qgz")
            if not qgz:
                self.iface.messageBar().pushWarning("宝岛文舆", "演示工程未生成：请先运行 scripts\\run_build_project.bat")
                return
            QgsProject.instance().clear()
            QgsProject.instance().read(qgz)
            from qgis.core import QgsRectangle
            self.iface.mapCanvas().setExtent(QgsRectangle(119.3, 21.9, 122.5, 25.3))
            self.iface.mapCanvas().refresh()
        except Exception:
            import traceback
            tb = traceback.format_exc()
            try:
                with open(r"C:\Temp\baodao_err.log", "w", encoding="utf-8") as f:
                    f.write(tb)
            except Exception:
                pass
            self.iface.messageBar().pushCritical("宝岛文舆", "打开演示工程出错，详见 C:\\Temp\\baodao_err.log")

    # 览：自产 XYZ 瓦片底图（street/terrain/hillshade），零第三方瓦片再分发
    def add_basemaps(self):
        from qgis.core import QgsCoordinateReferenceSystem
        pg = QgsProject.instance()
        added = 0
        for name, path in [("街道图(OSM自渲)", "street"), ("地形(高程色+阴影)", "terrain"), ("山体阴影", "hillshade")]:
            uri = f"type=xyz&url={TILE_BASE}/{path}/%7Bz%7D/%7Bx%7D/%7By%7D.png"
            lyr = QgsRasterLayer(uri, name, "wms")
            if lyr.isValid():
                pg.addMapLayer(lyr)
                added += 1
        # 统一工程 CRS 为 4326：XYZ 层会随投影重画，但避免矢量 extent（度）
        # 被当作 3857（米）解释导致视图飞进大洋变白屏
        pg.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        # 注入后自动飞到台湾全景（打开即有内容）
        from qgis.core import QgsRectangle
        self.iface.mapCanvas().setExtent(QgsRectangle(119.3, 21.9, 122.5, 25.3))
        self.iface.mapCanvas().refresh()
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
