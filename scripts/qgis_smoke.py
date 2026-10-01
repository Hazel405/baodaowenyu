# -*- coding: utf-8 -*-
"""在 QGIS 自带 Python 中验证：qgis.core 可用 + 插件包可导入 + 主类可实例化（offscreen）。"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.environ["APPDATA"], "QGIS", "QGIS3", "profiles", "default", "python", "plugins"))

from qgis.core import Qgis, QgsApplication
app = QgsApplication([], False)
app.initQgis()
print("QGIS version:", Qgis.QGIS_VERSION)

from qgis.gui import QgisInterface  # noqa: F401  确认 gui 绑定可用
import baodao_wenyu
factory = baodao_wenyu.classFactory


class FakeIface:  # 最小桩件，仅验证 initGui 代码路径
    _win = None
    def mainWindow(self):
        if FakeIface._win is None:
            from qgis.PyQt.QtWidgets import QMainWindow
            FakeIface._win = QMainWindow()
        return FakeIface._win
    def addToolBarIcon(self, a): pass
    def addPluginToMenu(self, *a): pass
    def removeToolBarIcon(self, a): pass
    def removePluginMenu(self, *a): pass
    def messageBar(self):
        class M:
            def pushInfo(self, *a): pass
            def pushWarning(self, *a): pass
        return M()
    def mapCanvas(self):
        class C:
            def refresh(self): pass
        return C()
    def addDockWidget(self, *a): pass


plug = factory(FakeIface())
plug.initGui()
print("plugin initGui OK, actions:", len(plug.actions))
app.exitQgis()
print("SMOKE PASS")
