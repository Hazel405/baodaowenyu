# -*- coding: utf-8 -*-
"""阶段1：建 tw 库 + gaz_tw/gaz_alias（GeoNames）+ boundary（GADM 0/1/2）一体化导入。
自包含单调用：起库 → 导入 → 校验 → 停库。"""
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile

sys.stdout.reconfigure(encoding="utf-8")
RAW = r"H:\taiwan\raw"
GEOLIB = r"H:\tools\conda_env_geo\Library\bin"
PGCTL = r"H:\tools\conda_env_pg\Library\bin\pg_ctl.exe"
PGDATA = r"H:\tools\pgdata_htm2026"
PGLOG = r"H:\tools\pg_start.log"
PSQL_ENV = dict(os.environ, PGCLIENTENCODING="UTF8")
PGCONN = dict(host="127.0.0.1", port=5433, dbname="tw", user="postgres")


def start_pg():
    if os.path.exists(os.path.join(PGDATA, "postmaster.pid")):
        os.remove(os.path.join(PGDATA, "postmaster.pid"))
    with open("NUL", "w") as nul:
        subprocess.run([PGCTL, "-D", PGDATA, "-o", "-p 5433", "-l", PGLOG, "start"],
                       stdout=nul, stderr=nul, timeout=60)
    import socket
    for _ in range(30):
        try:
            s = socket.create_connection(("127.0.0.1", 5433), 1)
            s.close()
            return True
        except OSError:
            time.sleep(1)
    return False


def stop_pg():
    with open("NUL", "w") as nul:
        subprocess.run([PGCTL, "-D", PGDATA, "stop", "-m", "fast"], stdout=nul, stderr=nul, timeout=60)


CJK = re.compile(r"[\u4e00-\u9fff]")


