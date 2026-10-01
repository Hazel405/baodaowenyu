# 宝岛文舆 · 开源GIS地志智询系统

> 第八届 AIC 算法主题赛（AI+开源）· 方向三「开源项目改进与生态贡献」参赛作品
> 作品形态：**QGIS 插件（主体）** + 二次开发项目 + 数据/知识工具
> 一句话：给开源 QGIS 补上"中国台湾省人文地理数据底座 + 防幻觉地志问答 + 历史舆图叠合"三件中文生态没有的事。

## 目录结构（按参赛归档组织）

```
D:\宝岛文舆\
├── README.md / LICENSE
├── plugin\baodao_wenyu\      ★ 作品主体：QGIS 插件（览/询/较/析 四工作台，GPL-2.0-or-later）
│   ├── metadata.txt plugin.py
│   └── panels\ browse.py qa.py compare.py analysis.py
├── server\                   本地服务（全离线）
│   ├── rag_server.py         防幻觉 RAG API :8600（规则路由→PostGIS→qwen3:8b 仅组织语言）
│   └── serve_tiles.py        自产 XYZ 瓦片服务 :8090
├── pipeline\                 开源数据→PostGIS→瓦片 全链脚本（可一键重建）
│   ├── import_phase1.py import_boundary.py     地名/别名/边界入库
│   ├── render_tiles.py                         街道图自渲（PostGIS→PIL，z0-13 20,854 张）
│   ├── make_tiles_terrain.py                   地形层（gdaldem+gdal2tiles，z0-11 2,853 张）
│   ├── make_hist_tiles.py                      历史舆图 GCP 纠正+切片
│   └── make_contest_demo.py                    参赛版清洗（去非民用/内部字样，幂等）
├── basemap\                  自产瓦片金字塔 street/ terrain/ hillshade/ hist_*\
├── data\                     raw\ derived\ db\tw_20261001.dump\ export\ + registry.csv + DATA_SOURCES.md
├── demo_contest\             辅助演示端（离线 Leaflet，评审快速查看，非主体）
├── eval\                     30 题问答基准 + 300 条消歧评测 + 裸模型消融（eval_qa30.py）
├── docs\                     技术报告草稿、资源清单、演示分镜、合规自查、历史图摆渡清单
├── submission\               8 项提交材料（对照规则逐件核销）
└── scripts\                  start_services.bat / install_plugin.bat 一键脚本
```

## 快速开始（评审/答辩机）

1. 装 QGIS 3.44 LTR（官网 Standalone 或 OSGeo4W，路径避免中文）；
2. 双击 `scripts\start_services.bat`（起 :8090 瓦片 + :8600 RAG，需本机 PostgreSQL:5433 与 Ollama qwen3:8b）；
3. 双击 `scripts\install_plugin.bat`（复制插件到当前 QGIS 用户配置目录）；
4. 打开 QGIS → 插件菜单「宝岛文舆」→ 依次点 **＋注入自产底图 → 览 → 询 → 较 → 析**。

## 与"直接用百度地图"的结构性差异（评委必问）

| | 在线商业地图 | 宝岛文舆 |
|---|---|---|
| 数据 | 封闭黑箱 | 每条可溯源（GeoNames/OSM/SRTM/WorldPop/GDELT + 采集时间） |
| 坐标 | GCJ-02 加偏，与科学栅格叠加必错位 | WGS84/4326 统一，直接叠 70m DEM 派生分析 |
| 时间 | 仅现势 | 清季/日治/1947/1998/当代多时点卷帘叠合 |
| AI | 无地志问答 | 防幻觉 RAG：数字只来自 SQL 检索，LLM 仅组织语言，输出回溯校验 |
| 开放 | 禁止抓取再分发 | 插件 GPL 开源、管线 MIT、区域模板可平移到其他研究对象 |

## 合规红线（已内置执行）

- 底图零第三方瓦片再分发（Esri/Bing/Google 条款禁止导出瓦片；全部自渲）；
- 数据集为民用口径（非民用要素已物理剔除，`make_contest_demo.py` 保证）；
- 地图表达按《地图管理条例》自查：台湾省为中国省级行政区域，金门马祖隶属标注，标准画法模板随插件发布；
- 全部材料匿名：无校名/LOGO/指导教师信息；
- AI 辅助开发披露与《开源及第三方资源使用清单》见 docs\ 与报告附录。
