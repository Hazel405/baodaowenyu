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
│   │                         ＋ /mvt 矢量瓦片、/api/layers、/api/locate（供网页查看端）
│   └── serve_tiles.py        单端口 :8090 = 自产 XYZ 瓦片金字塔 + web\ 查看端
├── pipeline\                 开源数据→PostGIS→瓦片 全链脚本（可一键重建）
│   ├── import_phase1.py import_boundary.py     地名/别名/边界入库
│   ├── render_tiles.py                         街道图自渲（PostGIS→PIL，z0-13 20,854 张）
│   ├── make_tiles_terrain.py                   地形层（gdaldem+gdal2tiles，z0-12 2,853 张）
│   ├── make_hist_tiles.py                      历史舆图 GCP 纠正+切片
│   ├── export_web_data.py                      网页离线快照 GeoJSON 导出（幂等）
│   ├── build_web_tiles.py                      随包低层级瓦片打包
│   └── make_handoff.py                         生成"接手包"快照（含红线自检与文件数校验）
├── basemap\                  自产瓦片金字塔 street/ terrain/ hillshade/ hist_*\
├── web\                      ★ 网页查看端（MapLibre，双模式：实时 MVT+问答 / 离线快照）
│   ├── index.html app.js style.css README.md
│   ├── vendor\               maplibre-gl 4.7.1（BSD-3，随包，无公网 CDN）
│   └── data\ tiles\          离线快照与低层级瓦片（由 pipeline 生成）
├── data\                     raw\ derived\ db\tw_20261001.dump\ + registry.csv + DATA_SOURCES.md
├── eval\                     30 题问答基准 + 300 条消歧评测 + 裸模型消融（eval_qa30.py）
├── docs\                     部署说明（接手必读）、已知限制与改进项、演示分镜、试用量表、历史图摆渡清单
├── tools\                    技术报告生成（gen_docs.py 生成器 / build_docs.py 安全落盘）
└── scripts\                  start_services.bat / start_web_demo.bat / install_plugin.bat 一键脚本
```

## 快速开始（评审/答辩机）

> 换机部署、数据库恢复、评测复跑、故障对照与红线，全部见 **`docs\部署说明.md`**（接手必读）。

1. **零门槛看效果**：双击 `scripts\start_web_demo.bat` → 浏览器打开 http://127.0.0.1:8090/
   （只起静态服务即为离线模式：随包快照 + 自渲瓦片，不需要 PG/Ollama）；
2. 装 QGIS 3.44 LTR（官网 Standalone 或 OSGeo4W，路径避免中文）；
3. 双击 `scripts\start_services.bat`（起 :8090 瓦片 + :8600 RAG，需本机 PostgreSQL:5433 与 Ollama qwen3:8b）；
4. 双击 `scripts\install_plugin.bat`（复制插件到当前 QGIS 用户配置目录）；
5. 打开 QGIS → 插件菜单「宝岛文舆」→ 依次点 **★ 打开演示工程 → 图层工作台 → 询（问答） → 较 → 析**。

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
- 数据集与接口均为民用口径：对外接口按 `rag_server.py` 的 `MVT_LAYERS` 图层白名单放行，
  离线快照由 `export_web_data.py` 只列民用要素表生成——非民用要素不进任何接口、快照与页面；
- 地图表达按《地图管理条例》自查：台湾省为中国省级行政区域，金门马祖隶属标注，标准画法模板随插件发布；
- 全部材料匿名：无校名/LOGO/指导教师信息；
- AI 辅助开发披露与《开源及第三方资源使用清单》见 docs\ 与报告附录。
