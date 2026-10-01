# -*- coding: utf-8 -*-
"""make_contest_demo.py — 从 demo 生成参赛版 demo_contest：
剔除非民用设施层与内部字样，民事口径文案，保留自渲街道底图。幂等可重跑。"""
import os, re, shutil, json

SRC = r"H:\taiwan\demo"
DST = r"D:\宝岛文舆\demo_contest"
SKIP = {"台湾人文-启动演示.zip", "_shots"}

def copy_tree(s, d):
    os.makedirs(d, exist_ok=True)
    for name in os.listdir(s):
        if name in SKIP:
            continue
        ss, dd = os.path.join(s, name), os.path.join(d, name)
        if os.path.isdir(ss):
            copy_tree(ss, dd)
        else:
            shutil.copy2(ss, dd)

def patch(text, pairs):
    for a, b in pairs:
        text = text.replace(a, b)
    return text

copy_tree(SRC, DST)

# ── index.html ──
p = os.path.join(DST, "index.html")
h = open(p, encoding="utf-8").read()
h = patch(h, [
    ("台湾人文地理 · 验证演示", "宝岛文舆 · 开源GIS地志智询系统"),
    ("单区域全要素竖切（科研口径）", "中国台湾省人文地理 · 开源数据 · 防幻觉智询（参赛演示）"),
    ("数据：GeoNames · GADM · OSM（全量包） · WorldPop · GDELT · SRTM 70m 地形（补洞） · NGA CADRG（底图）",
     "数据：GeoNames · OpenStreetMap · WorldPop · GDELT · NASA SRTM 70m（补洞） · 底图全部自渲（无第三方瓦片再分发）"),
    (" · 密级：内部", ""),
    ("· 密级：内部", ""),
])
h = re.sub(r'\s*<label class="chk"><input type="checkbox" data-ly="mil">[^<]*<span class="cnt">[^<]*</span></label>', '', h)
h = h.replace("等高线/水系/其他/工商/楼群来自 OSM 全量包与 70m DEM 派生层",
              "等高线/水系/工商/楼群来自 OSM 与 70m DEM 派生层（民用口径）")
h = h.replace(" · 非民用 1,178", "")
open(p, "w", encoding="utf-8").write(h)

# ── app.js ──
p = os.path.join(DST, "assets", "app.js")
j = open(p, encoding="utf-8").read()
i0 = j.find("layers.mil = L.layerGroup();")
c0 = j.rfind("/* 非民用设施", 0, i0) if i0 > -1 else -1
if c0 > -1:
    i0 = c0
i1 = j.find("/* 工商分区", i0)
if i0 > -1 and i1 > i0:
    j = j[:i0] + j[i1:]
j = j.replace("'#E06C5A': '非民用'", "'#E06C5A': '冲突'")
j = j.replace("OSM 全量其他/工商/楼群/电厂", "OSM 全量工商/楼群/电厂")
j = j.replace("DEM 派生等高线/具名水系 + OSM 全量其他/工商/楼群/电厂", "DEM 派生等高线/具名水系 + OSM 工商/楼群/电厂")
open(p, "w", encoding="utf-8").write(j)

# ── data_derived.js 去 mil 键 ──
p = os.path.join(DST, "data", "data_derived.js")
s = open(p, encoding="utf-8").read()
m = re.search(r"window\.TW_derived\s*=\s*(\{.*\})\s*;?\s*$", s, re.S)
if m:
    try:
        d = json.loads(m.group(1))
        had = "mil" in d
        d.pop("mil", None)
        s = s[:m.start(1)] + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n"
        open(p, "w", encoding="utf-8").write(s)
        print("data_derived mil removed:", had)
    except Exception as e:
        print("json parse fail, keep file:", e)
print("demo_contest built at", DST)
