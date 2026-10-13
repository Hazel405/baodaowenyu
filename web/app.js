/* 宝岛文舆 · 网页查看端
 * 双模式：
 *   live    —— 探测到 :8600 RAG 服务时，矢量瓦片走 /mvt/{layer}/{z}/{x}/{y}.pbf 直读 PostGIS，
 *              问答走 /api/ask（防幻觉链路），实体定位走 /api/locate；
 *   offline —— 探测不到服务时，退回随包 GeoJSON 快照 + 随包低层级瓦片，问答框改为本地地名检索。
 * 与 QGIS 插件共用同一套 PostGIS 库、同一瓦片金字塔、同一问答服务；本页不引入任何第三方在线瓦片。
 * 注：中文注记用 DOM Marker 而非 style 的 symbol 层，避免依赖 glyphs 字体 PBF 服务，保证完全离线。
 */
'use strict';

const API = 'http://127.0.0.1:8600';
const TILE_HOST = 'http://127.0.0.1:8090';
const ISLAND = { center: [121.0, 23.7], zoom: 6.6 };
const POP_STEP = ['step', ['coalesce', ['get', 'pop'], 0],
  '#fff5eb', 100000, '#fee0c6', 300000, '#fdae6b', 600000, '#fd8d3c', 1500000, '#e31a1c', 2500000, '#8c0d0e'];

const wan = v => (v == null || isNaN(v)) ? '—' : (v >= 10000 ? (v / 10000).toFixed(1) + ' 万' : String(Math.round(v)));
const num = v => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('zh-CN');

// 图层定义：paint 与数据源类型无关，live/offline 共用；字段名两侧已对齐（name/pop/elev/kind/…）
const LAYERS = [
  { id: 'pop_county', label: '县市人口分级', geom: 'fill', minzoom: 3, on: true, file: 'pop_county',
    paint: { 'fill-color': POP_STEP, 'fill-opacity': 0.7, 'fill-outline-color': '#6b4a33' },
    popup: [['name', '县市'], ['pop', '人口', wan]], legend: { type: 'choropleth' } },
  { id: 'town', label: '乡镇人口', geom: 'fill', minzoom: 7, on: false, file: 'town',
    paint: { 'fill-color': POP_STEP, 'fill-opacity': 0.55, 'fill-outline-color': 'rgba(90,70,50,.6)' },
    popup: [['name', '乡镇'], ['pop', '人口', wan]] },
  { id: 'town_line', label: '乡镇界', geom: 'line', minzoom: 8, on: false,
    paint: { 'line-color': 'rgba(120,120,120,.75)', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.4, 12, 1.0], 'line-dasharray': [2, 1.4] } },
  { id: 'county_line', label: '县市界', geom: 'line', minzoom: 3, on: true, file: 'county_line',
    paint: { 'line-color': '#2b3f5c', 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 1.0, 10, 2.2] },
    popup: [['name', '县市']] },
  { id: 'water_poly', label: '湖泊水库', geom: 'fill', minzoom: 6, on: false,
    paint: { 'fill-color': '#8fc3dc', 'fill-opacity': 0.75, 'fill-outline-color': '#5b93b3' },
    popup: [['name', '名称'], ['kind', '类型']] },
  { id: 'water_line', label: '河川', geom: 'line', minzoom: 6, on: false,
    paint: { 'line-color': '#69a8cc', 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.4, 12, 1.8] },
    popup: [['name', '名称'], ['wclass', '等级']] },
  { id: 'roads', label: '道路（高快速/主次）', geom: 'line', minzoom: 6, on: false,
    paint: { 'line-color': ['match', ['get', 'class'], 'motorway', '#d9662b', 'trunk', '#e39b4a',
      'primary', '#c9a227', 'secondary', '#b9b06a', '#999'],
    'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.5, 12, 2.6], 'line-opacity': 0.9 },
    popup: [['name', '路名'], ['class', '等级']] },
  { id: 'rail', label: '铁路', geom: 'line', minzoom: 7, on: false,
    paint: { 'line-color': '#4a4a55', 'line-width': ['interpolate', ['linear'], ['zoom'], 7, 0.7, 12, 2.0], 'line-dasharray': [3, 2] },
    popup: [['name', '线路'], ['kind', '类型']] },
  { id: 'peaks', label: '山峰（≥1000 m）', geom: 'circle', minzoom: 8, on: false, file: 'peaks', base: 2.4, field: 'elev',
    color: '#7a5c3e', popup: [['name', '山名'], ['elev', '高程(m)', num]] },
  { id: 'places', label: '聚落点（万人以上）', geom: 'circle', minzoom: 6, on: false, file: 'places', base: 2.2, field: 'pop',
    color: '#8a6d3b', popup: [['name', '地名'], ['pop', '人口', wan]] },
  { id: 'ports', label: '港口', geom: 'circle', minzoom: 7, on: false, file: 'ports', base: 2.6,
    color: '#2b7ba8', popup: [['name', '港口'], ['kind', '类型']] },
  { id: 'power', label: '电厂', geom: 'circle', minzoom: 8, on: false, file: 'power', base: 2.6,
    color: '#a8434f', popup: [['name', '电厂'], ['fuel', '燃料'], ['cap_mw', '容量(MW)', num]] },
  { id: 'events', label: '事件点（GDELT 采样）', geom: 'circle', minzoom: 5, on: false, file: 'events_2026', base: 2.4, field: 'mentions',
    color: '#6f4fb0', timeline: true, popup: [['date', '日期'], ['place', '地点'], ['theme', '主题'], ['mentions', '提及数', num]] },
  { id: 'cities', label: '主要城市', geom: 'circle', minzoom: 3, on: true, file: 'cities', base: 3.0, field: 'pop',
    color: '#b32d2d', popup: [['name', '城市'], ['pop', '人口', wan]] },
];