def main():
    assert start_pg(), "PG start failed"
    print("PG up")
    import psycopg2

    # ── 建库 ──
    c0 = psycopg2.connect(host="127.0.0.1", port=5433, dbname="postgres", user="postgres")
    c0.autocommit = True
    cur = c0.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname='tw'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE tw ENCODING 'UTF8' TEMPLATE template0")
    c0.close()

    c = psycopg2.connect(**PGCONN)
    c.autocommit = True
    cur = c.cursor()
    for ext in ["postgis", "pg_trgm"]:
        cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
    cur.execute("""
    CREATE SCHEMA IF NOT EXISTS tw;
    DROP TABLE IF EXISTS tw.gaz_tw, tw.gaz_alias, tw.boundary CASCADE;
    CREATE TABLE tw.gaz_tw (
        gid bigserial PRIMARY KEY, geonameid bigint UNIQUE,
        name text NOT NULL, asciiname text, alternatenames text,
        lat double precision, lon double precision, geom geometry(Point,4326),
        fclass char(1), fcode text, admin1 text, admin2 text,
        population bigint, elev_m integer, timezone text);
    CREATE TABLE tw.gaz_alias (
        geonameid bigint, alt text, is_zh boolean);
    CREATE TABLE tw.boundary (
        bid bigserial PRIMARY KEY, level int, name text, type_name text,
        geom geometry(MultiPolygon,4326), source text);
    """)
    cur.execute("CREATE INDEX gaz_geom_gix ON tw.gaz_tw USING gist (geom)")
    cur.execute("CREATE INDEX gaz_name_tgrm ON tw.gaz_tw USING gin (name gin_trgm_ops)")
    cur.execute("CREATE INDEX gaz_fcode_ix ON tw.gaz_tw (fcode)")
    cur.execute("CREATE INDEX bnd_geom_gix ON tw.boundary USING gist (geom)")
    print("schema ok")

    # ── GeoNames COPY ──
    rows, alias = [], []
    with open(os.path.join(RAW, "TW.txt"), encoding="utf8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 15:
                continue
            gid, name, asciiname, alts, lat, lon, fclass, fcode = p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]
            admin1, admin2, pop, elev, tz = p[10], p[11], p[14], p[15], p[17]
            rows.append([gid, name, asciiname, alts, lat, lon, fclass, fcode, admin1, admin2,
                         int(pop) if pop else None, int(elev) if elev else None, tz])
            for a in alts.split(","):
                a = a.strip()
                if a and CJK.search(a) and len(alias) < 400000:
                    alias.append([gid, a[:120], True])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    buf.seek(0)
    cur.copy_expert("""COPY tw.gaz_tw (geonameid,name,asciiname,alternatenames,lat,lon,fclass,fcode,admin1,admin2,population,elev_m,timezone)
        FROM STDIN WITH (FORMAT csv)""", buf)
    cur.execute("UPDATE tw.gaz_tw SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326) WHERE geom IS NULL")
    buf2 = io.StringIO()
    w2 = csv.writer(buf2)
    w2.writerows(alias)
    buf2.seek(0)
    cur.copy_expert("COPY tw.gaz_alias (geonameid, alt, is_zh) FROM STDIN WITH (FORMAT csv)", buf2)
    print(f"gaz_tw {len(rows)} rows, gaz_alias {len(alias)} zh-aliases")

    # ── GADM 0/1/2 via ogr2ogr ──
    env = dict(os.environ)
    env["PGCLIENTENCODING"] = "UTF8"
    conn = "PG:host=127.0.0.1 port=5433 dbname=tw user=postgres active_schema=tw"
    for lv, fn in [(0, "gadm41_TWN_0.json"), (1, "gadm41_TWN_1.json"), (2, "gadm41_TWN_2.json")]:
        r = subprocess.run([os.path.join(GEOLIB, "ogr2ogr.exe"), "-f", "PostgreSQL", conn,
                            "-nln", f"tw.gadm{lv}_raw", "-lco", "GEOMETRY_NAME=geom",
                            os.path.join(RAW, fn)], capture_output=True, text=True, env=env, cwd=RAW)
        if r.returncode != 0:
            print("ogr2ogr err", lv, (r.stderr or "")[:300])
    cur.execute("""
    INSERT INTO tw.boundary (level, name, type_name, geom, source)
    SELECT 0, NAME_0, 'country', ST_Multi(ST_SetSRID(geom, 4326)), 'GADM4.1' FROM tw.gadm0_raw
    UNION ALL
    SELECT 1, NAME_1, COALESCE(TYPE_1,''), ST_Multi(ST_SetSRID(geom, 4326)), 'GADM4.1' FROM tw.gadm1_raw
    UNION ALL
    SELECT 2, NAME_2, COALESCE(TYPE_2,''), ST_Multi(ST_SetSRID(geom, 4326)), 'GADM4.1' FROM tw.gadm2_raw
    """)
    cur.execute("UPDATE tw.boundary SET geom = ST_MakeValid(geom)")
    cur.execute("DROP TABLE IF EXISTS tw.gadm0_raw, tw.gadm1_raw, tw.gadm2_raw CASCADE")
    cur.execute("SELECT level, count(*) FROM tw.boundary GROUP BY level ORDER BY level")
    print("boundary:", cur.fetchall())

    # ── 校验 ──
    cur.execute("SELECT fclass, count(*) FROM tw.gaz_tw GROUP BY fclass ORDER BY 2 DESC LIMIT 6")
    print("fclass top:", cur.fetchall())
    cur.execute("SELECT count(*) FROM tw.gaz_tw WHERE name ILIKE '%台北%' OR name = 'Taipei'")
    print("Taipei rows:", cur.fetchone()[0])
    # 空间连接测试：台北市边界内的地名点数
    cur.execute("""
    SELECT count(*) FROM tw.gaz_tw g JOIN tw.boundary b
      ON b.level = 1 AND b.name = 'Taipei' AND ST_Intersects(g.geom, b.geom)""")
    print("points in Taipei City:", cur.fetchone()[0])
    cur.execute("SELECT name, fcode, population FROM tw.gaz_tw ORDER BY population DESC NULLS LAST LIMIT 5")
    for r in cur.fetchall():
        print("  top:", r)

    c.close()
    stop_pg()
    print("PHASE1 DONE")


main()
