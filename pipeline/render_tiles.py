# -*- coding: utf-8 -*-
"""render_tiles.py — 「宝岛文舆」合规自渲街道底图（无第三方瓦片）。
数据源：本地 PostGIS tw 库（OSM/GeoNames/GADM，ODbL/CC-BY）；渲染：PIL。
运行：python H:\\taiwan\\render_tiles.py [zmin zmax]   （默认 z0-13）
产物：H:\\taiwan\\basemap\\street\\{z}\\{x}\\{y}.png
署名（界面层需展示）：© OpenStreetMap contributors (ODbL) · 底图自渲。"""
import sys, os, json, math, io
import psycopg2
from multiprocessing import Pool
from PIL import Image, ImageDraw, ImageFont

ZMIN, ZMAX = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (0, 13)
OUT = r"D:\宝岛文舆\basemap\street"
BBOX = (118.0, 20.9, 122.7, 26.6)          # lon0,lat0,lon1,lat1 含澎湖/金门/马祖
Dsn = dict(host="127.0.0.1", port=5433, user="postgres", dbname="tw")

SEA   = (170, 211, 223)   # #aad3df
LAND  = (242, 239, 233)   # #f2efe9
WATER = (170, 211, 223)
PARK  = (200, 230, 180)
INDUS = (228, 223, 206)
COMM  = (250, 210, 190)
RETAIL= (255, 200, 200)
ROAD_C = {"motorway": (233,167,72), "trunk": (240,191,106), "primary": (247,224,155), "secondary": (247,237,184)}
ROAD_W = {"motorway": 6, "trunk": 5, "primary": 4, "secondary": 3}
CASE   = (178, 155, 115)
ADMIN_C = (136, 122, 170)
FONT = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 12)
FONT_S = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 10)

