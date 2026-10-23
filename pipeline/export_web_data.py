# -*- coding: utf-8 -*-
"""export_web_data.py — 导出 Web 查看端离线模式用的 GeoJSON 快照。

用法：python pipeline\\export_web_data.py
产物：web\\data\\*.geojson + web\\data\\manifest.json（记录条数/字节/生成时间）
设计：离线模式只需"能看懂的骨架"——县市人口面、界线、主要聚落、设施点与事件点；
      道路/水系由自渲街道瓦片承担视觉信息，不重复导出，避免包体膨胀。
      坐标精度 4 位小数（≈11 m），多边形按层级简化，逐层设要素上限。
白名单：只导出民用要素；osm_restricted 等非民用表一律不出现在此脚本与任何接口中。
"""
import json
import os
import time

import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 自动定位项目根（本文件位于 pipeline/ 下），避免换机后路径失效
OUT = os.path.join(ROOT, "web", "data")
PG = dict(host="127.0.0.1", port=5433, dbname="tw", user="postgres")

# (文件名, 中文标签, 几何类型, 源 SQL（须产出 geom 与 props 两列）)
LAYERS = [
    ("pop_county", "县市人口", "polygon", """
        SELECT ST_Simplify(geom, 0.0015) AS geom,
               jsonb_build_object('name', county_cn, 'pop', pop) AS props
        FROM tw.pop_county WHERE geom IS NOT NULL"""),
    ("county_line", "县市界", "line", """
        SELECT ST_Simplify(ST_Boundary(geom), 0.0012) AS geom,
               jsonb_build_object('name', name) AS props
        FROM tw.boundary WHERE level = 2"""),
    ("town", "乡镇界与人口", "polygon", """
        SELECT ST_Simplify(geom, 0.0008) AS geom,
               jsonb_build_object('name', town, 'pop', pop) AS props
        FROM tw.pop_town WHERE geom IS NOT NULL"""),
    ("cities", "主要城市", "point", """
        SELECT geom, jsonb_build_object('name', name_zh, 'pop', population, 'kind', 'city') AS props
        FROM tw.cities_major ORDER BY population DESC NULLS LAST LIMIT 40"""),
    ("places", "聚落点（万人以上）", "point", """
        SELECT geom, jsonb_build_object('name', name, 'pop', population, 'kind', fclass) AS props
        FROM tw.gaz_tw WHERE fclass = 'P' AND population >= 10000
        ORDER BY population DESC NULLS LAST LIMIT 1200"""),
    ("peaks", "山峰", "point", """
        SELECT geom, jsonb_build_object('name', name, 'elev', elev_m, 'kind', 'peak') AS props
        FROM tw.gaz_tw WHERE fclass = 'T' AND elev_m >= 1000
        ORDER BY elev_m DESC NULLS LAST LIMIT 400"""),
    ("ports", "港口", "point", """
        SELECT geom, jsonb_build_object('name', name, 'kind', kind) AS props
        FROM tw.ports ORDER BY name NULLS LAST LIMIT 800"""),
    ("power", "电厂", "point", """
        SELECT geom, jsonb_build_object('name', name, 'fuel', fuel, 'cap_mw', cap_mw) AS props
        FROM tw.power_capacity ORDER BY cap_mw DESC NULLS LAST LIMIT 520"""),
    # 事件点：按月各取提及数 Top250，覆盖 2024-09…2026-08（与网页时间轴口径一致）；
    # 2024-09 之前库内仅零散历史补录（单月个位数），不入快照
    ("events", "事件点", "point", """
        SELECT geom, jsonb_build_object('date', to_char(sqldate, 'YYYY-MM-DD'), 'theme', root_name,
                                        'mentions', mentions, 'place', geo_name) AS props
        FROM (SELECT e.*, row_number() OVER (PARTITION BY to_char(sqldate, 'YYYY-MM')
                                             ORDER BY mentions DESC NULLS LAST) AS rn
              FROM tw.events e WHERE sqldate >= '2024-09-01') t
        WHERE rn <= 250"""),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    c = psycopg2.connect(**PG)
    cur = c.cursor()
    manifest = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "离线快照：数据源见 data/DATA_SOURCES.md；实时模式请启服务走 /mvt 与 /api/ask",
                "crs": "EPSG:4326", "layers": {}}
    total = 0
    for fname, label, kind, sql in LAYERS:
        cur.execute(f"""SELECT jsonb_build_object('type','FeatureCollection','name','{fname}',
                             'features', COALESCE(jsonb_agg(jsonb_build_object('type','Feature',
                                  'geometry', ST_AsGeoJSON(geom, 4)::jsonb, 'properties', props)
                                  ORDER BY props->>'name' NULLS LAST), '[]'::jsonb))
                        FROM ({sql}) s""")
        gj = cur.fetchone()[0]
        feats = len(gj["features"]) if isinstance(gj, dict) else 0
        path = os.path.join(OUT, fname + ".geojson")
        with io_open(path, "w") as f:
            json.dump(gj, f, ensure_ascii=False, separators=(",", ":"))
        size = os.path.getsize(path)
        total += size
        manifest["layers"][fname] = {"label": label, "kind": kind, "count": feats, "bytes": size}
        print(f"{fname:14s} {feats:>6} feats  {size/1024:>8.1f} KB")
    manifest["total_bytes"] = total
    with io_open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    c.close()
    print(f"TOTAL {total/1024/1024:.2f} MB -> {OUT}")


def io_open(path, mode):
    # 显式 utf-8，避开 Windows 默认 GBK 写中文 GeoJSON 崩掉
    return open(path, "w", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
