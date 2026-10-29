# 数据与运行环境位置（大文件不入 git/参赛包）

| 资产 | 位置 | 大小 | 说明 |
|---|---|---|---|
| PostgreSQL 18.6 + PostGIS 3.6.2 | 运行实例（端口 5433，库 `tw`）；**归档转储 `data\db\tw_20261001.dump`（51MB，73 张表定义）** | — | 20 表 ≈48 万条（按 12 类要素加总 482,916 条，与技术报告一致）。恢复：`createdb tw && pg_restore -d tw data\db\tw_20261001.dump`（需先装同版本 PostGIS 并 `CREATE EXTENSION postgis;`） |
| 原始数据 | `data\raw`（GeoNames TW.txt、Overpass JSON、GDELT CSV、WorldPop TIF、DEM、dem_patched.tif；*.npy 中间产物不入库） | ~1GB | 以"来源 + 构建脚本"方式复现，不整体再分发（ODbL/研究用途条款） |
| DEM 派生层 | `data\derived`（slope/aspect/flowacc/stream_tier/mobility/contours/dem_stats） | ~280MB | 公共领域 SRTM 衍生，可随包（报告注明方法链） |
| 瓦片金字塔 | `basemap\`（street z0-13 20,854 张 / terrain z0-12 2,853 张 / hillshade 中间产品 2,853 张） | ~120MB | 全部自产，可再分发；服务 `server\serve_tiles.py` |
| 模型 | Ollama 本地（qwen3:8b 5.2GB、qwen3:0.6b、bge-m3 备用） | — | 权重不随包分发，README 记录拉取命令 |
| 数据管线全量脚本 | `pipeline\`（render_tiles / make_tiles_terrain / make_hist_tiles / import_phase1 / import_boundary 等；部分脚本保留原工作路径作为可复现参照） | — | 随交付包收录民用口径管线 |

## 表清单速查（tw 模式，民用口径）
gaz_tw 45,572 · gaz_alias 43,494 · roads 34,556 · rail 6,571 · power 1,223 · ports 752 ·
airports 22 · coastline 885 · admin_osm 208 · boundary 30 · events 15,295(月分区) ·
ts_attention 710 · osm_water_line 58,547 · osm_water_poly 58,258 · osm_landuse 9,412 ·
osm_buildings 76,083 · osm_poi 142,543 · power_capacity 506 · dem_zonal_stats 222 ·
contours 15,002 · land_poly(自产陆域合并面)
（public: tw_demo_countypop 22 · tw_demo_townpop 204）
