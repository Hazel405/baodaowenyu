# -*- coding: utf-8 -*-
"""P0a 入库：derived/dem_stats.json → tw.dem_zonal_stats；derived/contours_1000.geojson → tw.contours
幂等 DROP/CREATE + COPY 批量；系统 Python + psycopg2；PG 保持运行（不 stop）
运行  python H:/taiwan/load_p0a.py
"""
import csv
import io
import json
import os
import socket
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
DER = r"H:\taiwan\derived"
PGCTL = r"H:\tools\conda_env_pg\Library\bin\pg_ctl.exe"
PGDATA = r"H:\tools\pgdata_htm2026"
DEM_VER = "p0a-20260930-patched"


def boot_pg():
    if not os.path.exists(os.path.join(PGDATA, "postmaster.pid")):
        with open("NUL", "w") as nul:
            subprocess.run([PGCTL, "-D", PGDATA, "-o", "-p 5433", "-l", r"H:\tools\pg_start.log", "start"],
                           stdout=nul, stderr=nul, timeout=90)
    for _ in range(30):
        try:
            s = socket.create_connection(("127.0.0.1", 5433), 1)
            s.close()
            return
        except OSError:
            time.sleep(1)
    raise SystemExit("PG 未就绪（5433）")


def main():
    boot_pg()
    import psycopg2
    c = psycopg2.connect(host="127.0.0.1", port=5433, dbname="tw", user="postgres")
    cur = c.cursor()

    # ---------- 1. 分区统计（县 22 + 乡镇 204，列对齐 dem_stats.json 字段） ----------
    sp = os.path.join(DER, "dem_stats.json")
    rows = json.load(open(sp, encoding="utf8"))
    cur.execute("DROP TABLE IF EXISTS tw.dem_zonal_stats")
    cur.execute("""CREATE TABLE tw.dem_zonal_stats (
        unit_type text, unit_id text, unit_name text, parent text,
        area_km2 double precision, elev_mean double precision, elev_max double precision,
        slope_mean double precision, slope_p95 double precision, steep30_frac double precision,
        mobility_mean double precision, stream_l2_frac double precision, acc_max bigint,
        dem_ver text)""")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow([r["unit_type"], r["unit_id"], r["unit_name"], r.get("parent") or None,
                    r["area_km2"], r["elev_mean"], r["elev_max"], r["slope_mean"], r["slope_p95"],
                    r["steep30_frac"], r["mobility_mean"], r["stream_l2_frac"], r["acc_max"], DEM_VER])
    buf.seek(0)
    cur.execute("""CREATE TEMP TABLE stg_ds (unit_type text, unit_id text, unit_name text, parent text,
                    area_km2 double precision, elev_mean double precision, elev_max double precision,
                    slope_mean double precision, slope_p95 double precision, steep30_frac double precision,
                    mobility_mean double precision, stream_l2_frac double precision, acc_max bigint, dem_ver text)""")
    cur.copy_expert("COPY stg_ds FROM STDIN WITH (FORMAT csv)", buf)
    cur.execute("INSERT INTO tw.dem_zonal_stats SELECT * FROM stg_ds")
    c.commit()
    print(f"dem_zonal_stats {cur.rowcount} rows", flush=True)
    for q in ("SELECT unit_name, area_km2, elev_mean, elev_max, slope_mean, steep30_frac FROM tw.dem_zonal_stats "
              "WHERE unit_type='county' ORDER BY elev_max DESC LIMIT 4",
              "SELECT parent, unit_name, area_km2, mobility_mean FROM tw.dem_zonal_stats WHERE unit_type='town' "
              "ORDER BY mobility_mean DESC LIMIT 4"):
        cur.execute(q)
        print("sample:", cur.fetchall(), flush=True)

    # ---------- 2. 1000m 级等高线（索引线） ----------
    fc = json.load(open(os.path.join(DER, "contours_1000.geojson"), encoding="utf8"))
    feats = fc["features"]
    print(f"contours_1000 features {len(feats)}", flush=True)
    cur.execute("DROP TABLE IF EXISTS tw.contours")
    cur.execute("CREATE TABLE tw.contours (gid bigserial PRIMARY KEY, level int, geom geometry(LineString,4326))")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    n0 = 0
    for f in feats:
        cc = f["geometry"]["coordinates"]
        if len(cc) < 3:
            continue
        w.writerow([f["properties"].get("elev"),
                    "LINESTRING(" + ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in cc) + ")"])
        n0 += 1
    buf.seek(0)
    cur.execute("CREATE TEMP TABLE stg_ct (level int, wkt text)")
    cur.copy_expert("COPY stg_ct FROM STDIN WITH (FORMAT csv)", buf, size=64 * 1024 * 1024)
    cur.execute("INSERT INTO tw.contours (level, geom) SELECT level, ST_GeomFromText(wkt,4326) FROM stg_ct "
                "WHERE ST_IsValid(ST_GeomFromText(wkt,4326))")
    c.commit()
    cur.execute("CREATE INDEX i_contours_geom ON tw.contours USING GIST(geom)")
    cur.execute("CREATE INDEX i_contours_level ON tw.contours (level)")
    c.commit()
    print(f"contours {cur.rowcount} rows (from {n0} feats)", flush=True)
    cur.execute("SELECT level, count(*), round((sum(ST_Length(geom::geography))/1000.0)::numeric, 1) AS km "
                "FROM tw.contours GROUP BY level ORDER BY level")
    print("contours by level:", cur.fetchall(), flush=True)

    c.close()
    print("LOAD P0a DONE（PG 保持运行）", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"total {time.time()-t0:.0f}s", flush=True)