let MODE = 'off', map, labelMarkers = [], qaMarkers = [], manifest = null;

/* ───────── 服务探测 ───────── */
function fetchT(url, opt, ms) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  return fetch(url, Object.assign({ signal: c.signal }, opt)).finally(() => clearTimeout(t));
}
const FORCE_OFF = new URLSearchParams(location.search).has("offline");   // ?offline=1 强制演示离线模式，不必停服务
async function detectMode() {
  if (FORCE_OFF) return "off";
  try {
    const r = await fetchT(API + "/api/health", {}, 1200);
    return r.ok ? "live" : "off";
  } catch (e) { return "off"; }
}

/* ───────── 数据源 ───────── */
function srcFor(L) {
  if (MODE === 'live') {
    return { type: 'vector', tiles: [`${API}/mvt/${L.id}/{z}/{x}/{y}.pbf`], minzoom: L.minzoom, maxzoom: 14 };
  }
  return null;   // offline 走 geojson
}
async function geoFor(L) {
  const r = await fetch(`data/${L.file}.geojson`);
  if (!r.ok) throw new Error(L.file + ' 缺失');
  return r.json();
}

function circlePaint(L) {
  const rad = ['interpolate', ['linear'], ['zoom'], 4, radAt(L, 4), 12, radAt(L, 12)];
  return { 'circle-color': L.color, 'circle-radius': rad, 'circle-opacity': 0.88,
           'circle-stroke-color': '#fff', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 5, 0.6, 12, 1.4] };
}
function radAt(L, z) {
  if (!L.field) return L.base * (z > 8 ? 1.7 : 1);
  return ['interpolate', ['linear'], ['coalesce', ['get', L.field], 0],
    0, L.base * (z > 8 ? 1.1 : 0.7), 3000000, L.base * (z > 8 ? 3.4 : 2.1)];
}
function layerObj(L) {
  const lay = L.geom === 'line' ? { 'line-join': 'round', 'line-cap': 'round', visibility: L.on ? 'visible' : 'none' }
                                : { visibility: L.on ? 'visible' : 'none' };
  const obj = { id: L.id, source: L.id, minzoom: L.minzoom, layout: lay };
  if (MODE === 'live') obj['source-layer'] = L.id;      // geojson 源不能有 source-layer 键
  if (L.geom === 'fill') return Object.assign(obj, { type: 'fill', paint: L.paint });
  if (L.geom === 'line') return Object.assign(obj, { type: 'line', paint: L.paint });
  return Object.assign(obj, { type: 'circle', paint: circlePaint(L) });
}

