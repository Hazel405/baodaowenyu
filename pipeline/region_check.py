# -*- coding: utf-8 -*-
"""region_check.py — 区域配置（config/region_<code>.json）静态校验器。

用法：python pipeline\\region_check.py [code …]   （缺省校验 config/ 下全部 region_*.json）
校验项：
  1. 结构：必需键齐全（name_zh/short_zh/pg/map/admin/timeline/sources 及各子键）
  2. bbox：[minx, miny, maxx, maxy] 四点、经度 ±180、纬度 ±90、min < max
  3. center：[lon, lat] 合法且在 bbox 内（允许少量越界，警告不报错）
  4. zoom：0–20 数字
  5. timeline：YYYY-MM 格式、start <= end
  6. pg：dbname/schema 非空、schema 合法 SQL 标识符（小写字母开头，因直接进 f-string SQL）
  7. sources：geonames/osm/worldpop/gdelt 四类来源齐全且关键字段在位
退出码：0 = 全部通过；1 = 有错误（warnings 不计）。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

REQUIRED_TOP = ("name_zh", "short_zh", "pg", "map", "admin", "timeline", "sources")
REQUIRED_SRC = ("geonames", "osm", "worldpop", "gdelt")


def check_one(path):
    code = os.path.basename(path)[len("region_"):-len(".json")]
    errs, warns = [], []

    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        return [f"{code}: 解析失败 {e}"], []

    def need(cond, msg):
        if not cond:
            errs.append(f"{code}: {msg}")

    # 1. 结构
    for k in REQUIRED_TOP:
        need(k in cfg, f"缺顶层键 {k!r}")
    if errs:
        return errs, warns

    # 2. pg
    pg = cfg.get("pg", {})
    for k in ("dbname", "schema"):
        v = pg.get(k, "")
        need(isinstance(v, str) and v.strip(), f"pg.{k} 非空字符串")
    if pg.get("schema") and not IDENT_RE.match(pg["schema"]):
        errs.append(f"{code}: pg.schema={pg['schema']!r} 不是合法 SQL 标识符（小写字母/数字/下划线）")

    # 3. map
    m = cfg.get("map", {})
    bbox = m.get("bbox")
    need(isinstance(bbox, list) and len(bbox) == 4, "map.bbox 须为 [minx, miny, maxx, maxy]")
    if isinstance(bbox, list) and len(bbox) == 4:
        minx, miny, maxx, maxy = bbox
        need(all(isinstance(v, (int, float)) for v in bbox), "map.bbox 须全为数字")
        if all(isinstance(v, (int, float)) for v in bbox):
            need(-180 <= minx < maxx <= 180, f"map.bbox 经度范围不合法 {bbox}")
            need(-90 <= miny < maxy <= 90, f"map.bbox 纬度范围不合法 {bbox}")
    center = m.get("center")
    need(isinstance(center, list) and len(center) == 2, "map.center 须为 [lon, lat]")
    if isinstance(center, list) and len(center) == 2 and isinstance(bbox, list) and len(bbox) == 4:
        lon, lat = center
        if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
            need(-180 <= lon <= 180 and -90 <= lat <= 90, f"map.center 越界 {center}")
            if not (bbox[0] - 0.5 <= lon <= bbox[2] + 0.5 and bbox[1] - 0.5 <= lat <= bbox[3] + 0.5):
                warns.append(f"{code}: map.center {center} 不在 bbox 内（±0.5° 容差）")
    z = m.get("zoom")
    need(isinstance(z, (int, float)) and 0 <= z <= 20, f"map.zoom={z!r} 须在 0–20")

    # 4. admin
    ad = cfg.get("admin", {})
    for k in ("level1", "level2"):
        need(isinstance(ad.get(k), str) and ad[k].strip(), f"admin.{k} 非空字符串")

    # 5. timeline
    tl = cfg.get("timeline", {})
    s, e = tl.get("start", ""), tl.get("end", "")
    need(isinstance(s, str) and MONTH_RE.match(s), f"timeline.start={s!r} 须为 YYYY-MM")
    need(isinstance(e, str) and MONTH_RE.match(e), f"timeline.end={e!r} 须为 YYYY-MM")
    if MONTH_RE.match(s or "") and MONTH_RE.match(e or ""):
        need(s <= e, f"timeline {s}…{e} 起点晚于终点")

    # 6. sources
    src = cfg.get("sources", {})
    for k in REQUIRED_SRC:
        need(k in src, f"sources 缺 {k!r}")
    if isinstance(src, dict):
        gn = src.get("geonames", {})
        need(isinstance(gn.get("country"), str) and len(gn["country"]) == 2,
             f"sources.geonames.country 须为两位 ISO 码，实际 {gn.get('country')!r}")
        osm = src.get("osm", {})
        need(isinstance(osm.get("relation"), int) and osm["relation"] > 0,
             f"sources.osm.relation 须为正整数，实际 {osm.get('relation')!r}")
        need(isinstance(osm.get("overpass_area_id"), int) and osm["overpass_area_id"] > 0,
             f"sources.osm.overpass_area_id 须为正整数，实际 {osm.get('overpass_area_id')!r}")
        wp = src.get("worldpop", {})
        need(isinstance(wp.get("iso3"), str) and len(wp["iso3"]) == 3,
             f"sources.worldpop.iso3 须为三位 ISO3 码，实际 {wp.get('iso3')!r}")
        need(isinstance(wp.get("year"), int) and 2000 <= wp["year"] <= 2030,
             f"sources.worldpop.year 异常 {wp.get('year')!r}")

    return errs, warns


def main():
    if len(sys.argv) > 1:
        paths = [os.path.join(CONFIG_DIR, f"region_{c.lower()}.json") for c in sys.argv[1:]]
    else:
        paths = sorted(os.path.join(CONFIG_DIR, p) for p in os.listdir(CONFIG_DIR)
                       if p.startswith("region_") and p.endswith(".json"))
    if not paths:
        print("未发现 config/region_*.json")
        return 1

    total_err = 0
    for p in paths:
        if not os.path.exists(p):
            print(f"MISSING {p}")
            total_err += 1
            continue
        errs, warns = check_one(p)
        tag = "FAIL" if errs else "PASS"
        print(f"[{tag}] {os.path.basename(p)}")
        for w in warns:
            print(f"   warn: {w}")
        for e in errs:
            print(f"   err : {e}")
        total_err += len(errs)
    print(f"\n{len(paths)} 份配置，{total_err} 个错误")
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main())
