# -*- coding: utf-8 -*-
"""import_fj.py — 福建（FJ）区域数据入库：GeoNames 地名 + DataV 边界 + 港电示范数据 + GDELT 事件。

区域模板化阶段二的 FJ 实跑脚本（配置驱动：读 config/region_fj.json，表名与 tw 同构，
故 export_web_data.py --region fj 与 rag_server.py 的 schema shim 可直接工作）。

用法：
  python pipeline\\import_fj.py --stage geonames --raw D:\\bw_fj_raw
  python pipeline\\import_fj.py --stage boundary  --raw D:\\bw_fj_raw
  python pipeline\\import_fj.py --stage demos      --raw D:\\bw_fj_raw
  python pipeline\\import_fj.py --stage events     --raw D:\\bw_fj_raw
  python pipeline\\import_fj.py --stage verify

数据源与合规：
  GeoNames CN dump（CC-BY 4.0，署名见清单 #12 口径）
  行政区划边界：阿里 DataV GeoAtlas（高德数据，仅研究演示用途，已登记清单 #24）
  港口/电厂：人工整理公开资料（示范数据，行级 source 标注）
  事件：GDELT DOC 2.0 文章采样（限非商业研究用途，同 TW 口径；地理名经 gaz_tw 匹配落图）
注意：人口表（pop_county/pop_town/cities_major）由 compute_fj_pop.py 用 WorldPop 另算。
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
import zipfile

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from region import load as load_region   # noqa: E402

import psycopg2                          # noqa: E402

PG = dict(host="127.0.0.1", port=5433, user="postgres")
CJK = re.compile(r"[一-鿿]")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def conn(dbname):
    c = psycopg2.connect(dbname=dbname, **PG)
    c.autocommit = True
    return c


def ensure_base(cur, S):
    """与 tw 同构的九张业务表（DDL 取自 tw 库 information_schema 实测）。"""
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {S}.gaz_tw (
        gid bigserial PRIMARY KEY, geonameid bigint UNIQUE,
        name text NOT NULL, asciiname text, alternatenames text,
        lat double precision, lon double precision, geom geometry(Point,4326),
        fclass char(1), fcode text, admin1 text, admin2 text,
        population bigint, elev_m integer, timezone text);
    CREATE TABLE IF NOT EXISTS {S}.gaz_alias (geonameid bigint, alt text, is_zh boolean);
    CREATE TABLE IF NOT EXISTS {S}.boundary (
        bid bigserial PRIMARY KEY, level int, name text, type_name text,
        geom geometry(MultiPolygon,4326), source text);
    CREATE TABLE IF NOT EXISTS {S}.pop_county (
        gid serial PRIMARY KEY, county_cn text, pop bigint, geom geometry(MultiPolygon,4326));
    CREATE TABLE IF NOT EXISTS {S}.pop_town (
        gid serial PRIMARY KEY, town text, pop numeric, geom geometry(MultiPolygon,4326));
    CREATE TABLE IF NOT EXISTS {S}.cities_major (
        gid serial PRIMARY KEY, name_zh text, population bigint, geom geometry(Point,4326));
    CREATE TABLE IF NOT EXISTS {S}.ports (
        gid bigserial PRIMARY KEY, osm_id bigint, name text, kind text, geom geometry(Point,4326));
    CREATE TABLE IF NOT EXISTS {S}.power_capacity (
        gid bigserial PRIMARY KEY, osm_id bigint, name text, fuel text, cap_mw double precision,
        gsrc text, psrc text, geom geometry(Point,4326), pkind text);
    CREATE TABLE IF NOT EXISTS {S}.events (
        gid bigserial PRIMARY KEY, sqldate date, actor1 text, actor2 text,
        rootcode text, root_name text, goldstein numeric, mentions int, articles int,
        tone numeric, geo_name text, geo_adm1 text, geom geometry(Point,4326), src text);
    CREATE INDEX IF NOT EXISTS gaz_geom_gix ON {S}.gaz_tw USING gist (geom);
    CREATE INDEX IF NOT EXISTS gaz_name_tgrm ON {S}.gaz_tw USING gin (name gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS gaz_alias_alt_ix ON {S}.gaz_alias (alt);
    CREATE INDEX IF NOT EXISTS bnd_geom_gix ON {S}.boundary USING gist (geom);
    CREATE INDEX IF NOT EXISTS ev_geom_gix ON {S}.events USING gist (geom);
    CREATE INDEX IF NOT EXISTS ev_date_ix ON {S}.events (sqldate);
    """)


