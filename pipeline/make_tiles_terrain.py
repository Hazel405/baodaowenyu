# -*- coding: utf-8 -*-
"""make_tiles_terrain.py — 用本地 GDAL 把自有 DEM/晕渲切成 XYZ 瓦片（z0-12）。
运行：python pipeline\\make_tiles_terrain.py   （需 GDAL；产物许可：源 NASA SRTM 公共领域，完全自产可分发）
产物：basemap\\terrain\\{z}\\{x}\\{y}.png（彩色高程 + 山体阴影融合）"""
import os, sys
from osgeo import gdal
gdal.UseExceptions()

ROOT = r"D:\宝岛文舆"
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "basemap")
DEM = os.path.join(RAW, "dem_patched.tif")
HS = os.path.join(RAW, "hillshade_md.tif")
os.makedirs(OUT, exist_ok=True)

RAMP = os.path.join(OUT, "dem_ramp.txt")
with open(RAMP, "w", encoding="utf-8") as f:
    pts = [(0,(0,90,0)),(20,(245,240,200)),(100,(250,220,150)),(300,(240,190,120)),
           (700,(215,150,105)),(1500,(180,105,75)),(2500,(140,70,55)),
           (3300,(110,55,60)),(3950,(255,255,255))]
    for (e1, c1), (e2, c2) in zip(pts, pts[1:]):
        f.write("%d %d %d %d %d %d %d %d\n" % (e1, *c1, e2, *c2))

COL = os.path.join(OUT, "dem_color.tif")
if not os.path.exists(COL):
    import subprocess
    g = os.path.join(os.path.dirname(sys.executable), "Library", "bin", "gdaldem.exe")
    r = subprocess.run([g, "color-relief",
                        DEM, RAMP, COL, "-alpha", "-of", "GTiff", "-b", "1", "-compute_edges"],
                       capture_output=True, text=True)
    print("gdaldem rc", r.returncode, r.stderr[-300:], flush=True)

from osgeo_utils.gdal2tiles import main as gdal2tiles_main
def tiles(src, dst, levels):
    if os.path.isdir(dst):
        print("skip existing", dst, flush=True); return
    gdal2tiles_main(["gdal2tiles", "-p", "mercator", "--xyz", "-z", levels, "-w", "none", src, dst])
    print("tiles done", dst, flush=True)

tiles(COL, os.path.join(OUT, "terrain"), "0-12")
if os.path.exists(HS):
    tiles(HS, os.path.join(OUT, "hillshade"), "0-12")
print("ALL DONE")
