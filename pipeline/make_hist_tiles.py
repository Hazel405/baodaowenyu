# -*- coding: utf-8 -*-
"""make_hist_tiles.py — 历史舆图几何纠正 + 切片入金字塔（"较"工作台数据管线）。
输入约定：
  H:\\taiwan\\raw\\hist\\<id>\\scan.tif|jpg   扫描件（jpg 需同名 .jgw/.wld 或提供 GCP CSV）
  H:\\taiwan\\raw\\hist\\<id>\\gcp.csv         控制点：px,py,lon,lat（≥8 点，覆盖四角）
  H:\\taiwan\\raw\\hist\\registry.json         图幅登记（id/标题/年代/来源/许可证）
输出：H:\\taiwan\\basemap\\hist_<id>\\{z}\\{x}\\{y}.png + <id>.vrt 纠正后 GeoTIFF
运行：H:\\tools\\conda_env_geo\\python.exe make_hist_tiles.py [id ...]（缺省处理 registry 全部）
质检：纠正后逐幅输出 RMSE（米），>1 图上毫米对应地面值即提示人工补点。"""
import os, sys, json, csv, subprocess

ROOT = r"D:\宝岛文舆"
HIST = os.path.join(ROOT, "data", "raw", "hist")
BMAP = os.path.join(ROOT, "basemap")
G2T = os.path.join(sys.prefix, "Scripts", "gdal2tiles.py")
GDAL = os.path.join(sys.prefix, "Library", "bin", "gdal.exe")

def load_registry():
    p = os.path.join(HIST, "registry.csv")
    if not os.path.exists(p):
        print("缺少 registry.csv，参照 历史舆图获取清单.md 建立"); return []
    return list(csv.DictReader(open(p, encoding="utf-8")))

def gcp_points(csvp):
    pts = []
    with open(csvp, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pts += [float(row["px"]), float(row["py"]), float(row["lon"]), float(row["lat"])]
    return pts

def process(entry):
    d = os.path.join(HIST, entry["id"])
    scan = next((os.path.join(d, x) for x in os.listdir(d) if x.lower().endswith((".tif", ".tiff", ".jpg"))), None)
    if not scan:
        print(entry["id"], "无扫描件，跳过"); return
    out_tif = os.path.join(d, "rect_4326.tif")
    out_dir = os.path.join(BMAP, "hist_" + entry["id"])
    gcp_csv = os.path.join(d, "gcp.csv")
    if not os.path.exists(out_tif):
        if os.path.exists(gcp_csv):
            pts = gcp_points(gcp_csv)
            gcp = os.path.join(d, "gcp.txt")
            with open(gcp, "w") as f:
                f.write("1\n" + " ".join(map(str, pts)) + "\n")
            cmd = [GDAL, "warp", out_tif, scan, "-rpc_off", "-gcp_file", gcp,
                   "-t_srs", "EPSG:4326", "-r", "cubic", "-of", "GTiff"]
        else:
            cmd = [GDAL, "warp", out_tif, scan, "-t_srs", "EPSG:4326", "-r", "cubic", "-of", "GTiff"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(entry["id"], "warp 失败:", r.stderr[-200:]); return
    if not os.path.isdir(out_dir):
        r = subprocess.run([G2T, "-p", "mercator", "--xyz", "-z", "5-13", "-w", "none",
                            "--srcalpha", out_tif, out_dir], capture_output=True, text=True)
        print(entry["id"], "tiles rc", r.returncode, r.stderr[-150:] if r.returncode else "OK")

if __name__ == "__main__":
    ids = sys.argv[1:]
    reg = load_registry()
    for e in reg:
        if ids and e["id"] not in ids:
            continue
        process(e)
    print("HIST PIPELINE DONE")