def stage_geonames(cur, S, raw):
    zpath = os.path.join(raw, "CN.zip")
    rows, alias = [], []
    with zipfile.ZipFile(zpath) as z:
        with z.open("CN.txt") as f:
            for rawline in io.TextIOWrapper(f, encoding="utf-8"):
                p = rawline.rstrip("\n").split("\t")
                if len(p) < 19 or p[10] != "07":      # 07 = Fujian（admin1CodesASCII 已复核）
                    continue
                gid, name, asciiname, alts = p[0], p[1], p[2], p[3]
                lat, lon, fclass, fcode = p[4], p[5], p[6], p[7]
                admin1, admin2, pop, elev, tz = p[10], p[11], p[14], p[15], p[17]
                rows.append([gid, name, asciiname, alts, lat, lon, fclass, fcode,
                             admin1, admin2, int(pop) if pop else None,
                             int(elev) if elev else None, tz])
                for a in alts.split(","):
                    a = a.strip()
                    if a and CJK.search(a) and len(alias) < 400000:
                        alias.append([gid, a[:120], True])
    cur.execute(f"TRUNCATE {S}.gaz_tw, {S}.gaz_alias")
    cur.execute(f"ALTER SEQUENCE {S}.gaz_tw_gid_seq RESTART")
    buf = io.StringIO(); csv.writer(buf).writerows(rows); buf.seek(0)
    cur.copy_expert(f"""COPY {S}.gaz_tw (geonameid,name,asciiname,alternatenames,lat,lon,fclass,fcode,admin1,admin2,population,elev_m,timezone)
        FROM STDIN WITH (FORMAT csv)""", buf)
    cur.execute(f"UPDATE {S}.gaz_tw SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326) WHERE geom IS NULL")
    buf2 = io.StringIO(); csv.writer(buf2).writerows(alias); buf2.seek(0)
    cur.copy_expert(f"COPY {S}.gaz_alias (geonameid, alt, is_zh) FROM STDIN WITH (FORMAT csv)", buf2)
    print(f"gaz_tw {len(rows)} rows, gaz_alias {len(alias)}")
    cur.execute(f"SELECT fclass, count(*) FROM {S}.gaz_tw GROUP BY fclass ORDER BY 2 DESC LIMIT 6")
    print("fclass top:", cur.fetchall())
    cur.execute(f"SELECT count(*) FROM {S}.gaz_tw WHERE fclass='P' AND population >= 10000")
    print("P>=1万:", cur.fetchone()[0])


def stage_boundary(cur, S, raw):
    """DataV GeoAtlas：350000.json = 省要素；350000_full.json = 9 地级市；
    各地级市 <adcode>_full.json = 其下县区。聚合后写入三级 boundary。"""
    cur.execute(f"TRUNCATE {S}.boundary")
    cur.execute(f"ALTER SEQUENCE {S}.boundary_bid_seq RESTART")
    lv1 = json.load(open(os.path.join(raw, "fj_350000.json"), encoding="utf-8"))
    cities = json.load(open(os.path.join(raw, "fj_350000_full.json"), encoding="utf-8"))
    src = "DataV GeoAtlas(高德)"
    n = [0, 0, 0]

    def insert(level, name, tname, geom_geojson):
        g = json.dumps(geom_geojson, ensure_ascii=False)
        cur.execute(f"""INSERT INTO {S}.boundary (level, name, type_name, geom, source)
                        VALUES (%s,%s,%s, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(%s)), 3)), %s)""",
                    (level, name, tname, g, src))
        n[level] += 1

    # level 0：省界
    f0 = lv1["features"][0]
    insert(0, f0["properties"].get("name", "福建省"), "省", f0["geometry"])
    # level 1：9 地级市
    for ft in cities.get("features", []):
        p = ft["properties"]
        insert(1, p["name"], "地级市", ft["geometry"])
    # level 2：各县区（9 个地级市各自的 <adcode>_full.json）
    for ft in cities.get("features", []):
        adcode = ft["properties"]["adcode"]
        path = os.path.join(raw, f"fj_{adcode}_full.json")
        if not os.path.exists(path):
            print(f"  !! missing {path}")
            continue
        sub = json.load(open(path, encoding="utf-8"))
        for cft in sub.get("features", []):
            p = cft["properties"]
            insert(2, p["name"], "县/区", cft["geometry"])
    print("boundary:", n)
    cur.execute(f"""SELECT level, count(*), round((sum(ST_Area(geom::geography))/1e6)::numeric) AS km2
                    FROM {S}.boundary GROUP BY level ORDER BY level""")
    print("area:", cur.fetchall())


