# -*- coding: utf-8 -*-
"""build_web_tiles.py — 为 Web 查看端的离线模式复制低层级自产瓦片。

用法：python pipeline\\build_web_tiles.py
产物：web\\tiles\\{street|terrain}\\{z}\\{x}\\{y}.png
理由：全量金字塔 23,707 张（~120 MB）不适合随包分发；离线模式只到全岛/县市视角，
      street 取 z0–10（412 张）、terrain 取 z0–9（230 张）即够看，实时模式仍走 :8090 全量服务。
      瓦片全部由本仓库管线自渲（街道：PostGIS+PIL；地形：自有 DEM+gdaldem），许可可再分发。
"""
import os
import shutil

SRC = r"D:\宝岛文舆\basemap"
DST = r"D:\宝岛文舆\web\tiles"
WANT = {"street": 10, "terrain": 9}


def main():
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    n = 0
    for lay, zmax in WANT.items():
        root = os.path.join(SRC, lay)
        if not os.path.isdir(root):
            print("skip (missing):", root)
            continue
        for zname in sorted(os.listdir(root), key=lambda s: int(s) if s.isdigit() else 99):
            if not zname.isdigit() or int(zname) > zmax:
                continue
            sdir = os.path.join(root, zname)
            for xname in os.listdir(sdir):
                sxd = os.path.join(sdir, xname)
                dxd = os.path.join(DST, lay, zname, xname)
                os.makedirs(dxd, exist_ok=True)
                for fn in os.listdir(sxd):
                    if fn.endswith(".png"):
                        shutil.copy2(os.path.join(sxd, fn), os.path.join(dxd, fn))
                        n += 1
    size = sum(os.path.getsize(os.path.join(dp, f))
               for dp, _, fs in os.walk(DST) for f in fs if f.endswith(".png"))
    print(f"copied {n} tiles, {size/1024/1024:.1f} MB -> {DST}")


if __name__ == "__main__":
    main()
