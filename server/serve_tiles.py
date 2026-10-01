# -*- coding: utf-8 -*-
"""serve_tiles.py — 单端口静态服务：自产瓦片金字塔 + Web 查看端（:8090）。

运行：python server\\serve_tiles.py      （答辩/演示机保持本进程）
路由：/{street|terrain|hillshade|hist_*}/{z}/{x}/{y}.png → basemap\\ 全量金字塔（可再分发，全部自渲）
      其余路径 → web\\ 查看端；http://127.0.0.1:8090/ 即打开网页版，
      只跑这一个进程也能看离线演示（GeoJSON 快照 + 随包低层级瓦片），无需 PG/Ollama。
QGIS 端：XYZ Tiles → http://127.0.0.1:8090/street/{z}/{x}/{y}.png
"""
import functools
import http.server
import os
import urllib.parse

ROOT = r"D:\宝岛文舆\basemap"
WEB = r"D:\宝岛文舆\web"
PORT = 8090
TILE_PREFIX = ("street", "terrain", "hillshade")


class H(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parts = [s for s in urllib.parse.urlsplit(path).path.split("/") if s and s != ".."]
        if not parts:
            return os.path.join(WEB, "index.html")
        head = parts[0]
        if head in TILE_PREFIX or head.startswith("hist_"):
            return os.path.join(ROOT, *parts)
        return os.path.join(WEB, *parts)

    def end_headers(self):
        # 瓦片可长缓存；页面与数据必须每次回源，否则改了 app.js 浏览器仍跑旧版（踩过坑）
        path = urllib.parse.urlsplit(self.path).path
        cache = "public, max-age=86400" if path.endswith((".png", ".jpg", ".pbf")) else "no-store"
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", cache)
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.chdir(WEB)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(H, directory=WEB)) as s:
        print("http://127.0.0.1:%d/  (tiles root=%s, web root=%s)" % (PORT, ROOT, WEB), flush=True)
        s.serve_forever()