# ── 示范数据：福建主要港口 / 电厂（人工整理公开资料，行级 source 标注）──
PORTS = [
    # name, lon, lat, kind
    ("厦门港东渡港区", 118.080, 24.490, "海港"), ("厦门港海沧港区", 118.030, 24.450, "海港"),
    ("厦门港翔安港区", 118.260, 24.580, "海港"), ("福州港马尾港区", 119.450, 25.980, "河港"),
    ("福州港江阴港区", 119.670, 25.420, "海港"), ("福州港罗源湾港区", 119.790, 26.470, "海港"),
    ("泉州港后渚港区", 118.670, 24.880, "海港"), ("泉州港肖厝港区", 118.930, 24.870, "海港"),
    ("漳州港", 118.080, 24.410, "海港"), ("宁德港三都澳港区", 119.730, 26.650, "海港"),
    ("莆田港秀屿港区", 119.080, 25.180, "海港"), ("莆田港东吴港区", 119.050, 25.150, "海港"),
    ("平潭港金井港区", 119.780, 25.490, "海港"), ("南平港延平港区", 118.170, 26.640, "河港"),
    ("龙岩港永定港区", 116.730, 24.720, "河港"), ("三明港沙县港区", 117.790, 26.400, "河港"),
]
POWER = [
    # name, fuel, cap_mw, lon, lat, pkind
    ("宁德核电站", "核电", 4356, 120.190, 27.050, "在运"),
    ("福清核电站", "核电", 6678, 119.440, 25.440, "在运"),
    ("漳州核电站", "核电", 1200, 117.490, 23.930, "在建"),
    ("后石电厂", "煤电", 4200, 117.930, 24.400, "在运"),
    ("可门电厂", "煤电", 2400, 119.830, 26.330, "在运"),
    ("湄洲湾电厂", "煤电", 2800, 118.980, 25.050, "在运"),
    ("鸿山热电厂", "煤电", 1200, 118.650, 24.730, "在运"),
    ("晋江天然气电厂", "气电", 1400, 118.550, 24.780, "在运"),
    ("莆田LNG电厂", "气电", 1400, 119.030, 25.160, "在运"),
    ("邵武电厂", "煤电", 1200, 117.490, 27.340, "在运"),
    ("永安火电厂", "煤电", 600, 117.360, 25.970, "在运"),
    ("漳平火电厂", "煤电", 600, 117.420, 25.290, "在运"),
    ("华阳厦门电厂", "煤电", 660, 118.110, 24.500, "在运"),
    ("厦门垃圾焚烧发电厂", "垃圾焚烧", 72, 118.090, 24.540, "在运"),
    ("平潭海峡海上风电场", "风电", 300, 119.780, 25.500, "在运"),
    ("莆田平海湾海上风电场", "风电", 250, 119.250, 25.150, "在运"),
    ("漳州六鳌海上风电场", "风电", 200, 118.030, 23.930, "在运"),
    ("宁德霞浦海上风电场", "风电", 300, 120.200, 26.850, "在运"),
    ("古田溪水电站", "水电", 240, 118.750, 26.580, "在运"),
    ("池潭水电站", "水电", 100, 117.170, 26.980, "在运"),
    ("棉花滩水电站", "水电", 600, 116.650, 24.750, "在运"),
    ("街面水电站", "水电", 300, 118.280, 25.950, "在运"),
]


