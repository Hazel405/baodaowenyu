# -*- coding: utf-8 -*-
"""阶段3导入（快速版）：staging COPY + 集合式 INSERT，单事务，幂等。"""
import csv
import io
import json
import os
import socket
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
RAW = r"H:\taiwan\raw\overpass"
PGCTL = r"H:\tools\conda_env_pg\Library\bin\pg_ctl.exe"
PGDATA = r"H:\tools\pgdata_htm2026"


def load(name):
    p = os.path.join(RAW, name + ".json")
    if not os.path.exists(p) or os.path.getsize(p) < 500:
        return None
    return json.load(open(p, encoding="utf8"))


def way_coords(el):
    return [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]


def el_name(el):
    t = el.get("tags", {})
    return (t.get("name") or t.get("name:en") or t.get("name:zh") or "")[:200]


def _key(p):
    return (round(p[0], 6), round(p[1], 6))


def assemble_ring(coords_list):
    segs = [list(c) for c in coords_list if len(c) >= 2]
    rings = []
    while segs:
        cur = segs.pop(0)
        closed = _key(cur[0]) == _key(cur[-1])
        guard = 0
        while not closed and guard < 800:
            guard += 1
            k = _key(cur[-1])
            for i, s in enumerate(segs):
                if _key(s[0]) == k:
                    cur = cur + s[1:]
                    segs.pop(i)
                    break
                if _key(s[-1]) == k:
                    cur = cur + s[::-1][1:]
                    segs.pop(i)
                    break
            else:
                break
            closed = _key(cur[0]) == _key(cur[-1])
        if closed and len(cur) >= 4:
            rings.append(cur)
    return rings


def ls_wkt(cc):
    return "LINESTRING(" + ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in cc) + ")"


def mp_wkt(rings):
    return "MULTIPOLYGON(" + ",".join("(" + "(" + ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in (r + [r[0]])) + ")" + ")" for r in rings) + ")"


