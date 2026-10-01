# -*- coding: utf-8 -*-
"""eval_disambig300.py — 300 条跨县市同名实体消歧评测。
构造：从库内选 100 组"同名跨≥2县市"的实体（聚落/山/水），每组刻意选非首条（非最大/非首个）
      作为目标，3 个提问模板共 300 题；朴素按人口或首条作答必然错，构成真实消歧考验。
真值：目标条目的 县市admin1/乡镇admin2/经纬度(1位小数)。
判定：答案含目标坐标前缀或目标 admin2 = correct；仅含干扰条目坐标/admin2 = wrong_pick；
      诚实空答 = honest_empty；其余 = other。
输出：eval/outputs/disambig300_dataset.json + disambig300_<date>.json
"""
import json, os, re, sys, time, datetime, urllib.request
import psycopg2

PG = dict(host="127.0.0.1", port=5433, user="postgres", dbname="tw")
API = "http://127.0.0.1:8600/api/ask"
OUT = r"D:\宝岛文舆\eval\outputs"

EN2CN = {"Taipei":"台北市","NewTaipei":"新北市","Taoyuan":"桃園市","Taichung":"台中市","Tainan":"台南市",
         "Kaohsiung":"高雄市","Keelung":"基隆市","HsinchuCity":"新竹市","HsinchuCounty":"新竹縣",
         "Changhua":"彰化縣","Miaoli":"苗栗縣","Nantou":"南投縣","Yulin":"雲林縣","ChiayiCity":"嘉義市",
         "ChiayiCounty":"嘉義縣","Pingtung":"屏東縣","Yilan":"宜蘭縣","Hualien":"花蓮縣","Taitung":"臺東縣",
         "Penghu":"澎湖縣","Kinmen":"金門縣","Lienkiang":"連江縣"}

SQL = """
WITH p AS (
  SELECT DISTINCT a.alt, t.geonameid, cb.name AS county, tb.name AS town,
         round(ST_Y(t.geom)::numeric,1) la, round(ST_X(t.geom)::numeric,1) lo,
         coalesce(t.population,0) AS pop
  FROM tw.gaz_alias a
  JOIN tw.gaz_tw t ON t.geonameid = a.geonameid
  JOIN tw.boundary cb ON cb.level = 2 AND ST_Within(t.geom, cb.geom)
  LEFT JOIN tw.admin_osm tb ON tb.level = 8 AND ST_Within(t.geom, tb.geom)
  WHERE t.fclass IN ('P','T','H') AND length(a.alt) >= 2 AND a.alt !~ '^[A-Za-z]'),
grp AS (
  SELECT alt FROM p GROUP BY alt
  HAVING count(DISTINCT county) >= 2 AND count(*) BETWEEN 2 AND 8
  ORDER BY count(*) DESC, alt LIMIT 100)
SELECT alt, county, town, la, lo FROM p WHERE alt IN (SELECT alt FROM grp)
ORDER BY alt, pop DESC, la
"""


def build_dataset():
    c = psycopg2.connect(**PG); cur = c.cursor()
    cur.execute(SQL)
    rows = cur.fetchall()
    c.close()
    by = {}
    for alt, county_en, town, la, lo in rows:
        county = EN2CN.get(county_en.replace(" ", ""), county_en)
        by.setdefault(alt, []).append((county, town, float(la), float(lo)))
    items = []
    for alt, lst in by.items():
        if len(lst) < 2:
            continue
        big = lst[0]
        small = next((x for x in lst[1:] if x[0] != big[0]), None)
        if not small:
            continue
        county, town, la, lo = small
        for tpl in (f"{alt}在{county}的位置？",
                    f"{county} {alt} 在哪里？",
                    f"{alt}属于{county}的哪个乡镇？坐标大约多少？"):
            items.append({"q": tpl, "name": alt, "county": county, "town": town or "",
                          "la": la, "lo": lo,
                          "decoy_county": big[0], "decoy_la": big[2], "decoy_lo": big[3]})
    return items[:300]


NUMCOM = re.compile(r"[,，]")

def ask(q):
    req = urllib.request.Request(API, data=json.dumps({"question": q}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())

COORD = re.compile(r"(\d{2}\.\d{1,3})\s*[°,度]?\s*[,，]?\s*(\d{3}\.\d{1,3})\s*[°,度]?")

def hit(ans, item, which):
    if which == "target":
        la, lo, adm = item["la"], item["lo"], item["town"] or item["county"]
    else:
        la, lo, adm = item["decoy_la"], item["decoy_lo"], item["decoy_county"]
    for m in COORD.finditer(ans):
        if abs(float(m.group(1)) - la) < 0.05 and abs(float(m.group(2)) - lo) < 0.05:
            return True
    return bool(adm) and adm in ans

def judge(ans, item):
    a = NUMCOM.sub("", ans)
    honest_empty = ("无数据" in ans) or ("未收录" in ans) or ("查询结果为空" in ans) or ("没有找到" in ans)
    t, d = hit(a, item, "target"), hit(a, item, "decoy")
    if t and not d:
        return "correct"
    if t and d:
        return "correct"  # 列举全部同名时，只要包含目标即算消歧成功
    if d and not t:
        return "wrong_pick"
    if honest_empty:
        return "honest_empty"
    return "other"

if __name__ == "__main__":
    ds = build_dataset()
    os.makedirs(OUT, exist_ok=True)
    json.dump(ds, open(os.path.join(OUT, "disambig300_dataset.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    names = len({d['name'] for d in ds})
    print(f"dataset: {len(ds)} q / {names} names", flush=True)
    stat = {"correct": 0, "wrong_pick": 0, "honest_empty": 0, "other": 0, "error": 0}
    rows, t0 = [], time.time()
    for i, item in enumerate(ds):
        try:
            r = ask(item["q"])
            v = judge(r.get("answer", ""), item)
        except Exception:
            v = "error"; r = {}
        stat[v] += 1
        rows.append({**item, "verdict": v, "ans_head": r.get("answer", "")[:100]})
        if i % 25 == 0:
            print(f"{i}/{len(ds)} {v}", flush=True)
    out = {"date": str(datetime.date.today()), "n": len(ds), "names": names, "stat": stat,
           "acc": round(stat["correct"] / max(len(ds), 1), 3),
           "wrong_pick_rate": round(stat["wrong_pick"] / max(len(ds), 1), 3),
           "elapsed_s": round(time.time() - t0, 1), "rows": rows}
    fn = os.path.join(OUT, f"disambig300_{out['date']}.json")
    json.dump(out, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("SAVED", fn, "acc=", out["acc"], "wrong=", out["wrong_pick_rate"], flush=True)
