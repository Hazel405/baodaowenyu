# 数据字典（参赛版 · 民用口径）

库：PostgreSQL 18.6 + PostGIS 3.6.2，模式 `tw`，EPSG:4326。
构建方式：`pipeline/` 脚本从公开源重建（GeoNames / OpenStreetMap / GDELT / WorldPop / NASA SRTM），
每条记录含来源与采集时间；许可与署名见根 README 与报告附录《开源及第三方资源使用清单》。

| 表 | 行数 | 来源 | 说明 |
|---|---|---|---|
| gaz_tw | 45,572 | GeoNames | 地名主表（点，含 fclass/fcode/高程；admin1/2 为代码，中文归属经空间挂接 boundary/admin_osm 推导） |
| gaz_alias | 43,494 | GeoNames alternatenames | 中文别名（繁体为主，覆盖率 94.2%，pg_trgm 模糊索引） |
| boundary | 30 | GADM 4.1 切片 | 县市面 level=2 共 22（对照用；现势界以 OSM 为准） |
| admin_osm | 208 | OSM admin_level=8 | 乡镇面 204（另 4 面为大陆侧 bbox 噪声，land_poly 构建时已剔除） |
| roads | 34,556 | OSM | 分级路网 motorway/trunk/primary/secondary |
| rail | 6,571 | OSM | 铁路/轻轨 |
| osm_water_line | 58,547 | OSM(Geofabrik pbf) | 水系线（river/canal/stream/drain） |
| osm_water_poly | 58,258 | OSM | 湖泊/水库/水塘 |
| osm_landuse | 9,412 | OSM | industrial/commercial/retail/education |
| osm_buildings | 76,083 | OSM | 建筑足迹（height/levels 折算层高） |
| osm_poi | 142,543 | OSM | 兴趣点（民用分类子集） |
| power | 1,223 | OSM | 电厂/变电站点位 |
| power_capacity | 506 | OSM | 电厂燃料类型（容量字段 OSM 缺失，如实记档） |
| ports / airports / coastline | 752/22/885 | OSM | 港口码头、机场、海岸线 |
| events | 15,295 | GDELT 1.0 | 新闻事件（月分区，双周采样口径，CAMEO 中文类目） |
| ts_attention | 710 | GDELT DOC | 每日新闻关注度时序 |
| dem_zonal_stats | 222 | SRTM 派生 | 县 21+乡镇 201 的坡度/流积/通行难度/最陡占比分区统计（連江縣未纳入 DEM 统计范围，故县计 21 而非 22） |
| contours | 15,002 | SRTM 派生 | 1000m 级等高线 |
| land_poly | 自产 | boundary∪admin_osm | 陆域合并面（底图渲染与空间过滤用） |
| tw_demo_countypop / tw_demo_townpop | 22/204 | WorldPop 2020 | 县/乡镇人口（public 模式） |

注：参赛版数据集**不含任何非民用要素图层**；原始工作库中的非民用表不随本仓库发布。
