# -*- coding: utf-8 -*-
"""region.py — 区域配置加载器（区域模板化：一份配置驱动 pipeline / 服务 / 网页三端）。

配置位置：config/region_<code>.json（code 由文件名承载，配置内不写 code，避免两处不一致）
选择优先级：显式参数 code > 环境变量 BW_REGION > 默认 "tw"
web 子集：export_web_data.py 会把它写入 web/data/region.json，网页查看端启动时读取。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 项目根（本文件位于 pipeline/ 下）
CONFIG_DIR = os.path.join(ROOT, "config")


def load(code=None):
    """载入区域配置；code 缺省取 BW_REGION 环境变量，再缺省 tw。"""
    code = (code or os.environ.get("BW_REGION") or "tw").lower()
    path = os.path.join(CONFIG_DIR, f"region_{code}.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["code"] = code
    return cfg


def web_subset(cfg):
    """网页查看端所需的子集（不含数据源细节，避免无关信息进随包快照）。"""
    return {k: cfg[k] for k in ("code", "name_zh", "short_zh", "map", "admin", "timeline")}


if __name__ == "__main__":
    import sys
    print(json.dumps(load(sys.argv[1] if len(sys.argv) > 1 else None), ensure_ascii=False, indent=1))
