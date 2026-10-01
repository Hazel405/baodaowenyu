# 网页查看端（web/）

QGIS 插件是作品主体；本目录是**同一套数据与服务的轻量查看端**，解决两个现实问题：
评审不必安装 QGIS/PostGIS/Ollama 也能看到成果形态，答辩现场断网或模型未就绪时页面仍然可用。

## 双模式

| 模式 | 触发条件 | 几何来源 | 问答 |
| --- | --- | --- | --- |
| 实时 | 探测到 `:8600/api/health` | `/mvt/{layer}/{z}/{x}/{y}.pbf` 由 PostGIS `ST_AsMVT` 即时出瓦 | `/api/ask` 防幻觉链路 + `/api/locate` 实体落图 |
| 离线 | 未探测到服务 | `data/*.geojson` 随包快照（`pipeline/export_web_data.py` 生成） | 提问框自动降级为快照内地名检索 |

页面右上角徽标显示当前模式；两种模式下底图都来自本项目自渲瓦片（实时可勾选 z0–13 全量金字塔，
离线用 `pipeline/build_web_tiles.py` 随包的低层级瓦片）。

## 运行

```bat
scripts\start_web_demo.bat          :: 起 :8090 与 :8600 并打开浏览器（完整体验）
python server\serve_tiles.py        :: 只起静态服务，即离线模式
```

已在实时模式时想演示离线降级，直接访问 `http://127.0.0.1:8090/?offline=1`（强制走快照，不必停服务）。

```bat
python pipeline\export_web_data.py  :: 重新导出快照（改了库表或字段后）
python pipeline\build_web_tiles.py  :: 重新打包低层级瓦片
```

`server/serve_tiles.py` 单端口同时提供瓦片与本目录页面：`/{street|terrain|hist_*}/...` 走 `basemap/`
全量金字塔，其余路径走 `web/`。`.png/.pbf` 长缓存，页面与数据 `no-store`（改完刷新即生效）。

## 与 QGIS 插件的关系（不是换皮）

- 同一 PostGIS 库（`tw` schema 20 表）、同一瓦片金字塔、同一 `/api/ask` 问答服务；
- 图层面板的勾选即加载、县市人口分级色带，与插件"览"工作台和 `demo_project.qgz` 使用同一套
  `pop_county` 表与同一分级断点（10/30/60/150/250 万）；
- 中文注记用 DOM 标记而非 style 的 symbol 层，因此**不需要 glyphs 字体 PBF 服务**，完全离线可用。

## 许可与合规

- 前端渲染库 **MapLibre GL JS 4.7.1（BSD-3-Clause）**，已内置于 `vendor/`，不引用任何公网 CDN；
  样式与数据全部本地。第三方依赖仅这一个。
- 接口按**图层白名单**放行（见 `server/rag_server.py` 的 `MVT_LAYERS`）：只有民用要素表可对外提供，
  非民用要素表不出现在任何接口、快照或页面中。
- 定位为教学与科研参考：不用于导航、产权或行政边界判定；地图表达按《地图管理条例》自查
  （台湾省为中国省级行政区域，金门、马祖作相应标注）。
- 随参赛包离线交付，不做公网托管（对外提供互联网地图服务涉及测绘资质，见报告第七章）。

## 已知限制

- 山峰/聚落等点层在离线快照里按人口或高程取了头部若干条，不是全量；全量请走实时模式。
- 事件层为 GDELT 双周采样口径，时间轴按 ISO 日期字符串过滤，切换月份不改变统计口径说明。
- 历史舆图时点（`hist_*`）待扫描件摆渡入库后自动出现在底图下拉中（同一套 MVT/瓦片机制）。
