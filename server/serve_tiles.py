# -*- coding: utf-8 -*-
"""serve_tiles.py — 底图瓦片静态服务（:8090）。
运行：python H:\\taiwan\\serve_tiles.py   （演示/答辩机需保持本进程或做成计划任务）
目录：H:\\taiwan\\basemap\\{street|terrain|hillshade|hist_*}/{z}/{x}/{y}.png
Leaflet: L.tileLayer('http://127.0.0.1:8090/street/{z}/{x}/{y}.png')
QGIS:    XYZ Tiles → http://127.0.0.1:8090/street/{z}/{x}/{y}.png"""
import http.server, functools, os

ROOT = r"D:\宝岛文舆\basemap"
PORT = 8090

class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    os.chdir(ROOT)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(H, directory=ROOT)) as s:
        print("tile server on http://127.0.0.1:%d (root=%s)" % (PORT, ROOT), flush=True)
        s.serve_forever()
