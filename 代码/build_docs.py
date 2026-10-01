# -*- coding: utf-8 -*-
"""build_docs.py — 生成两份参赛文档并安全落盘。

用法：python 代码\\build_docs.py
产物：技术报告_宝岛文舆_v1.docx、获奖胜率评估_宝岛文舆.docx（均在仓库根目录）

先写到 代码/_tmp_*.docx 再 os.replace 覆盖正式文件：
WPS/Word 打开着目标文件时覆盖会 PermissionError，此时自动退回写 v2 并给出提示，
避免生成器中途失败留下半成品（历史坑：直接覆盖被占用文件会抛错并中断整条命令链）。
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("gd", os.path.join(HERE, "gen_docs.py"))
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

R = os.path.join(HERE, "_tmp_report.docx")
O = os.path.join(HERE, "_tmp_odds.docx")


def put(src, dst):
    try:
        os.replace(src, dst)
        print("OK", os.path.basename(dst))
        return True
    except PermissionError:
        print("占用中，无法覆盖：", os.path.basename(dst))
        return False


if __name__ == "__main__":
    gd.build_report(R)
    gd.build_odds(O)
    v1 = os.path.join(ROOT, "技术报告_宝岛文舆_v1.docx")
    if not put(R, v1):
        put(R, os.path.join(ROOT, "技术报告_宝岛文舆_v2.docx"))
        print("→ 请先关闭 WPS/Word 中的 v1，再重跑本脚本（或直接删除 v2 后重跑）")
    put(O, os.path.join(ROOT, "获奖胜率评估_宝岛文舆.docx"))
