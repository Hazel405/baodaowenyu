# -*- coding: utf-8 -*-
"""export_web_data.py — 导出 Web 查看端离线模式用的 GeoJSON 快照。

用法：python pipeline\\export_web_data.py [--region <code>|tw|…] [--out 目录]
产物：<out>\\*.geojson + <out>\\manifest.json（记录条数/字节/生成时间）
      + <out>\\region.json（网页查看端启动时读取：中心点/行政层级/时间轴口径）
      --out 缺省 web\\data；平移验证其他区域时建议另指目录（如 web\\data_<code>），
      避免覆盖交付主区域（tw）已发布的离线快照。
区域模板化：库名、模式前缀、事件时间轴范围全部来自 config/region_<code>.json；
      平移新区域只需新增一份配置并用 --region（或环境变量 BW_REGION）指定。
设计：离线模式只需"能看懂的骨架"——一级行政区人口面、界线、主要聚落、设施点与事件点；
      道路/水系由自渲街道瓦片承担视觉信息，不重复导出，避免包体膨胀。
      坐标精度 4 位小数（≈11 m），多边形按层级简化，逐层设要素上限。
白名单：只导出民用要素。《规范》禁止表示的受限要素在入库阶段即剔除（库内不设对应要素表）；
      核电站在本脚本按 fuel 与名称双重排除（《公开地图内容表示规范》第二十条）；
      事件地名字段统一输出规范中文行政区（中国台湾省××县市／福建省金门县、连江县（马祖）），
      任何残留英文国名／地区名的取值一律回落为「中国台湾省」，不出现在快照中。
"""
import argparse
import json
import os
import time

import psycopg2

from region import load as load_region, web_subset   # 同目录模块（脚本位于 pipeline/ 下）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 自动定位项目根（本文件位于 pipeline/ 下），避免换机后路径失效
OUT = os.path.join(ROOT, "web", "data")
PG_BASE = dict(host="127.0.0.1", port=5433, user="postgres")

REGION = None   # main() 里按 --region / BW_REGION 载入；SQL 在 layer_sqls() 内据此生成
S = None


def layer_sqls():
    """按区域配置生成各层导出 SQL（模式前缀与时间轴范围来自 config/region_<code>.json）。"""
    start, end = REGION["timeline"]["start"], REGION["timeline"]["end"]
    return [
    # 金门、连江（马祖）按《公开地图内容表示规范》与 GB/T 35764 附录 A.2 作福建省岛屿表示，注记加隶属括注
    ("pop_county", f"{REGION['admin']['level1']}人口", "polygon", f"""
        SELECT ST_Simplify(geom, 0.0015) AS geom,
               jsonb_build_object('name',
                   CASE WHEN county_cn ~ '金門|金门' THEN '金門縣（福建省）'
                        WHEN county_cn ~ '連江|连江' THEN '連江縣（馬祖，福建省）'
                        ELSE county_cn END,
                   'pop', pop) AS props
        FROM {S}.pop_county WHERE geom IS NOT NULL"""),
    ("county_line", f"{REGION['admin']['level1']}界", "line", f"""
        SELECT ST_Simplify(ST_Boundary(geom), 0.0012) AS geom,
               jsonb_build_object('name', name) AS props
        FROM {S}.boundary WHERE level = 2"""),
    ("town", f"{REGION['admin']['level2']}界与人口", "polygon", f"""
        SELECT ST_Simplify(geom, 0.0008) AS geom,
               jsonb_build_object('name', town, 'pop', pop) AS props
        FROM {S}.pop_town WHERE geom IS NOT NULL"""),
    # 台北按《公开地图内容表示规范》八（一）以省级行政中心标注
    ("cities", "主要城市", "point", f"""
        SELECT geom, jsonb_build_object('name',
                   CASE WHEN name_zh = '台北' THEN '台北（省级行政中心）' ELSE name_zh END,
                   'pop', population, 'kind', 'city') AS props
        FROM {S}.cities_major ORDER BY population DESC NULLS LAST LIMIT 40"""),
    ("places", "聚落点（万人以上）", "point", f"""
        SELECT geom, jsonb_build_object('name', name, 'pop', population, 'kind', fclass) AS props
        FROM {S}.gaz_tw WHERE fclass = 'P' AND population >= 10000
        ORDER BY population DESC NULLS LAST LIMIT 1200"""),
    ("peaks", "山峰", "point", f"""
        SELECT geom, jsonb_build_object('name', name, 'elev', elev_m, 'kind', 'peak') AS props
        FROM {S}.gaz_tw WHERE fclass = 'T' AND elev_m >= 1000
        ORDER BY elev_m DESC NULLS LAST LIMIT 400"""),
    ("ports", "港口", "point", f"""
        SELECT geom, jsonb_build_object('name', name, 'kind', kind) AS props
        FROM {S}.ports ORDER BY name NULLS LAST LIMIT 800"""),
    ("power", "电厂", "point", f"""
        SELECT geom, jsonb_build_object('name', name, 'fuel', fuel, 'cap_mw', cap_mw) AS props
        FROM {S}.power_capacity
        WHERE COALESCE(fuel, '') NOT IN ('nuclear', 'fission')
          AND (name IS NULL OR name !~* '核|nuclear')
        ORDER BY cap_mw DESC NULLS LAST LIMIT 520"""),
    # 事件点：按月各取提及数 Top250，覆盖与区域配置 timeline 一致（台湾库 2024-09 之前仅零散补录，不入快照）
    # place 字段：入库时已归一为规范中文行政区；此处再加一道守卫，任何含英文字母的残留取值回落「中国台湾省」
    ("events", "事件点", "point", f"""
        SELECT geom, jsonb_build_object('date', to_char(sqldate, 'YYYY-MM-DD'), 'theme', root_name,
                                        'mentions', mentions,
                                        'place', CASE WHEN geo_name ~ '[A-Za-z]' THEN '中国台湾省'
                                                      ELSE COALESCE(geo_name, '中国台湾省') END) AS props
        FROM (SELECT e.*, row_number() OVER (PARTITION BY to_char(sqldate, 'YYYY-MM')
                                             ORDER BY mentions DESC NULLS LAST) AS rn
              FROM {S}.events e
              WHERE sqldate >= '{start}-01'::date
                AND sqldate < ('{end}-01'::date + interval '1 month')) t
        WHERE rn <= 250"""),
    ]


