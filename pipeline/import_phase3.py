# -*- coding: utf-8 -*-
"""阶段3导入：overpass/*.json → tw 库表（roads/rail/airports/ports/power/coastline/admin_osm）。
自包含：起库→导入→校验→停库。admin 关系做环组装。"""
import json
import os
import socket
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
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
    return t.get("name") or t.get("name:en") or t.get("name:zh") or ""


def center(coords):
    if not coords:
        return None
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return lon, lat


def assemble_ring(coords_list):
    """把多段折线按端点拼接成闭合环列表。"""
    segs = [list(c) for c in coords_list if len(c) >= 2]
    rings = []
    while segs:
        cur = segs.pop(0)
        closed = _key(cur[0]) == _key(cur[-1])
        guard = 0
        while not closed and guard < 500:
            guard += 1
            k = _key(cur[-1])
            for i, s in enumerate(segs):
                if _key(s[0]) == k:
                    cur = cur + s[1:]
                    segs.pop(i)
                    closed = _key(cur[0]) == _key(cur[-1])
                    break
                if _key(s[-1]) == k:
                    cur = cur + s[::-1][1:]
                    segs.pop(i)
                    closed = _key(cur[0]) == _key(cur[-1])
                    break
            else:
                break
        if closed and len(cur) >= 4:
            rings.append(cur)
    return rings


def _key(p):
    return (round(p[0], 6), round(p[1], 6))


def ring_wkt(ring):
    pts = ring + [ring[0]]
    return "(" + ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in pts) + ")"


def poly_wkt(outers, holes):
    if not outers:
        return None
    all_rings = outers + holes
    return "MULTIPOLYGON(" + ",".join("(" + ring_wkt(r) + ")" for r in all_rings) + ")"


def admin_relation(rel, level):
    """relation(out geom) → (name, multipolygon wkt)"""
    tags = rel.get("tags", {})
    name = tags.get("name") or tags.get("name:en") or ""
    outers, holes = [], []
    for m in rel.get("members", []):
        if m.get("type") != "way":
            continue
        c = [(g["lon"], g["lat"]) for g in m.get("geometry", []) if g]
        if len(c) < 2:
            continue
        (outers if m.get("role") != "inner" else holes).append(c)
    o_rings = assemble_ring(outers)
    h_rings = assemble_ring(holes)
    wkt = poly_wkt(o_rings, h_rings)
    return name, wkt


def import_all():
    import psycopg2
    c = psycopg2.connect(host="127.0.0.1", port=5433, dbname="tw", user="postgres")
    c.autocommit = True
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
    CREATE INDEX IF NOT EXISTS roads_gix ON tw.roads USING gist(geom);
    CREATE INDEX IF NOT EXISTS roads_cls ON tw.roads (class);
    CREATE INDEX IF NOT EXISTS admin_osm_gix ON tw.admin_osm USING gist(geom);
    CREATE INDEX IF NOT EXISTS admin_osm_lv ON tw.admin_osm (level);
    """)
    n = {}

    # roads / rail / coastline（线）
    def lines(src, table, cls_key, classes=None):
        data = load(src)
        if not data:
            print(src, "skip (no data)")
            return
        rows = []
        for el in data.get("elements", []):
            if el.get("type") != "way":
                continue
            cc = way_coords(el)
            if len(cc) < 2:
                continue
            t = el.get("tags", {})
            cls = t.get(cls_key, "")
            if classes and cls not in classes:
                continue
            wkt = "LINESTRING(" + ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in cc) + ")"
            rows.append((el["id"], cls, t.get("name") or "", wkt))
        for osm_id, cls, nm, wkt in rows:
            if table == "roads":
                cur.execute("INSERT INTO tw.roads (osm_id,class,name,geom) VALUES (%s,%s,%s,ST_GeomFromText(%s,4326))", (osm_id, cls, nm, wkt))
            elif table == "rail":
                cur.execute("INSERT INTO tw.rail (osm_id,kind,name,geom) VALUES (%s,%s,%s,ST_GeomFromText(%s,4326))", (osm_id, cls, nm, wkt))
            else:
                cur.execute("INSERT INTO tw.coastline (osm_id,geom) VALUES (%s,ST_GeomFromText(%s,4326))", (osm_id, wkt))
        n[table] = len(rows)
        print(table, len(rows), flush=True)

    lines("roads_major", "roads", "highway")
    lines("rail", "rail", "railway")
    lines("coastline", "coastline", None)

    # 点类：node 取坐标，way 取质心
    def points(src, table, kind_from_tags):
        data = load(src)
        if not data:
            print(src, "skip")
            return
        cnt = 0
        for el in data.get("elements", []):
            t = el.get("tags", {})
            if el["type"] == "node":
                pt = (el["lon"], el["lat"])
            elif el["type"] == "way":
                cc = way_coords(el)
                pt = center(cc)
            else:
                continue
            if not pt:
                continue
            kind = kind_from_tags(t) if kind_from_tags else ""
            cur.execute(f"INSERT INTO tw.{table} (osm_id,name,kind,geom) VALUES (%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326))",
                        (el["id"], el_name(el), kind, pt[0], pt[1]))
            cnt += 1
        n[table] = cnt
        print(table, cnt, flush=True)

    points("airports", "airports", lambda t: t.get("aeroway", "aerodrome"))
    points("ports", "ports", lambda t: t.get("harbour") or t.get("industrial") or t.get("man_made") or "")
    points("power", "power", lambda t: t.get("power", ""))

    # admin（环组装）
    for src, lv in [("admin6", 6), ("admin8", 8)]:
        data = load(src)
        if not data or len(json.dumps(data)) < 600:
            print(src, "skip")
            continue
        cnt = 0
        for el in data.get("elements", []):
            if el.get("type") != "relation":
                continue
            name, wkt = admin_relation(el, lv)
            if not wkt:
                continue
            cur.execute("INSERT INTO tw.admin_osm (level,osm_rel,name,geom) VALUES (%s,%s,%s,ST_GeomFromText(%s,4326))",
                        (lv, el["id"], name, wkt))
            cnt += 1
        n[src] = cnt
        print(src, cnt, flush=True)

    cur.execute("SELECT 'roads', count(*) FROM tw.roads UNION ALL SELECT 'rail', count(*) FROM tw.rail "
                "UNION ALL SELECT 'airports', count(*) FROM tw.airports UNION ALL SELECT 'ports', count(*) FROM tw.ports "
                "UNION ALL SELECT 'power', count(*) FROM tw.power UNION ALL SELECT 'coastline', count(*) FROM tw.coastline "
                "UNION ALL SELECT 'admin_osm', count(*) FROM tw.admin_osm")
    print("counts:", cur.fetchall())
    c.close()


if __name__ == "__main__":
    if os.path.exists(os.path.join(PGDATA, "postmaster.pid")):
        pass
    else:
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
    import_all()
    with open("NUL", "w") as nul:
        subprocess.run([PGCTL, "-D", PGDATA, "stop", "-m", "fast"], stdout=nul, stderr=nul, timeout=60)
    print("PHASE3 IMPORT DONE")