def main():
    if not os.path.exists(os.path.join(PGDATA, "postmaster.pid")):
        with open("NUL", "w") as nul:
            subprocess.run([PGCTL, "-D", PGDATA, "-o", "-p 5433", "-l", r"H:\tools\pg_start.log", "start"],
                           stdout=nul, stderr=nul, timeout=60)
        for _ in range(30):
            try:
                s = socket.create_connection(("127.0.0.1", 5433), 1)
                s.close()
                break
            except OSError:
                time.sleep(1)
    import psycopg2
    c = psycopg2.connect(host="127.0.0.1", port=5433, dbname="tw", user="postgres")
    cur = c.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tw.roads (gid bigserial PRIMARY KEY, osm_id bigint, class text, name text, geom geometry(LineString,4326));
    CREATE TABLE IF NOT EXISTS tw.rail (gid bigserial PRIMARY KEY, osm_id bigint, kind text, name text, geom geometry(LineString,4326));
    CREATE TABLE IF NOT EXISTS tw.airports (gid bigserial PRIMARY KEY, osm_id bigint, name text, geom geometry(Point,4326));
    CREATE TABLE IF NOT EXISTS tw.ports (gid bigserial PRIMARY KEY, osm_id bigint, name text, kind text, geom geometry(Point,4326));
    CREATE TABLE IF NOT EXISTS tw.power (gid bigserial PRIMARY KEY, osm_id bigint, kind text, name text, geom geometry(Point,4326));
    CREATE TABLE IF NOT EXISTS tw.coastline (gid bigserial PRIMARY KEY, osm_id bigint, geom geometry(LineString,4326));
    CREATE TABLE IF NOT EXISTS tw.admin_osm (gid bigserial PRIMARY KEY, level int, osm_rel bigint, name text, geom geometry(MultiPolygon,4326));
    TRUNCATE tw.roads, tw.rail, tw.airports, tw.ports, tw.power, tw.coastline, tw.admin_osm;
    """)
    c.commit()

    def bulk_lines(src, table, cls_key, cls_col):
        data = load(src)
        if not data:
            print(src, "skip", flush=True)
            return
        rows = []
        for el in data.get("elements", []):
            if el.get("type") != "way":
                continue
            cc = way_coords(el)
            if len(cc) < 2:
                continue
            t = el.get("tags", {})
            rows.append((el["id"], t.get(cls_key, "") if cls_key else "", el_name(el), ls_wkt(cc)))
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3]])
        buf.seek(0)
        cur.execute(f"CREATE TEMP TABLE stg_{table} (osm_id bigint, cls text, nm text, wkt text)")
        cur.copy_expert(f"COPY stg_{table} FROM STDIN WITH (FORMAT csv)", buf, size=64 * 1024 * 1024)
        if cls_col:
            cur.execute(f"""INSERT INTO tw.{table} (osm_id, {cls_col}, name, geom)
                SELECT osm_id, cls, NULLIF(nm,''), ST_GeomFromText(wkt, 4326) FROM stg_{table}""")
        elif table == "coastline":
            cur.execute(f"""INSERT INTO tw.coastline (osm_id, geom)
                SELECT osm_id, ST_GeomFromText(wkt, 4326) FROM stg_{table}""")
        else:
            cur.execute(f"""INSERT INTO tw.{table} (osm_id, name, geom)
                SELECT osm_id, NULLIF(nm,''), ST_GeomFromText(wkt, 4326) FROM stg_{table}""")
        cur.execute(f"DROP TABLE stg_{table}")
        c.commit()
        print(table, len(rows), "rows", flush=True)

    bulk_lines("roads_major", "roads", "highway", "class")
    bulk_lines("rail", "rail", "railway", "kind")
    bulk_lines("coastline", "coastline", None, None)

    def bulk_points(src, table, kind_fn):
        data = load(src)
        if not data:
            print(src, "skip", flush=True)
            return
        rows = []
        for el in data.get("elements", []):
            t = el.get("tags", {})
            if el["type"] == "node":
                pt = (el["lon"], el["lat"])
            elif el["type"] == "way":
                cc = way_coords(el)
                if not cc:
                    continue
                pt = (sum(x for x, _ in cc) / len(cc), sum(y for _, y in cc) / len(cc))
            else:
                continue
            rows.append((el["id"], el_name(el), kind_fn(t), pt[0], pt[1]))
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for r in rows:
            w.writerow(r)
        buf.seek(0)
        cur.execute(f"CREATE TEMP TABLE stg_{table} (osm_id bigint, nm text, kind text, lon double precision, lat double precision)")
        cur.copy_expert(f"COPY stg_{table} FROM STDIN WITH (FORMAT csv)", buf)
        if table == "airports":
            cur.execute(f"""INSERT INTO tw.airports (osm_id, name, geom)
                SELECT osm_id, NULLIF(nm,''), ST_SetSRID(ST_MakePoint(lon, lat), 4326) FROM stg_{table}""")
        else:
            cur.execute(f"""INSERT INTO tw.{table} (osm_id, name, kind, geom)
                SELECT osm_id, NULLIF(nm,''), kind, ST_SetSRID(ST_MakePoint(lon, lat), 4326) FROM stg_{table}""")
        cur.execute(f"DROP TABLE stg_{table}")
        c.commit()
        print(table, len(rows), "rows", flush=True)

    bulk_points("airports", "airports", lambda t: t.get("aeroway", "aerodrome"))
    bulk_points("ports", "ports", lambda t: t.get("harbour") or t.get("industrial") or t.get("man_made") or "")
    bulk_points("power", "power", lambda t: t.get("power", ""))

    # admin 环组装（行少，逐条）
    for src, lv in [("admin6", 6), ("admin8", 8)]:
        data = load(src)
        if not data:
            print(src, "skip", flush=True)
            continue
        rows = []
        for el in data.get("elements", []):
            if el.get("type") != "relation":
                continue
            t = el.get("tags", {})
            name = (t.get("name") or t.get("name:en") or "")[:100]
            outers, holes = [], []
            for m in el.get("members", []):
                if m.get("type") != "way":
                    continue
                cc = [(g["lon"], g["lat"]) for g in m.get("geometry", []) if g]
                if len(cc) >= 2:
                    (holes if m.get("role") == "inner" else outers).append(cc)
            orings = assemble_ring(outers)
            hrings = assemble_ring(holes)
            if not orings:
                continue
            allr = orings + hrings
            wkt = "MULTIPOLYGON(" + ",".join("(" + "(" + ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in (r + [r[0]])) + ")" + ")" for r in allr) + ")"
            rows.append((lv, el["id"], name, wkt))
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3]])
        buf.seek(0)
        cur.execute("CREATE TEMP TABLE stg_adm (lv int, oid bigint, nm text, wkt text)")
        cur.copy_expert("COPY stg_adm FROM STDIN WITH (FORMAT csv)", buf, size=64 * 1024 * 1024)
        cur.execute("""INSERT INTO tw.admin_osm (level, osm_rel, name, geom)
            SELECT lv, oid, NULLIF(nm,''), ST_Multi(ST_MakeValid(ST_GeomFromText(wkt, 4326))) FROM stg_adm""")
        cur.execute("DROP TABLE stg_adm")
        c.commit()
        print(src, len(rows), "relations", flush=True)

    cur.execute("SELECT 'roads',count(*) FROM tw.roads UNION ALL SELECT 'rail',count(*) FROM tw.rail "
                "UNION ALL SELECT 'airports',count(*) FROM tw.airports UNION ALL SELECT 'ports',count(*) FROM tw.ports "
                "UNION ALL SELECT 'power',count(*) FROM tw.power UNION ALL SELECT 'coastline',count(*) FROM tw.coastline "
                "UNION ALL SELECT 'admin6/8',count(*) FROM tw.admin_osm")
    print("counts:", cur.fetchall(), flush=True)
    c.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    with open("NUL", "w") as nul:
        subprocess.run([PGCTL, "-D", PGDATA, "stop", "-m", "fast"], stdout=nul, stderr=nul, timeout=60)
    print(f"PHASE3 FAST-IMPORT DONE in {time.time()-t0:.0f}s")