def stage_demos(cur, S, raw):
    cur.execute(f"TRUNCATE {S}.ports, {S}.power_capacity")
    cur.execute(f"ALTER SEQUENCE {S}.ports_gid_seq RESTART; ALTER SEQUENCE {S}.power_capacity_gid_seq RESTART")
    for name, lon, lat, kind in PORTS:
        cur.execute(f"""INSERT INTO {S}.ports (osm_id, name, kind, geom)
                        VALUES (0, %s, %s, ST_SetSRID(ST_MakePoint(%s,%s),4326))""",
                    (name, kind + "·人工整理公开资料", lon, lat))
    for name, fuel, mw, lon, lat, pkind in POWER:
        cur.execute(f"""INSERT INTO {S}.power_capacity (osm_id, name, fuel, cap_mw, gsrc, psrc, geom, pkind)
                        VALUES (0, %s, %s, %s, 'demo-curated', '公开资料约数', ST_SetSRID(ST_MakePoint(%s,%s),4326), %s)""",
                    (name, fuel, mw, lon, lat, pkind))
    print(f"ports {len(PORTS)}, power {len(POWER)}")


EVENT_KW = [  # (主题, 关键词列表)
    ("台风", ["台风", "typhoon"]),
    ("暴雨洪涝", ["暴雨", "洪涝", "rainstorm", "flood"]),
    ("地震", ["地震", "earthquake"]),
    ("交通事故", ["交通事故", "crash"]),
    ("经济发展", ["经济", "GDP", "investment", "trade"]),
    ("文旅活动", ["旅游", "文化", "tourism", "festival"]),
    ("体育赛事", ["体育", "marathon", "sports"]),
    ("教育科技", ["教育", "科技", "university", "tech"]),
    ("生态环境", ["生态", "环境", "pollution", "conservation"]),
    ("两岸交流", ["两岸", "cross-strait"]),
]
CITY_KW = [  # 地名匹配（GeoNames gaz 主名/常见变体）
    ("福州", ["福州", "Fuzhou"]), ("厦门", ["厦门", "Xiamen", "Amoy"]), ("泉州", ["泉州", "Quanzhou"]),
    ("漳州", ["漳州", "Zhangzhou"]), ("莆田", ["莆田", "Putian"]), ("宁德", ["宁德", "Ningde"]),
    ("南平", ["南平", "Nanping"]), ("三明", ["三明", "Sanming"]), ("龙岩", ["龙岩", "Longyan"]),
    ("平潭", ["平潭", "Pingtan"]), ("武夷山", ["武夷山", "Wuyishan"]), ("晋江", ["晋江", "Jinjiang"]),
    ("石狮", ["石狮", "Shishi"]), ("南安", ["南安", "Nan'an"]), ("福鼎", ["福鼎", "Fuding"]),
    ("霞浦", ["霞浦", "Xiapu"]), ("连江", ["连江", "Lianjiang"]), ("永泰", ["永泰", "Yongtai"]),
    ("德化", ["德化", "Dehua"]), ("长汀", ["长汀", "Changting"]),
]


def classify(title):
    for theme, kws in EVENT_KW:
        if any(k.lower() in title.lower() for k in kws):
            return theme
    return "综合"


def locate(title, cur, S):
    for city, kws in CITY_KW:
        hit = next((k for k in kws if k in title), None)
        if not hit:
            continue
        # GeoNames CN 主名为罗马字（Fuzhou），中文在 gaz_alias（福州）——双通道同查
        cur.execute(f"""SELECT g.lon, g.lat FROM {S}.gaz_tw g
                        LEFT JOIN {S}.gaz_alias a ON a.geonameid = g.geonameid AND a.alt = %s
                        WHERE g.fclass = 'P' AND (g.name = %s OR a.geonameid IS NOT NULL)
                        ORDER BY g.population DESC NULLS LAST LIMIT 1""", (hit, hit))
        r = cur.fetchone()
        if r:
            return city, r[0], r[1]
    return "福建", 118.1, 26.07   # 省会方位兜底点


