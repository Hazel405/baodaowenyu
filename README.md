# 宝岛文舆 · 开源GIS地志智询系统

> 第八届 AIC 算法主题赛（AI+开源）· 方向三「开源项目改进与生态贡献」参赛作品
> 作品形态：**QGIS 插件（主体）** + 二次开发项目 + 数据/知识工具
> 一句话：给开源 QGIS 补上"中国台湾省人文地理数据底座 + 防幻觉地志问答 + 历史舆图叠合"三件中文生态没有的事。

## 目录结构（按开源归档组织）

```
baodaowenyu/
├── README.md / LICENSE / LICENSE-MIT / NOTICE
├── 技术报告_宝岛文舆.docx / .pdf   ★ 技术报告定稿（18 页：正文 13 页 + 附录 5 页，九章大纲，含团队编号）
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
│   ├── make_hist_tiles.py                      历史舆图 GCP 纠正＋切片
│   ├── export_web_data.py                      网页离线快照 GeoJSON 导出（幂等）
│   ├── build_web_tiles.py                      随包低层级瓦片打包
│   └── make_handoff.py                         生成可分发的交付快照（含合规自检与文件数校验）
├── basemap\                  自产瓦片金字塔 street/ terrain/ hillshade/ hist_*\
├── web\                      ★ 网页查看端（MapLibre，双模式：实时 MVT+问答 / 离线快照；
│   │                         在线底图可一键切换 Esri 卫星影像 / 街道图，仅在线拉取、不随包分发）
│   ├── index.html app.js style.css README.md
│   ├── vendor\               maplibre-gl 4.7.1（BSD-3，随包，无公网 CDN）
│   └── data\ tiles\          离线快照与低层级瓦片（由 pipeline 生成）
├── data\                     raw\ derived\ db\tw_20261001.dump\ + registry.csv + DATA_SOURCES.md
├── eval\                     30 题问答基准 + 300 条消歧评测 + 裸模型消融（eval_qa30.py）
├── docs\                     部署说明 · 已知限制与改进项 · 用户试用量表 · 开源及第三方资源使用清单
├── tools\                    技术报告生成（gen_docs.py 生成器 / build_docs.py 安全落盘）
└── scripts\                  start_services.bat / start_web_demo.bat / install_plugin.bat 一键脚本
```

## 快速开始

> 数据库恢复、评测复跑、故障对照与合规检查，全部见 **`docs\部署说明.md`**。

1. **零门槛看效果**：双击 `scripts\start_web_demo.bat` → 浏览器打开 http://127.0.0.1:8090/
   （只起静态服务即为离线模式：随包快照 + 自渲瓦片，不需要 PG/Ollama）；
2. 装 QGIS 3.44 LTR（官网 Standalone 或 OSGeo4W，路径避免中文）；
3. 双击 `scripts\start_services.bat`（起 :8090 瓦片 + :8600 RAG，需本机 PostgreSQL:5433 与 Ollama qwen3:8b）；
4. 双击 `scripts\install_plugin.bat`（复制插件到当前 QGIS 用户配置目录）；
5. 打开 QGIS → 插件菜单「宝岛文舆」→ 依次点 **★ 打开演示工程 → 图层工作台 → 询（问答） → 较 → 析**。

## 优化路线（Roadmap）

### R1 · 在线底图增强：Esri 卫星影像与街道图

在自产瓦片之上新增**可选在线底图**：用户按需一键切换卫星影像 / 街道参考，离线模式不受影响。

