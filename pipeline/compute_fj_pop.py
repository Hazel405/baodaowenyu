# -*- coding: utf-8 -*-
"""compute_fj_pop.py — GHSL R2023A GHS-POP E2020（30ss≈1km）分区统计 → fj.pop_county / fj.cities_major。

方法链（与 TW 的 WorldPop 分区统计同思路，数据源改 GHSL——本机网络无法访问 data.worldpop.org）：
  GHSL 瓦片(R7_C30/R7_C31) → FJ bbox 窗口镶嵌 → 县区/地级市多边形栅格化 → 分区求和。
口径：GHSL E2020 de facto 人口，许可 CC BY 4.0（署名 © European Commission, Joint Research Centre, GHSL）。
乡镇级（pop_town）：福建无公开 township 边界源，本版留空（0 行），export 照常导出空层。
"""
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from region import load as load_region   # noqa: E402

import numpy as np                       # noqa: E402
import psycopg2                          # noqa: E402
import rasterio                          # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from rasterio.merge import merge         # noqa: E402

PG = dict(host="127.0.0.1", port=5433, user="postgres")
RAW = r"D:\bw_fj_raw"


def read_window(tifs, bbox):
    """镶嵌两块瓦片并裁剪到 bbox（rasterio.merge 处理网格对齐）。"""
    datasets = [rasterio.open(t) for t in tifs]
    arr, transform = merge(datasets, bounds=bbox, nodata=0)
    for ds in datasets:
        ds.close()
    return arr[0].astype(np.float32), transform


def main():
    cfg = load_region("fj")
    S = cfg["pg"]["schema"]
    db = cfg["pg"]["dbname"]
    bbox = cfg["map"]["bbox"]
    tifs = sorted(glob.glob(os.path.join(RAW, "ghsl_R7_C3*/*.tif")))
    assert tifs, "未找到 GHSL R7 瓦片"
    arr, transform = read_window(tifs, bbox)
    print("window", arr.shape, "sum {:.0f}".format(float(arr.sum())))

    c = psycopg2.connect(dbname=db, **PG)
    c.autocommit = True
    cur = c.cursor()

    def zonal(level):
        cur.execute(f"""SELECT name, ST_AsGeoJSON(ST_CollectionExtract(ST_MakeValid(geom),3))
                        FROM {S}.boundary WHERE level=%s ORDER BY name""", (level,))
        rows = cur.fetchall()
        shapes = [(json.loads(g), i) for i, (nm, g) in enumerate(rows)]
        ids = rasterize(shapes, out_shape=arr.shape, transform=transform, fill=-1, dtype="int32")
        flat_i, flat_w = ids.ravel(), arr.ravel().astype(np.float64)
        keep = flat_i >= 0                       # 背景（-1）不得计入任何分区
        sums = np.bincount(flat_i[keep], weights=flat_w[keep], minlength=len(rows))
        return [(rows[i][0], int(round(sums[i]))) for i in range(len(rows))], rows

    cur.execute(f"TRUNCATE {S}.pop_county, {S}.cities_major")
    cur.execute(f"ALTER SEQUENCE {S}.pop_county_gid_seq RESTART; ALTER SEQUENCE {S}.cities_major_gid_seq RESTART")

    cnty, cnty_rows = zonal(2)
    for name, pop in cnty:
        cur.execute(f"""INSERT INTO {S}.pop_county (county_cn, pop, geom)
                        SELECT %s, %s, geom FROM {S}.boundary WHERE level=2 AND name=%s LIMIT 1""",
                    (name, pop, name))
    print(f"pop_county {len(cnty)} rows, top:",
          sorted(cnty, key=lambda x: -x[1])[:5])

    city, _ = zonal(1)
    for name, pop in city:
        cur.execute(f"""INSERT INTO {S}.cities_major (name_zh, population, geom)
                        SELECT %s, %s, ST_PointOnSurface(geom) FROM {S}.boundary WHERE level=1 AND name=%s LIMIT 1""",
                    (name, pop, name))
    print("cities_major:", [(n, f"{p/1e4:.0f}万") for n, p in sorted(city, key=lambda x: -x[1])])

    total = sum(p for _, p in city)
    print(f"FJ GHSL E2020 合计 ≈ {total/1e8:.2f} 千万（七普常住 4154 万对照，GHSL 为 de facto 口径）")
    cur.execute(f"SELECT count(*), sum(pop) FROM {S}.pop_county")
    print("pop_county 校验:", cur.fetchone())
    c.close()
    print("COMPUTE FJ POP DONE")


if __name__ == "__main__":
    main()