def stage_events(cur, S, cfg):
    """GDELT DOC 2.0 按月采样（8s 节流；429 退避 50s×6）。失败月份记入缺窗清单，仅保留已采部分。"""
    import urllib.request
    start, end = cfg["timeline"]["start"], cfg["timeline"]["end"]
    cur.execute(f"TRUNCATE {S}.events")
    cur.execute(f"ALTER SEQUENCE {S}.events_gid_seq RESTART")
    q = "(fujian OR fuzhou OR xiamen OR quanzhou OR zhangzhou OR putian OR ningde OR nanping OR sanming OR longyan OR pingtan) sourcelang:chinese"
    y, m = MONTH_RE.match(start).groups()
    ey, em = MONTH_RE.match(end).groups()
    y, m, ey, em = int(y), int(m), int(ey), int(em)
    total = 0
    missing = []

    def fetch_month(yy, mm):
        if mm == 12:
            s, e = f"{yy}123100000000", f"{yy + 1}0101000000"
        else:
            s = f"{yy}{mm:02d}01000000"
            e = f"{yy}{mm + 1:02d}01000000"
        url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote(q)
               + f"&mode=ArtList&maxrecords=250&startdatetime={s}&enddatetime={e}&format=json&sort=datedesc")
        for attempt in range(6):
            try:
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "baodaowenyu-fj-template/1.0 academic research contact@example.com")
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                rows = []
                for a in data.get("articles", []):
                    t = (a.get("title") or "").strip()
                    d = (a.get("seendate") or "")[:8]
                    if not t or len(d) != 8:
                        continue
                    sqldate = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                    city, lon, lat = locate(t, cur, S)
                    rows.append((sqldate, "", "", classify(t)[:8], classify(t), None, 1, 1, None,
                                 city, "FJ", lon, lat, a.get("url", "")[:200]))
                return rows
            except Exception as ex:
                print(f"  {yy}-{mm:02d} retry#{attempt} {ex!r:.100}", flush=True)
                time.sleep(50 + attempt * 10)
        return None   # 6 次均失败：该月记缺窗

    yy, mm = y, m
    while (yy, mm) <= (ey, em):
        rows = fetch_month(yy, mm)
        if rows is None:
            missing.append((yy, mm))
            rows = []
        for r in rows:
            cur.execute(f"""INSERT INTO {S}.events (sqldate, actor1, actor2, rootcode, root_name, goldstein,
                            mentions, articles, tone, geo_name, geo_adm1, geom, src)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326), %s)""",
                        (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13]))
        total += len(rows)
        print(f"{yy}-{mm:02d}: {len(rows)} articles", flush=True)
        time.sleep(8)
        mm += 1
        if mm == 13:
            yy, mm = yy + 1, 1

    # 二轮补采：给 429 节流窗足够冷却时间后再试缺窗月份
    if missing:
        print(f"二轮补采 {len(missing)} 个缺窗: {missing}", flush=True)
        time.sleep(120)
    for yy, mm in missing:
        rows = fetch_month(yy, mm)
        if rows is None:
            print(f"{yy}-{mm:02d}: STILL MISSING", flush=True)
            continue
        for r in rows:
            cur.execute(f"""INSERT INTO {S}.events (sqldate, actor1, actor2, rootcode, root_name, goldstein,
                            mentions, articles, tone, geo_name, geo_adm1, geom, src)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326), %s)""",
                        (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13]))
        total += len(rows)
        print(f"{yy}-{mm:02d}: {len(rows)} articles (二轮)", flush=True)
        time.sleep(8)
    print(f"events total {total}")


def stage_verify(cur, S):
    for t in ("gaz_tw", "gaz_alias", "boundary", "pop_county", "pop_town",
              "cities_major", "ports", "power_capacity", "events"):
        cur.execute(f"SELECT count(*) FROM {S}.{t}")
        print(f"{t:16s} {cur.fetchone()[0]:>8}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", choices=["geonames", "boundary", "demos", "events", "verify", "all"])
    ap.add_argument("--region", default="fj")
    ap.add_argument("--raw", default=r"D:\bw_fj_raw")
    args = ap.parse_args()
    cfg = load_region(args.region)
    S = cfg["pg"]["schema"]
    db = cfg["pg"]["dbname"]
    c = conn(db)
    cur = c.cursor()
    ensure_base(cur, S)
    stages = ["geonames", "boundary", "demos", "events", "verify"] if args.stage == "all" else [args.stage, "verify"]
    import urllib.parse  # stage_events 需要
    for st in stages:
        print(f"── stage {st} ──", flush=True)
        {"geonames": lambda: stage_geonames(cur, S, args.raw),
         "boundary": lambda: stage_boundary(cur, S, args.raw),
         "demos": lambda: stage_demos(cur, S, args.raw),
         "events": lambda: stage_events(cur, S, cfg),
         "verify": lambda: stage_verify(cur, S)}[st]()
    c.close()
    print("IMPORT FJ DONE")


if __name__ == "__main__":
    main()
