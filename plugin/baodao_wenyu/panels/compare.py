# -*- coding: utf-8 -*-
"""较 · 历史舆图叠合工作台：时间轴选择时点 → 加载 hist_* 瓦片层 → 透明度调节与现状图对比。
数据来自 pipeline/make_hist_tiles.py 产物（basemap/hist_<id>），经 GCP 纠正、权利逐幅登记。"""
import os
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QSlider, QLabel,
                                 QPushButton, QComboBox, QFormLayout, QMessageBox)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsRasterLayer

TILE_BASE = "http://127.0.0.1:8090"
def _find_basemap():
    """自动定位 basemap 根：BAODAO_HOME → 常见安装位置扫描 → 原开发机路径兜底。"""
    import glob as _g
    home = os.environ.get("BAODAO_HOME")
    cands = []
    if home:
        cands.append(os.path.join(home, "basemap"))
    cands += _g.glob(os.path.join(r"D:\宝岛文娱", "*", "basemap"))
    cands.append(r"D:\宝岛文舆\basemap")
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[-1]

BASMAP_ROOT = _find_basemap()
# 时点登记（id, 标题, 年份）——与 data/registry.csv 同步
PERIODS = [("jpc200k_1904", "日治实测地形图 二十万分一", 1904),
           ("jpc50k_1921", "台湾堡图（街庄名）", 1921),
           ("tw1947_50k", "光复初期五万分之一", 1947),
           ("usgs_onc_1960", "ONC 一百万分之一", 1960)]


class CompareDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.hist_layer = None
        self.setWindowTitle("宝岛文舆 · 较（历史舆图 × 现状叠合）")
        self.resize(460, 240)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.combo = QComboBox()
        avail = []
        for pid, title, year in PERIODS:
            has = os.path.isdir(os.path.join(BASMAP_ROOT, "hist_" + pid))
            self.combo.addItem(f"{year} · {title}" + ("" if has else "（待摆渡）"), pid)
            if has:
                avail.append(pid)
        form.addRow("历史时点", self.combo)
        lay.addLayout(form)

        row = QHBoxLayout()
        row.addWidget(QLabel("历史图上透明度"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100); self.slider.setValue(55)
        self.slider.valueChanged.connect(self._set_opacity)
        row.addWidget(self.slider)
        lay.addLayout(row)

        btns = QHBoxLayout()
        b1 = QPushButton("叠加到当前工程"); b1.clicked.connect(self.load_hist)
        b2 = QPushButton("移除历史层"); b2.clicked.connect(self.remove_hist)
        btns.addWidget(b1); btns.addWidget(b2)
        lay.addLayout(btns)
        lay.addWidget(QLabel("对比要点：堡图街庄名 ↔ 今地名（闽南/客家通名）；1947 聚落形态 ↔ 2020 建筑足迹；\n"
                             "瓦片与现状图同一金字塔坐标系（EPSG:3857），卷帘=直接调透明度。"))

    def load_hist(self):
        pid = self.combo.currentData()
        if not os.path.isdir(os.path.join(BASMAP_ROOT, "hist_" + pid)):
            QMessageBox.information(self, "尚缺图幅",
                                    "该历史图尚未入库（扫描件摆渡中，见 docs/历史舆图获取清单.md）。\n"
                                    "可先用“览”工作台的街道图与地形晕渲两底图叠置，体验同一坐标系下的透明度卷帘机制。")
            return
        self.remove_hist()
        lyr = QgsRasterLayer(f"type=xyz&url={TILE_BASE}/hist_{pid}/%7Bz%7D/%7Bx%7D/%7By%7D.png", f"历史图·{pid}", "wms")
        if lyr.isValid():
            QgsProject.instance().addMapLayer(lyr)
            lyr.setOpacity(self.slider.value() / 100.0)
            lyr.renderer().setBrightness(-10)
            self.hist_layer = lyr
            self.iface.layerTreeRoot().findLayer(lyr).setItemVisibilityChecked(True)

    def remove_hist(self):
        if self.hist_layer:
            QgsProject.instance().removeMapLayer(self.hist_layer.id())
            self.hist_layer = None

    def _set_opacity(self, v):
        if self.hist_layer:
            self.hist_layer.setOpacity(v / 100.0)