def main():
    global REGION, S
    ap = argparse.ArgumentParser(description="导出 Web 查看端离线快照")
    ap.add_argument("--region", help="区域代码，对应 config/region_<code>.json（缺省取 BW_REGION 环境变量，再缺省 tw）")
    ap.add_argument("--out", default=OUT, help=f"快照输出目录（缺省 {OUT}；验证非主区域请另指目录）")
    args = ap.parse_args()
    REGION = load_region(args.region)
    S = REGION["pg"]["schema"]
    PG = dict(PG_BASE, dbname=REGION["pg"]["dbname"])
    print(f"region={REGION['code']}  db={PG['dbname']}  schema={S}  timeline={REGION['timeline']['start']}…{REGION['timeline']['end']}")

    os.makedirs(args.out, exist_ok=True)
    c = psycopg2.connect(**PG)
    cur = c.cursor()
    manifest = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "离线快照：数据源见 data/DATA_SOURCES.md；实时模式请启服务走 /mvt 与 /api/ask",
                "region": REGION["code"],
                "crs": "EPSG:4326", "layers": {}}
    total = 0
    for fname, label, kind, sql in layer_sqls():
        cur.execute(f"""SELECT jsonb_build_object('type','FeatureCollection','name','{fname}',
                             'features', COALESCE(jsonb_agg(jsonb_build_object('type','Feature',
                                  'geometry', ST_AsGeoJSON(geom, 4)::jsonb, 'properties', props)
                                  ORDER BY props->>'name' NULLS LAST), '[]'::jsonb))
                        FROM ({sql}) s""")
        gj = cur.fetchone()[0]
        feats = len(gj["features"]) if isinstance(gj, dict) else 0
        path = os.path.join(args.out, fname + ".geojson")
        with io_open(path, "w") as f:
            json.dump(gj, f, ensure_ascii=False, separators=(",", ":"))
        size = os.path.getsize(path)
        total += size
        manifest["layers"][fname] = {"label": label, "kind": kind, "count": feats, "bytes": size}
        print(f"{fname:14s} {feats:>6} feats  {size/1024:>8.1f} KB")
    manifest["total_bytes"] = total
    with io_open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    # 区域配置的网页子集：查看端启动时读取（中心点/行政层级标签/时间轴口径），失败则退回台湾缺省
    with io_open(os.path.join(args.out, "region.json"), "w") as f:
        json.dump(web_subset(REGION), f, ensure_ascii=False, indent=1)
    c.close()
    print(f"TOTAL {total/1024/1024:.2f} MB -> {args.out}")


def io_open(path, mode):
    # 显式 utf-8，避开 Windows 默认 GBK 写中文 GeoJSON 崩掉
    return open(path, "w", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