def deg2px(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n * 256
    r = math.radians(lat)
    y = (1 - math.asinh(math.tan(r)) / math.pi) / 2 * n * 256
    return x, y

def bbox_env(tx, ty, z):
    n = 2 ** z * 256
    def lat(py):
        yy = 1 - 2 * py / n
        return math.degrees(math.atan(math.sinh(math.pi * yy)))
    return (tx * 256 / n * 360 - 180, lat((ty + 1) * 256), (tx + 1) * 256 / n * 360 - 180, lat(ty * 256))

def q(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchall()

def geoms(cur, table, env, cond="true", extra=None):
    sel = "ST_AsGeoJSON(geom)" + (f", {extra}" if extra else "")
    sql = (f"select {sel} from tw.{table} "
           f"where geom && ST_MakeEnvelope(%s,%s,%s,%s,4326) and {cond}")
    rows = q(cur, sql, [env[0], env[1], env[2], env[3]])
    out = []
    for gj, *rest in rows:
        g = json.loads(gj)
        out.append((g, rest[0] if rest else None))
    return out

def px(g, z, tx, ty):
    sx = 256 * 2 ** z; x0 = tx * 256; y0 = ty * 256
    return [((x - x0) / 256 * 256, (y - y0) / 256 * 256) for x, y in g]

def to_px(lon, lat, z, tx, ty):
    x, y = deg2px(lon, lat, z)
    return x - tx * 256, y - ty * 256

def draw_geom(d, geom, z, tx, ty, mode, **kw):
    t = geom["type"]; co = geom["coordinates"]
    if mode == "poly":
        polys = [co] if t == "Polygon" else (co if t == "MultiPolygon" else [])
        for poly in polys:                      # poly = [ring, ...]
            P = [to_px(a, b, z, tx, ty) for a, b in poly[0]]
            if len(P) > 2: d.polygon(P, fill=kw["fill"])
    elif mode == "line":
        lines = [co] if t == "LineString" else (co if t == "MultiLineString" else [])
        for ln in lines:
            P = [to_px(a, b, z, tx, ty) for a, b in ln]
            if len(P) > 1:
                if kw.get("case"):
                    d.line(P, fill=kw["case"], width=kw["w"] + 2, joint="curve")
                d.line(P, fill=kw["fill"], width=max(kw["w"], 1), joint="curve")

def _save(img, z, tx, ty):
    p = os.path.join(OUT, str(z), str(tx)); os.makedirs(p, exist_ok=True)
    img.save(os.path.join(p, f"{ty}.png"))

def render_tile(args):
    z, tx, ty = args
    img = Image.new("RGB", (256, 256), LAND)
    d = ImageDraw.Draw(img)
    env = bbox_env(tx, ty, z)
    try:
        c = psycopg2.connect(**Dsn); cur = c.cursor()
        # 无陆地且无道路 → 纯海色快速瓦片
        cur.execute("select exists(select 1 from tw.land_poly where geom && ST_MakeEnvelope(%s,%s,%s,%s,4326))", list(env))
        if not cur.fetchone()[0]:
            cur.execute("select exists(select 1 from tw.roads where geom && ST_MakeEnvelope(%s,%s,%s,%s,4326))", list(env))
            if not cur.fetchone()[0]:
                c.close()
                _save(img, z, tx, ty)
                return 1
        # 陆地：预合并 land_poly（GADM L0 ∪ OSM 乡镇面，剔除大陆侧噪声）
        d.rectangle([0, 0, 256, 256], fill=SEA)
        for g, _ in geoms(cur, "land_poly", env, "true"):
            draw_geom(d, g, z, tx, ty, "poly", fill=LAND)
        if z >= 10:
            for g, k in geoms(cur, "osm_landuse", env, "true", "kind"):
                f = {"industrial": INDUS, "commercial": COMM, "retail": RETAIL, "education": PARK}.get(k)
                if f: draw_geom(d, g, z, tx, ty, "poly", fill=f)
        for g, _ in geoms(cur, "osm_water_poly", env, "true"):
            draw_geom(d, g, z, tx, ty, "poly", fill=WATER)
        if z >= 12:
            for g, _ in geoms(cur, "osm_buildings", env, "true"):
                draw_geom(d, g, z, tx, ty, "poly", fill=(216, 208, 198))
        if z >= 11:
            for g, k in geoms(cur, "osm_water_line", env, "wclass in ('river','canal')", "wclass"):
                draw_geom(d, g, z, tx, ty, "line", fill=(150, 190, 215), w=2 if z >= 13 else 1)
        elif z >= 9:
            for g, _ in geoms(cur, "osm_water_line", env, "wclass='river'", None):
                draw_geom(d, g, z, tx, ty, "line", fill=(150, 190, 215), w=1)
        if z >= 9:
            for g, _ in geoms(cur, "rail", env, "kind='rail'", None):
                draw_geom(d, g, z, tx, ty, "line", fill=(90, 90, 90), w=1)
        wf = max(1.0, 2.0 ** (z - 13))
        order = ["secondary", "primary", "trunk", "motorway"]
        for g, k in geoms(cur, "roads", env, "true", "class"):
            if z < 7 and k in ("secondary",): continue
            if z < 9 and k == "primary": continue
            draw_geom(d, g, z, tx, ty, "line", fill=ROAD_C[k], w=max(int(ROAD_W[k] * wf), 1), case=CASE if z >= 10 else None)
        # 行政界
        if z >= 7:
            for g, _ in geoms(cur, "boundary", env, "level=2", None):
                draw_geom(d, g, z, tx, ty, "line", fill=ADMIN_C, w=1)
        # 标注
        if z >= 8:
            cur.execute("""select name, ST_X(ST_Centroid(geom)) lx, ST_Y(ST_Centroid(geom)) ly from tw.boundary
                           where level=2 and ST_Centroid(geom) && ST_MakeEnvelope(%s,%s,%s,%s,4326)""", list(env))
            for nm, lx, ly in cur.fetchall() or []:
                x, y = to_px(lx, ly, z, tx, ty)
                if nm and 0 < x < 256 and 0 < y < 256:
                    d.text((x + 3, y - 6), nm.replace("省", ""), font=FONT, fill=(60, 60, 60))
                    d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(60, 60, 60))
        if z >= 11:
            cur.execute("""select name, ST_X(ST_Centroid(geom)) lx, ST_Y(ST_Centroid(geom)) ly from tw.admin_osm
                           where level=8 and not (ST_X(ST_Centroid(geom)) between 118.9 and 120.05 and ST_Y(ST_Centroid(geom)) between 24.8 and 25.9)
                           and ST_Centroid(geom) && ST_MakeEnvelope(%s,%s,%s,%s,4326)""", list(env))
            rows = cur.fetchall() or []
            step = 1 if z >= 13 else 1
            for i, (nm, lx, ly) in enumerate(rows):
                x, y = to_px(lx, ly, z, tx, ty)
                if nm and 0 < x < 256 and 0 < y < 256:
                    d.text((x + 2, y - 5), nm, font=FONT_S, fill=(85, 85, 85))
        cur.close(); c.close()
    except Exception as e:
        print("tile err", z, tx, ty, repr(e)[:100], flush=True)
    _save(img, z, tx, ty)
    return 1

def tiles_in_bbox(z):
    n = 2 ** z
    x0 = int((BBOX[0] + 180) / 360 * n); x1 = int((BBOX[2] + 180) / 360 * n)
    def tyof(lat):
        r = math.radians(lat)
        return int((1 - math.asinh(math.tan(r)) / math.pi) / 2 * n)
    y0 = tyof(BBOX[3]); y1 = tyof(BBOX[1])
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

if __name__ == "__main__":
    todo = [t for z in range(ZMIN, ZMAX + 1) for t in tiles_in_bbox(z)]
    print("tiles:", len(todo), flush=True)
    with Pool(8) as pool:
        for i, _ in enumerate(pool.imap_unordered(render_tile, todo, chunksize=16)):
            if i % 500 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    print("RENDER DONE", flush=True)