/* ───────── 底图 ───────── */
function basemapTiles(kind) {
  const useFull = MODE === 'live' && document.getElementById('fullTiles').checked;
  const host = useFull ? TILE_HOST + '/' : '';
  return [`${host}${kind}/{z}/{x}/{y}.png`];
}

/* ───────── 初始化 ───────── */
async function boot() {
  MODE = await detectMode();
  const badge = document.getElementById('modeBadge');
  badge.textContent = MODE === 'live' ? '实时模式 · /mvt 直读 PostGIS + 问答服务在线' : '离线模式 · GeoJSON 快照 + 随包瓦片';
  badge.className = 'badge ' + (MODE === 'live' ? 'live' : 'off');
  document.getElementById('fullWrap').style.display = MODE === 'live' ? '' : 'none';

  map = new maplibregl.Map({
    container: 'map', center: ISLAND.center, zoom: ISLAND.zoom, minZoom: 3, maxZoom: 14,
    attributionControl: false,
    style: { version: 8, sources: {}, layers: [] },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-left');
  map.addControl(new maplibregl.AttributionControl({
    customAttribution: '自渲瓦片 · GeoNames/OSM(ODbL)/WorldPop/GDELT/SRTM · MapLibre GL JS',
  }), 'bottom-right');

  map.on('load', () => setupLayers());

  buildLayerPanel();
  buildLegend();
  buildTimeline();
  bindUI();
  await fillSrcNote();
  initHazard();
  window.BW = { map: () => map, setup: setupLayers, ask, locate, LAYERS, mode: () => MODE };  // 调试/自动化句柄
}

let SETUP = false;
async function setupLayers() {
  if (SETUP || !map) return;
  SETUP = true;
  map.addSource('basemap', { type: 'raster', tiles: basemapTiles('terrain'), tileSize: 256,
    bounds: [-180, -85, 180, 85] });
  map.addLayer({ id: 'basemap', type: 'raster', source: 'basemap',
    layout: { visibility: document.getElementById('basemap').value === 'none' ? 'none' : 'visible' } });

  // 先几何后面：fill → line → circle（数组顺序即叠放次序）
  const order = { fill: 0, line: 1, circle: 2 };
  for (const L of LAYERS.slice().sort((a, b) => order[a.geom] - order[b.geom])) {
    try {
      if (MODE === 'live') map.addSource(L.id, srcFor(L));
      else map.addSource(L.id, { type: 'geojson', data: await geoFor(L) });
      map.addLayer(layerObj(L));
      bindPopup(L);
    } catch (e) {
      console.warn('layer skip', L.id, e);
      L.missing = true;
    }
  }
  await addCityLabels();
  buildLayerPanel();                       // 缺层信息回填面板
  document.getElementById('spin').style.display = 'none';
}

/* ───────── 城市注记（DOM Marker，离线可用） ───────── */
async function addCityLabels() {
  try {
    const fc = await geoFor({ file: 'cities' });
    fc.features.forEach(f => {
      const el = document.createElement('div');
      el.className = 'lbl';
      el.textContent = f.properties.name;
      const mk = new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat(f.geometry.coordinates).addTo(map);
      labelMarkers.push(mk);
    });
  } catch (e) { console.warn('labels', e); }
}

/* ───────── 弹窗 ───────── */
const POP_TITLES = { pop_county: '县市', county_line: '县市界', town: '乡镇', town_line: '乡镇界',
  cities: '城市', places: '聚落', peaks: '山峰', ports: '港口', power: '电厂',
  events: '事件点', roads: '道路', rail: '铁路', water_line: '水系', water_poly: '湖库' };
function bindPopup(L) {
  map.on('click', L.id, ev => {
    const f = ev.features[0]; if (!f) return;
    const rows = (L.popup || []).map(([k, lab, fn]) =>
      `<tr><td>${lab}</td><td>${(fn || String)(f.properties[k])}</td></tr>`).join('');
    new maplibregl.Popup({ maxWidth: '280px' })
      .setLngLat(ev.lngLat).setHTML(`<h4>${POP_TITLES[L.id] || L.label}：${f.properties.name || ''}</h4><table>${rows}</table>`)
      .addTo(map);
  });
  map.on('mouseenter', L.id, () => map.getCanvas().style.cursor = 'pointer');
  map.on('mouseleave', L.id, () => map.getCanvas().style.cursor = '');
}

/* ───────── 图层面板 ───────── */
function buildLayerPanel() {
  const box = document.getElementById('layerList');
  box.innerHTML = '';
  LAYERS.forEach(L => {
    const lab = document.createElement('label');
    lab.className = 'lyr';
    const cnt = L.missing ? '（快照缺失）' : '';
    lab.innerHTML = `<input type="checkbox" data-id="${L.id}" ${L.on && !L.missing ? 'checked' : ''} ${L.missing ? 'disabled' : ''}>
      <div><div class="nm">${L.label}${cnt}</div><div class="meta">${L.geom === 'fill' ? '面' : L.geom === 'line' ? '线' : '点'} · z${L.minzoom} 起 · ${MODE === 'live' ? '矢量瓦片' : '快照'}</div></div>`;
    lab.querySelector('input').addEventListener('change', e => {
      if (!map.getLayer(L.id)) return;
      map.setLayoutProperty(L.id, 'visibility', e.target.checked ? 'visible' : 'none');
      if (e.target.checked && L.timeline) applyTimeline();
    });
    box.appendChild(lab);
  });
}

/* ───────── 图例 ───────── */
function buildLegend() {
  const box = document.getElementById('legend');
  const brk = [['< 10 万', '#fff5eb'], ['10–30 万', '#fee0c6'], ['30–60 万', '#fdae6b'],
               ['60–150 万', '#fd8d3c'], ['150–250 万', '#e31a1c'], ['> 250 万', '#8c0d0e']];
  let html = '<div class="sub">县市人口（WorldPop 2020 聚合）</div>';
  brk.forEach(([t, c]) => html += `<div class="row"><i style="background:${c}"></i>${t}</div>`);
  html += '<div class="sub" style="margin-top:8px">其他要素</div>';
  [['#2b3f5c', '县市界'], ['#69a8cc', '河川'], ['#d9662b', '高速公路'], ['#b32d2d', '主要城市'], ['#6f4fb0', '事件点']]
    .forEach(([c, t]) => html += `<div class="row"><i style="background:${c}"></i>${t}</div>`);
  box.innerHTML = html;
}

/* ───────── 事件时间轴 ───────── */
const MONTHS = (() => {
  const out = []; let y = 2024, m = 9;
  for (let i = 0; i < 24; i++) { out.push(`${y}-${String(m).padStart(2, '0')}`); if (++m > 12) { m = 1; y++; } }
  return out;   // 2024-09 … 2026-08
})();
function buildTimeline() {
  const lo = document.getElementById('tlMin'), hi = document.getElementById('tlMax');
  lo.max = hi.max = MONTHS.length - 1; hi.value = MONTHS.length - 1;
  [lo, hi].forEach(el => el.addEventListener('input', applyTimeline));
}
function lastDay(ym) { const [y, m] = ym.split('-').map(Number); return new Date(y, m, 0).getDate(); }
function applyTimeline() {
  const a = MONTHS[+document.getElementById('tlMin').value];
  const b = MONTHS[+document.getElementById('tlMax').value];
  const [lo, hi] = a <= b ? [a, b] : [b, a];
  document.getElementById('tlLabel').textContent = `${lo} → ${hi}（事件层按 GDELT 双周采样口径）`;
  if (map && map.getLayer('events')) {
    map.setFilter('events', ['all', ['>=', ['get', 'date'], lo + '-01'], ['<=', ['get', 'date'], hi + '-' + String(lastDay(hi)).padStart(2, '0')]]);
  }
}

/* ───────── 数据说明 ───────── */
async function fillSrcNote() {
  const box = document.getElementById('srcNote');
  try {
    const r = await fetch('data/manifest.json');
    manifest = await r.json();
    const n = Object.keys(manifest.layers).length;
    box.innerHTML = `离线快照 ${n} 层 / ${(manifest.total_bytes / 1048576).toFixed(2)} MB，生成于 ${manifest.generated}<br>` +
      `实时模式下几何改由 PostGIS ${MODE === 'live' ? '即时出 MVT' : '—'}；字段字典见仓库 data/README.md`;
  } catch (e) { box.textContent = '快照清单未生成：运行 python pipeline\\export_web_data.py'; }
}

/* ───────── 问答与定位 ───────── */
const EXAMPLES = ['宜兰有多少港口？', '新北市人口多少？', '玉山在哪里，多高？', '2025年5月发生了什么事件？',
  '台东县的关注度趋势', '彰化这个名字因何而来？'];
function bindUI() {
  const sel = document.getElementById('basemap');
  sel.addEventListener('change', () => {
    const v = sel.value;
    if (v === 'none') { map.setLayoutProperty('basemap', 'visibility', 'none'); return; }
    map.setLayoutProperty('basemap', 'visibility', 'visible');
    map.getSource('basemap').setTiles(basemapTiles(v));
  });
  document.getElementById('fullTiles').addEventListener('change', () => {
    const v = sel.value; if (v !== 'none') map.getSource('basemap').setTiles(basemapTiles(v));
  });
  document.getElementById('resetView').addEventListener('click', () => map.flyTo({ center: ISLAND.center, zoom: ISLAND.zoom }));

  const ex = document.getElementById('examples');
  EXAMPLES.forEach(t => {
    const b = document.createElement('button');
    b.className = 'chip'; b.textContent = t;
    b.addEventListener('click', () => { document.getElementById('q').value = t; ask(); });
    ex.appendChild(b);
  });
  document.getElementById('ask').addEventListener('click', ask);
  document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') ask(); });
  applyTimeline();
}

function setStatus(t) { document.getElementById('status').textContent = t; }

async function ask() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  if (MODE !== 'live') return localSearch(q);
  const btn = document.getElementById('ask');
  btn.disabled = true;
  setStatus('① 意图分类与实体抽取 → ② PostGIS 结构化检索 → ③ qwen3 组织语言（本地推理，约 5–25 s）');
  const t0 = performance.now();
  try {
    const r = await fetchT(API + '/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q }) }, 120000);
    const d = await r.json();
    renderAnswer(d, q, (performance.now() - t0) / 1000);
    setStatus('');
    if (d.entities && d.entities.length) locate(d.entities);
  } catch (e) {
    setStatus('问答服务无响应：' + String(e.message || e).slice(0, 80));
  } finally { btn.disabled = false; }
}

function renderAnswer(d, q, secs) {
  const txt = String(d.answer || '').replace(/(\d[\d,\.]*)/g, '<b>$1</b>');
  const tags = [`<span class="tag">意图 ${d.intent || '—'}</span>`, `<span class="tag">${secs.toFixed(1)} s</span>`]
    .concat((d.entities || []).map(e => `<span class="tag">${e}</span>`)).join('');
  document.getElementById('answer').innerHTML =
    `<div class="card"><div class="tags">${tags}</div><div class="txt">${txt}</div>` +
    `<div class="src">来源：${(d.sources || []).join('；')}</div></div>`;
}

async function locate(names) {
  const box = document.getElementById('locate');
  try {
    const r = await fetchT(API + '/api/locate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }) }, 30000);
    const d = await r.json();
    const pts = d.points || [];
    qaMarkers.forEach(m => m.remove()); qaMarkers = [];
    if (!pts.length) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<div class="sub">答案实体落图（点击飞行定位）</div>';
    const bnds = new maplibregl.LngLatBounds();
    pts.forEach(p => {
      bnds.extend([p.lon, p.lat]);
      const el = document.createElement('div'); el.className = 'pin';
      const mk = new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat])
        .setPopup(new maplibregl.Popup({ offset: 14 }).setHTML(
          `<h4>${p.name}${p.alias && p.alias !== p.name ? '（' + p.alias + '）' : ''}</h4>` +
          `<table><tr><td>归属</td><td>${p.county || '—'} ${p.town || ''}</td></tr>` +
          `<tr><td>坐标</td><td>${p.lat}, ${p.lon}</td></tr>` +
          `<tr><td>类型</td><td>${p.fclass || ''}</td></tr>` +
          `<tr><td>人口</td><td>${p.population ? num(p.population) : '—'}</td></tr></table>`))
        .addTo(map);
      qaMarkers.push(mk);
      const row = document.createElement('div');
      row.className = 'pt';
      row.innerHTML = `<span>${p.name}${p.alias && p.alias !== p.name ? '（' + p.alias + '）' : ''}</span>` +
        `<span>${p.county || ''} ${p.town || ''}</span>`;
      row.addEventListener('click', () => { map.flyTo({ center: [p.lon, p.lat], zoom: 11 }); mk.togglePopup(); });
      box.appendChild(row);
    });
    map.fitBounds(bnds, { padding: 90, maxZoom: 11, duration: 900 });
  } catch (e) { setStatus('定位接口无响应：' + String(e.message || e).slice(0, 60)); }
}

/* 离线模式：把提问框降级为本地地名检索（快照内查找并飞行定位） */
async function localSearch(q) {
  setStatus('离线模式：在快照中做地名检索（提问需启动 server/rag_server.py）');
  const keys = ['cities', 'places', 'peaks', 'ports', 'pop_county'];
  qaMarkers.forEach(m => m.remove()); qaMarkers = [];
  const hits = [];
  for (const k of keys) {
    try {
      const fc = await geoFor({ file: k });
      fc.features.forEach(f => {
        const nm = f.properties.name || '';
        if (nm && (q.includes(nm) || nm.includes(q))) hits.push({ f, k });
      });
    } catch (e) { /* 忽略缺层 */ }
    if (hits.length > 12) break;
  }
  const box = document.getElementById('locate');
  if (!hits.length) {
    box.hidden = false;
    box.innerHTML = '<div class="card"><div class="txt">快照内未检索到该地名。启动 RAG 服务后可用自然语言问答。</div></div>';
    return;
  }
  box.hidden = false;
  box.innerHTML = '<div class="sub">本地快照检索结果（点击飞行定位）</div>';
  const bnds = new maplibregl.LngLatBounds();
  hits.slice(0, 12).forEach(({ f, k }) => {
    const c = f.geometry.type === 'Point' ? f.geometry.coordinates : null;
    const label = LAYERS.find(L => L.file === k);
    if (c) {
      bnds.extend(c);
      const el = document.createElement('div'); el.className = 'pin';
      const mk = new maplibregl.Marker({ element: el }).setLngLat(c).addTo(map);
      qaMarkers.push(mk);
    }
    const row = document.createElement('div');
    row.className = 'pt';
    row.innerHTML = `<span>${f.properties.name}</span><span>${label ? label.label : k}</span>`;
    if (c) row.addEventListener('click', () => map.flyTo({ center: c, zoom: 11 }));
    box.appendChild(row);
  });
  if (!bnds.isEmpty()) map.fitBounds(bnds, { padding: 90, maxZoom: 11, duration: 900 });
  setStatus('');
}

/* ───────── 析 · 地形与灾害参考（与 QGIS 插件「析」同源同口径）─────────
 * 数据：tw.dem_zonal_stats（70m SRTM 空洞修补派生层），县 21 单元 + 乡镇单元 201。
 * 纪律：清单为参考口径而非结论；未覆盖单元如实标注缺口，不作推测、不作填补。 */
let HZ_META = null;

function switchPane(id) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.dataset.pane === id));
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('on', p.id === id));
}

