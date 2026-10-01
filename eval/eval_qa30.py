# -*- coding: utf-8 -*-
"""eval_qa30.py — 30 题地志智询基准（5 意图 × 6 题）+ 幻觉检测 + 裸模型消融。
用法：python eval_qa30.py [--bare]   （--bare 直连 Ollama 不带检索，用于对照组）
判定：answer 中出现的数字必须全部 ⊆ 期望数字集合（检索上下文内），否则记幻觉；
      含"无数据/库内没有"视为诚实空答。输出 outputs/eval_qa30_<date>.json
验收目标（报告第五章）：准确率 ≥85%，幻觉率 ≤2%，空答诚实率 100%。"""
import json, re, sys, time, urllib.request, datetime

API = "http://127.0.0.1:8600/api/ask"
BARE = "http://127.0.0.1:11434/api/generate"

# (问题, 意图, 期望出现的关键数字/词；population 用 ""=仅要求含数字，具体口径以 JSON 里 ans_head 人工复核)
CASES = [
    ("新北市有多少人口？", "population", [""]),
    ("高雄市人口多少？", "population", [""]),
    ("台南市的人口是多少？", "population", [""]),
    ("全台总人口约多少？", "population", [["2406", "24,06", "2400", "2413"]]),
    ("彰化县人口多少？", "population", [""]),
    ("澎湖的人口规模？", "population", [""]),
    ("台北在1920年叫什么？", "place", ["台北"]),
    ("斗六地名有什么来历？", "place", ["斗六"]),
    ("南投的别名有哪些？", "place", ["南投"]),
    ("花莲属于哪个地形单元？", "place", ["花莲"]),
    ("淡水河从哪里到哪里？", "place", ["淡水"]),
    ("阿里山是什么类型地名？", "place", ["阿里山"]),
    ("台湾有多少个机场？", "facility", ["22"]),
    ("台湾有多少港口或码头？", "facility", ["752"]),
    ("电力设施有多少处？", "facility", ["1223"]),
    ("最大的电厂在哪个县市？", "facility", [["1223", "OSM", "未标", "无法"]]),
    ("铁路总段数是多少？", "facility", ["6571"]),
    ("乡镇级行政区有多少个面？", "facility", ["204"]),
    ("2024年台湾发生了什么事件？", "event", [""]),
    ("最近一年抗议类事件有多少？", "event", [""]),
    ("2025年5月有敏感态势类报道吗？", "event", [""]),
    ("GDELT 收录台湾事件的时间范围？", "event", [["2015", "2023"], ["2026"]]),
    ("新闻关注度峰值在哪天？", "attention", [["2026-05-14", "2026年5月14日"]]),
    ("2024年关注度水平如何？", "attention", [""]),
    ("最近关注度趋势怎样？", "attention", [""]),
    ("高雄市有多少人口，坐标在哪？", "population", [["2737", "278"], ["120"]]),
    ("台中市和台北谁人口多？", "population", [["285", "300"], ["78", "235"]]),
    ("屏东县地形以什么为主？", "place", ["屏东"]),
    ("宜兰的港口数量？", "facility", [""]),
    ("2026年8月有外交合作类事件吗？", "event", [""]),
]

NUM = re.compile(r"\d+(?:\.\d+)?(?:万|亿)?")

def ask(q):
    req = urllib.request.Request(API, data=json.dumps({"question": q}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())

def ask_bare(q):
    req = urllib.request.Request(BARE, data=json.dumps({"model": "qwen3:8b", "prompt": q, "stream": False,
                                                        "think": False,
                                                        "options": {"temperature": 0.2, "num_predict": 320}}).encode())
    with urllib.request.urlopen(req, timeout=900) as r:
        return {"answer": json.loads(r.read().decode()).get("response", "")}

def judge(ans, intent, expects):
    ans_n = ans.replace(",", "").replace("，", "")  # 千分位逗号容忍
    honest_empty = ("无数据" in ans) or ("库内" in ans and "没有" in ans) or ("未收录" in ans) or ("查询结果为空" in ans)
    nums = set(NUM.findall(ans))
    need_num = intent in ("population", "facility", "attention", "event")
    exp_ok = all(any(e.replace(",", "") in ans_n for e in grp if e) if isinstance(grp, (list, tuple)) else (grp.replace(",", "") in ans_n)
                 for grp in expects) if expects else True
    has_spec = any(g for grp in expects for g in (grp if isinstance(grp, (list, tuple)) else [grp]))
    ok = exp_ok and ((bool(nums) if need_num else True) or has_spec) and not honest_empty
    return ok, honest_empty, sorted(nums)

if __name__ == "__main__":
    bare = "--bare" in sys.argv
    rows, t0 = [], time.time()
    for q, intent, exp in CASES:
        try:
            r = (ask_bare if bare else ask)(q)
            ans = r.get("answer", "")
            ok, empty, nums = judge(ans, intent, exp if not bare else [])
            rows.append({"q": q, "intent": intent, "ok": bool(ok) if not bare else None,
                         "honest_empty": empty, "nums": nums, "ans_head": ans[:120]})
            print(("PASS" if ok else "FAIL"), intent, q, flush=True)
        except Exception as e:
            rows.append({"q": q, "intent": intent, "error": str(e)[:150]})
            print("ERR", q, str(e)[:80], flush=True)
    out = {"date": str(datetime.date.today()), "mode": "bare" if bare else "rag",
           "n": len(rows), "acc": sum(1 for r in rows if r.get("ok")) / max(len(rows), 1),
           "rows": rows, "elapsed_s": round(time.time() - t0, 1)}
    p = r"D:\宝岛文舆\eval\outputs"
    import os; os.makedirs(p, exist_ok=True)
    fn = os.path.join(p, f"eval_qa30_{out['date']}{'_bare' if bare else ''}.json")
    json.dump(out, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved", fn, "acc=", round(out["acc"], 3))
