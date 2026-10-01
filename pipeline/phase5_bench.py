# -*- coding: utf-8 -*-
"""阶段5：检索四接口基准实测（EXPLAIN ANALYZE）+ 中文消歧评测集构建。自包含起停库。"""
import csv
import os
import re
import socket
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
RAW = r"H:\taiwan\raw"
PGCTL = r"H:\tools\conda_env_pg\Library\bin\pg_ctl.exe"
PGDATA = r"H:\tools\pgdata_htm2026"


def ensure_pg():
    if os.path.exists(os.path.join(PGDATA, "postmaster.pid")):
        return
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


def bench(cur, name, sql, params=None):
    cur.execute("EXPLAIN (ANALYZE, BUFFERS OFF, COSTS OFF, TIMING OFF) " + sql, params or ())
    plan = [r[0] for r in cur.fetchall()]
    t = [p for p in plan if "Execution Time" in p]
    ms = float(re.search(r"([\d.]+) ms", t[0]).group(1)) if t else -1
    rows = [p for p in plan if p.startswith("Rows")]
    print(f"{name}: {ms:.2f} ms  ({rows[0] if rows else ''})", flush=True)
    return ms


def main():
    ensure_pg()
    import psycopg2
    c = psycopg2.connect(host="127.0.0.1", port=5433, dbname="tw", user="postgres")
    cur = c.cursor()
    results = []

    # ── GZ-01 地名检索 ──
    results.append(bench(cur, "GZ-01a 中文别名精确(台北)",
        "SELECT g.name, g.fcode FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid) "
        "WHERE a.alt = %s ORDER BY g.population DESC NULLS LAST LIMIT 10", ("台北",)))
    cur.execute("SELECT g.name, g.fcode, g.population FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid) "
                "WHERE a.alt = %s ORDER BY g.population DESC NULLS LAST LIMIT 3", ("台北",))
    for r in cur.fetchall():
        print("   命中:", r, flush=True)
    results.append(bench(cur, "GZ-01b 英文模糊(错拼 Kaoshiung)",
        "SELECT name, similarity(name, %s) s FROM tw.gaz_tw WHERE name %% %s "
        "ORDER BY s DESC LIMIT 5", ("Kaoshiung", "Kaoshiung")))
    results.append(bench(cur, "GZ-01c 高亮地名(阿里山)",
        "SELECT name, fcode, elev_m FROM tw.gaz_tw WHERE name ILIKE %s OR asciiname ILIKE %s LIMIT 10",
        ("%阿里山%", "%Alishan%")))

    # ── GZ-02 坐标反查 KNN ──
    results.append(bench(cur, "GZ-02 KNN反查(台北101附近)",
        "SELECT name, fcode, ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(121.5644, 25.0330), 4326)::geography)::int d "
        "FROM tw.gaz_tw WHERE geom IS NOT NULL ORDER BY geom <-> ST_SetSRID(ST_MakePoint(121.5644, 25.0330), 4326) LIMIT 5"))
    cur.execute("SELECT name, ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(121.5644, 25.0330), 4326)::geography)::int "
                "FROM tw.gaz_tw WHERE geom IS NOT NULL ORDER BY geom <-> ST_SetSRID(ST_MakePoint(121.5644, 25.0330), 4326) LIMIT 3")
    for r in cur.fetchall():
        print("   命中:", r, flush=True)

    # ── GZ-03 ID 解析 ──
    results.append(bench(cur, "GZ-03 ID解析(geonameid=1668337)",
        "SELECT name, fcode, population FROM tw.gaz_tw WHERE geonameid = %s", (1668337,)))

    # ── GZ-04 自动补全 ──
    results.append(bench(cur, "GZ-04 前缀补全(tain)",
        "SELECT name FROM tw.gaz_tw WHERE asciiname ILIKE %s ORDER BY population DESC NULLS LAST LIMIT 10", ("tain%",)))

    # ── 时空联合（范围+时间切片事件联动） ──
    results.append(bench(cur, "时空联合: 高雄港周边5km事件(2024H2)",
        "SELECT count(*) FROM tw.events e WHERE e.sqldate BETWEEN %s AND %s "
        "AND ST_DWithin(e.geom::geography, ST_SetSRID(ST_MakePoint(120.28, 22.61), 4326)::geography, 5000)",
        ("2024-07-01", "2024-12-31")))
    results.append(bench(cur, "乡镇归属: 面含地名点(30面×45k点)",
        "SELECT b.name, count(g.gid) FROM tw.boundary b JOIN tw.gaz_tw g ON ST_Intersects(g.geom, b.geom) "
        "WHERE b.level = 2 GROUP BY b.name"))

    # ── 同名异地消歧评测集构建 ──
    cur.execute("""
    SELECT a.alt, count(DISTINCT g.geonameid) n, count(DISTINCT g.admin1) na
    FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
    WHERE a.is_zh AND length(a.alt) BETWEEN 2 AND 5
    GROUP BY a.alt HAVING count(DISTINCT g.geonameid) >= 2 AND count(DISTINCT g.admin1) >= 2
    ORDER BY n DESC LIMIT 300""")
    multi = cur.fetchall()
    with open(os.path.join(RAW, "eval_disambiguation.csv"), "w", encoding="utf8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["alias", "candidates"])
        for alt, n, na in multi:
            cur.execute("""SELECT g.geonameid, g.name, g.admin1, g.admin2, g.lat, g.lon, g.population
                FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
                WHERE a.alt = %s ORDER BY g.population DESC NULLS LAST""", (alt,))
            cands = "; ".join(f"{r[0]}|{r[1]}|{r[2]}/{r[3]}|({r[4]:.3f},{r[5]:.3f})|pop={r[6] or 0}" for r in cur.fetchall())
            w.writerow([alt, cands])
    print(f"同名异地评测集: {len(multi)} 个跨县市中文别名 → eval_disambiguation.csv", flush=True)
    for alt, n, na in multi[:6]:
        print(f"   例: {alt} → {n} 候选 / {na} 县市", flush=True)

    # 中文别名覆盖率
    cur.execute("SELECT count(*) FROM tw.gaz_tw g WHERE EXISTS (SELECT 1 FROM tw.gaz_alias a WHERE a.geonameid = g.geonameid AND a.is_zh)")
    cov = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM tw.gaz_tw")
    print(f"中文别名覆盖率: {cov}/{cur.fetchone()[0]} = {cov/45572*100:.1f}%", flush=True)

    c.close()
    print("\n==== 基准汇总（ms）====")
    for r in results:
        print(f"  {r:.2f}")
    print("MAX:", f"{max(results):.2f} ms")


if __name__ == "__main__":
    t0 = time.time()
    main()
    with open("NUL", "w") as nul:
        subprocess.run([PGCTL, "-D", r"H:\tools\pgdata_htm2026", "stop", "-m", "fast"], stdout=nul, stderr=nul, timeout=60)
    print(f"PHASE5 DONE in {time.time()-t0:.0f}s")