- **图层源**（Esri 公共瓦片端点，客户端实时拉取）：
  - 卫星影像：`World_Imagery` — `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
  - 街道参考：`World_Street_Map` — 同目录树 `World_Street_Map/MapServer`
- **合规边界（红线不动）**：只在线拉取、不入库、不预下载、不缓存再分发、不随包打包——
  与"底图零第三方瓦片再分发"不冲突（该红线针对的是**导出再分发**，客户端实时渲染引用是端点的标准用法）；
  端点不可达时自动回落自产瓦片，保证离线演示零依赖。
- **署名要求**：页面与工程按 Esri 条款保留 "Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community"。
- **实现要点**：
  - web\：MapLibre 新增 raster source + 底图切换控件（自产街道 / 自产地形 / Esri 卫星 / Esri 街道），
    叠加层（自产数据、历史舆图）始终位于在线底图之上；
  - 插件：演示工程内置 Esri XYZ 连接（QGIS 原生支持 XYZ Layer，零新依赖），浏览工作台加"在线底图"开关；
  - 二者共用一份白名单配置（`ESRI_BASEMAPS`），民用要素与在线底图分离审查。

### R2 · 主题升维：区域模板化（当前迭代）

把「台湾省人文地理智询」抽象为**「任意区域人文地理智询模板」**：
一份 `config/region_<code>.json` 驱动数据管线、RAG 服务、网页查看端三端，
内置 **TW（台湾）** 配置；模板机制可平移至任意区域（见平移手册）。

- **一份配置**：库名/模式、初始视角、行政层级通名（县市/乡镇→地级市/县区）、
  事件时间轴口径、数据源登记（GeoNames/OSM/WorldPop/GDELT 参数与复核备注）全部进配置；
  选择优先级 `--region` > 环境变量 `BW_REGION` > 缺省 `tw`。
- **三端已接线**：`export_web_data.py --region`（SQL 按配置生成 + 落盘 region.json）、
  `rag_server.py`（游标 shim 把 `tw.` 前缀重定向到目标 schema，同构库零 SQL 改动）、
  `web/app.js`（启动读 `data/region.json`，缺失时退回台湾缺省——旧包向后兼容）；
  新增 `pipeline/region_check.py` 配置静态校验器（TW PASS）。
- **平移指南**：见 [docs/区域模板化平移手册.md](docs/区域模板化平移手册.md)
  （十步流程 + 阶段二诚实声明：import/render 脚本参数、QA 规则口径、插件文案、历史舆图模板仍为台湾专属，须人工复校）。

### R3 · 演示升维：OSM 对照底图 + 镜头直达（当前迭代）

- **在线对照底图升维**：在 Esri 卫星/街道之上新增 **OSM 标准图**（`tile.openstreetmap.org`，ODbL 署名内置）——与自产街道图同数据同风格，是还原度对比的最佳分屏搭档；断网探测只禁 Esri 不禁 OSM（不同源可达性互不代理），运行期加载失败自动回落自产底图。
- **`?shot=` 镜头直达**：`SETUP` 完成后按 URL 参数一次性摆好底图+视角+图层+问答，内置 `dive/osm/esristreet/satellite/street/terrain/events/qa` 八镜头——比赛视频录制零废片，同视角异底图直接出分屏对照素材。
- **体验加固**：RAG 相同问题 10 分钟短缓存（重复提问/演示重开免重等本地推理）；`ollama_gen` 超时上限 900s→120s；插件瓦片地址支持 `BAODAO_TILES` 环境变量覆盖（局域网演示）；悬浮底图面板补 OSM 快捷项。

---

## 与"直接用百度地图"的结构性差异（常见疑问）

| | 在线商业地图 | 宝岛文舆 |
|---|---|---|
| 数据 | 封闭黑箱 | 每条可溯源（GeoNames/OSM/SRTM/WorldPop/GDELT + 采集时间） |
| 坐标 | GCJ-02 加偏，与科学栅格叠加必错位 | WGS84/4326 统一，直接叠 70m DEM 派生分析 |
| 时间 | 仅现势 | 清季/日治/1947/1998/当代多时点卷帘叠合 |
| AI | 无地志问答 | 防幻觉 RAG：数字只来自 SQL 检索，LLM 仅组织语言，输出回溯校验 |
| 开放 | 禁止抓取再分发 | 插件 GPL-2.0-or-later、管线与评测脚本 MIT，区域模板可平移到其他研究对象 |

## 许可（Licensing）

- **QGIS 插件（`plugin/`）**：GPL-2.0-or-later，完整文本见 [`LICENSE`](LICENSE)（QGIS 衍生插件依 GPL-2.0 发布）；
- **数据管线与评测脚本（`pipeline/`、`eval/`、`server/serve_tiles.py`）**：MIT，见 [`LICENSE-MIT`](LICENSE-MIT)；
- **数据资产** 各自许可、不随代码授权：OSM 派生数据 ODbL；GeoNames、WorldPop CC-BY 4.0；SRTM 公共领域；GDELT 开放使用条款（学术/商业/政府用途均无限制使用，须引用 GDELT Project 并附 <https://www.gdeltproject.org/> 链接）；模型权重（qwen3、bge-m3）本地拉取、依自身许可（Apache-2.0/MIT）、不随包再分发；
- **`web/data/` 下的离线快照（本仓库随源码分发的 11 个 GeoJSON）**：由 OSM 派生的图层（县市界、乡镇界与人口、聚落、港口、水系、事件点位等）作为 ODbL 1.0 衍生数据库，一并按 ODbL 1.0 提供，署名 **© OpenStreetMap contributors**（见 <https://www.openstreetmap.org/copyright>）；纯自算属性（人口统计、地形派生指标）不受 ODbL 约束，但随档不单独拆分；
- 逐源义务矩阵见 [`docs/开源及第三方资源使用清单.md`](docs/开源及第三方资源使用清单.md) 与报告附录（一）。

## 合规红线（已内置执行）

- 底图零第三方瓦片再分发（Esri/Bing/Google 条款禁止导出瓦片；全部自渲）；
- 数据集与接口均为民用口径：对外接口按 `rag_server.py` 的 `MVT_LAYERS` 图层白名单放行，
  离线快照由 `export_web_data.py` 只列民用要素表生成——**《公开地图内容表示规范》禁止表示的
  受限要素（含核电站）入库即剔除，不进任何接口、快照与页面**（库内不设对应要素表；
  核电站在导出层按 `fuel` 与名称双重排除）；
- 地图表达按《地图管理条例》与《公开地图内容表示规范》自查：台湾省为中国省级行政区域；
  **金门县、连江县（马祖）作为福建省岛屿表示**，注记加「（福建省）」括注；制图模板随插件发布；
  底图为自渲瓦片与公共领域/开放许可数据，在线底图仅作联网对照、客户端实时引用、不落盘再分发；
- 全部材料匿名：无校名/LOGO/指导教师信息；
- AI 辅助开发披露见 `submission/佐证/03_过程记录/AI辅助使用披露.md`；《开源及第三方资源使用清单》见 `docs/开源及第三方资源使用清单.md` 与报告附录。
