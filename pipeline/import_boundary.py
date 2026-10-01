# -*- coding: utf-8 -*-
"""边界导入：WKT CSV → staging 表 → ST_GeomFromText → tw.boundary；含校验与停库。"""
import csv
import io
import os
import subprocess
import sys
import time
import socket

sys.stdout.reconfigure(encoding="utf-8")
PGCTL = r"H:\tools\conda_env_pg\Library\bin\pg_ctl.exe"
PGDATA = r"H:\tools\pgdata_htm2026"
if os.path.exists(os.path.join(PGDATA, "postmaster.pid")):
    pass  # 已在运行（上次异常退出未停库）
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

import psycopg2

c = psycopg2.connect(host="127.0.0.1", port=5433, dbname="tw", user="postgres")
c.autocommit = True
cur = c.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS tw.bnd_stg (level int, name text, type_name text, wkt text)""")
rows = list(csv.reader(open(r"H:\taiwan\raw\boundary_import.csv", encoding="utf8")))
buf = io.StringIO()
w = csv.writer(buf)
w.writerows(rows)
buf.seek(0)
cur.copy_expert("COPY tw.bnd_stg FROM STDIN WITH (FORMAT csv)", buf, size=64 * 1024 * 1024)
cur.execute("""
INSERT INTO tw.boundary (level, name, type_name, geom, source)
SELECT level, name, type_name, ST_Multi(ST_MakeValid(ST_GeomFromText(wkt, 4326))), 'GADM4.1'
FROM tw.bnd_stg""")
cur.execute("DROP TABLE tw.bnd_stg")
cur.execute("SELECT level, count(*), sum(ST_Area(geom::geography))/1e6 AS area_km2 FROM tw.boundary GROUP BY level ORDER BY level")
for r in cur.fetchall():
    print(f"level {r[0]}: {r[1]} 个面, 面积合计 {float(r[2]):,.0f} km²")
cur.execute("SELECT count(*) FROM tw.boundary WHERE level = 2")
n2 = cur.fetchone()[0]
# 空间连接验证：台北市边界内的地名点
cur.execute("""
SELECT b.name, count(g.gid) FROM tw.boundary b
LEFT JOIN tw.gaz_tw g ON b.level = 1 AND ST_Intersects(g.geom, b.geom)
WHERE b.level = 1 GROUP BY b.name ORDER BY 2 DESC LIMIT 5""")
for r in cur.fetchall():
    print("县市地名点 top:", r)
c.close()
with open("NUL", "w") as nul:
    subprocess.run([PGCTL, "-D", PGDATA, "stop", "-m", "fast"], stdout=nul, stderr=nul, timeout=60)
print("PHASE1-BOUNDARY DONE, townships =", n2)
