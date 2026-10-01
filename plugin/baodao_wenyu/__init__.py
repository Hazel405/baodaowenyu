# -*- coding: utf-8 -*-
"""宝岛文舆 QGIS 插件入口（GPL-2.0-or-later）。"""
from .plugin import BaodaoWenyuPlugin


def classFactory(iface):  # noqa: N802
    return BaodaoWenyuPlugin(iface)