async function initHazard() {
  document.querySelectorAll('.tab').forEach(t =>
    t.addEventListener('click', () => switchPane(t.dataset.pane)));
  const out = document.getElementById('hzOut');
  if (MODE !== 'live') {
    out.innerHTML = '<p class="placeholder">该工作台需实时模式：请先启动 server/rag_server.py（:8600）。</p>';
    return;
  }
  try {
    const r = await fetchT(API + '/api/hazard/units', {}, 15000);
    HZ_META = await r.json();
    const sel = document.getElementById('hzUnit');
    HZ_META.units.forEach(u => {
      const o = document.createElement('option');
      o.value = u.name; o.textContent = u.name;
      sel.appendChild(o);
    });
    const nt = HZ_META.units.find(u => u.name.indexOf('南投') === 0);
    if (nt) sel.value = nt.name;
  } catch (e) {
    out.innerHTML = '<p class="placeholder">县市清单读取失败：' + String(e.message || e).slice(0, 60) + '</p>';
    return;
  }
  document.getElementById('hzGo').addEventListener('click', () => hzQuery('zonal'));
  document.getElementById('hzSc').addEventListener('click', () => hzQuery('scenario'));
  document.getElementById('hzLo').addEventListener('click', hzLocate);
}

async function hzQuery(kind) {
  const unit = document.getElementById('hzUnit').value;
  const out = document.getElementById('hzOut');
  out.innerHTML = '<p class="placeholder">查询中…</p>';
  try {
    const r = await fetchT(API + '/api/hazard', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unit })
    }, 30000);
    const d = await r.json();
    if (d.error) { out.innerHTML = '<p class="placeholder">' + d.error + '</p>'; return; }
    window.__HZ = d;
    out.innerHTML = kind === 'zonal' ? hzZonalHtml(d) : hzScenarioHtml(d);
  } catch (e) {
    out.innerHTML = '<p class="placeholder">查询失败：' + String(e.message || e).slice(0, 80) + '</p>';
  }
}

function hzZonalHtml(d) {
  const rows = d.zonal.map(z =>
    `<tr><td>${z.level}</td><td>${z.units}</td><td>${z.slope_mean == null ? '—' : z.slope_mean}</td>` +
    `<td>${z.mobility == null ? '—' : z.mobility}</td><td>${z.elev_max == null ? '—' : z.elev_max}</td></tr>`).join('');
  return `<div class="card"><div class="tags"><span class="tag">${d.unit}</span><span class="tag">高程分区统计</span></div>` +
    `<table class="hz"><tr><th>层级</th><th>单元数</th><th>平均坡度°</th><th>通行难度</th><th>最高点 m</th></tr>${rows}</table>` +
    `<div class="src">来源：tw.dem_zonal_stats｜本县市覆盖 ${d.covered_towns} 个乡镇单元；未覆盖单元如实标注于数据缺口，不作推测、不作填补。</div></div>`;
}

function hzScenarioHtml(d) {
  const head = `<div class="tags"><span class="tag">${d.unit}</span><span class="tag">暴雨情景</span></div>`;
  if (!d.scenario.length) {
    return `<div class="card">${head}<div class="txt">${d.unit}内乡镇均未命中高风险条件（${d.trigger}）。</div></div>`;
  }
  const rows = d.scenario.map(x =>
    `<tr><td>${x.name}</td><td>${x.slope_mean}</td><td>${(x.steep30_frac * 100).toFixed(1)}%</td>` +
    `<td>${(x.stream_l2_frac * 100).toFixed(1)}%</td><td>${x.mobility}</td></tr>`).join('');
  return `<div class="card">${head}<table class="hz"><tr><th>乡镇</th><th>平均坡度°</th><th>&gt;30°坡</th><th>二级河道</th><th>通行难度</th></tr>${rows}</table>` +
    `<div class="src">触发条件：${d.trigger}｜来源：tw.dem_zonal_stats｜清单为参考口径，非结论。</div></div>`;
}

function hzLocate() {
  const unit = document.getElementById('hzUnit').value;
  const u = HZ_META && HZ_META.units.find(x => x.name === unit);
  if (!u || u.lat == null) return;
  map.flyTo({ center: [u.lon, u.lat], zoom: 9, duration: 900 });
}

boot();
