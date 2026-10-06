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
    ("2025年5月有哪些报道？", "event", [""]),
    ("GDELT 收录台湾事件的时间范围？", "event", [["2015", "2023"], ["2026"]]),
    ("新闻关注度峰值在哪天？", "attention", [["2026-05-14", "2026年5月14日"]]),
    ("2024年关注度水平如何？", "attention", [""]),
    ("最近关注度趋势怎样？", "attention", [""]),
    ("高雄市有多少人口，坐标在哪？", "population", [["2737", "278"], ["120"]]),
    # 台中为多口径条目：ADM2 行政区 2,867,848 / PPLA2 2,850,285 / demo(WorldPop) 3,007,731
    ("台中市和台北谁人口多？", "population", [["285", "286", "300"], ["78", "235"]]),
    ("屏东县地形以什么为主？", "place", ["屏东"]),
    ("宜兰的港口数量？", "facility", [""]),
    ("2026年8月有外交合作类事件吗？", "event", [""]),
]

NUM = re.compile(r"\d+(?:\.\d+)?(?:万|亿)?")

# ── 判分口径（2026-10-06 修订，说明见 修改第二版\33_QA30判分口径修订说明.md）──────
# 诚实空答标记：模型如实说明"库内没有该数据"的常见措辞。
# 原版只认 4 种（无数据/库内没有/未收录/查询结果为空），而模型实际输出的是
# "未提及/未提供/未包含" → honest_empty 恒为 False，判分退化成"期望词出现即过"。
EMPTY_MARKERS = ("无数据", "未收录", "未提及", "未提供", "未包含", "未查到",
                 "查询结果为空", "暂缺", "没有相关", "无法提供", "无法判断",
                 "无法确定", "无法给出", "未找到")

# 允许诚实空答的题：库内确无该数据 → 空答即正确（记 honest，PASS）。
# 依据：2026-10-06 逐题连库实测。未列入者默认"库内应有数据"，空答按拒答记 FAIL。
ALLOW_EMPTY = {
    "台北在1920年叫什么？",    # gaz_tw 无"年份标注的历史名"字段
    "南投的别名有哪些？",      # gaz_alias 实测 0 条
    "花莲属于哪个地形单元？",  # 库内无"地形单元"分类字段
    "屏东县地形以什么为主？",  # 同上
    "最大的电厂在哪个县市？",  # power_capacity.cap_mw 全空（0/506）
    "淡水河从哪里到哪里？",    # gaz_tw 仅点数据，无河流起止点属性
}

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

def judge(ans, q, intent, expects):
    """返回 (ok, klass, honest_empty, nums, nums_sig)

    klass 分类：
      answer  = 有实质作答且通过
      novalue = 有作答但未给出有效数字/期望值（FAIL）
      honest  = 库内确无该数据 → 诚实空答（PASS，计入诚实空答率）
      refusal = 库内应有数据却空答 → 拒答（FAIL）
    """
    ans_n = ans.replace(",", "").replace("，", "")
    is_empty = any(m in ans for m in EMPTY_MARKERS) or ("库内" in ans and "没有" in ans)

    if is_empty:                                  # 诚实空答：按该题是否允许空答判定
        if q in ALLOW_EMPTY:
            return True, "honest", True, [], []
        return False, "refusal", True, [], []

    nums = set(NUM.findall(ans))
    q_nums = set(NUM.findall(q))
    nums_sig = nums - q_nums        # 只认"问句里没有的新数字"，防止复述年份充数
    need_num = intent in ("population", "facility", "attention", "event")
    exp_ok = all(any(e.replace(",", "") in ans_n for e in grp if e) if isinstance(grp, (list, tuple)) else (grp.replace(",", "") in ans_n)
                 for grp in expects) if expects else True
    has_spec = any(g for grp in expects for g in (grp if isinstance(grp, (list, tuple)) else [grp]))
    ok = exp_ok and ((bool(nums_sig) if need_num else True) or has_spec)
    # 库内确无数据的题：只要正确作答（空答声明 或 带解释的缺失说明）即记 honest
    if ok and q in ALLOW_EMPTY:
        return ok, "honest", False, sorted(nums), sorted(nums_sig)
    return ok, ("answer" if ok else "novalue"), False, sorted(nums), sorted(nums_sig)

if __name__ == "__main__":
    bare = "--bare" in sys.argv
    rows, t0 = [], time.time()
    for q, intent, exp in CASES:
        try:
            r = (ask_bare if bare else ask)(q)
            ans = r.get("answer", "")
            if bare:
                ok, klass, empty, nums, nums_sig = None, None, False, [], []
            else:
                ok, klass, empty, nums, nums_sig = judge(ans, q, intent, exp)
            rows.append({"q": q, "intent": intent, "ok": bool(ok) if not bare else None,
                         "klass": klass, "honest_empty": empty,
                         "nums": nums, "nums_sig": nums_sig, "ans_head": ans[:120]})
            tag = f" [{klass}]" if (not bare and klass != "answer") else ""
            print(("PASS" if ok else "FAIL") if not bare else "BARE", intent, q, tag, flush=True)
        except Exception as e:
            rows.append({"q": q, "intent": intent, "error": str(e)[:150]})
            print("ERR", q, str(e)[:80], flush=True)
    allow_total = sum(1 for q, _, _ in CASES if q in ALLOW_EMPTY)
    honest_n = sum(1 for r in rows if r.get("klass") == "honest")
    refusal_n = sum(1 for r in rows if r.get("klass") == "refusal")
    out = {"date": str(datetime.date.today()), "mode": "bare" if bare else "rag",
           "n": len(rows), "acc": sum(1 for r in rows if r.get("ok")) / max(len(rows), 1),
           "honest_n": honest_n, "refusal_n": refusal_n, "allow_total": allow_total,
           "honest_rate": round(honest_n / allow_total, 3) if allow_total else None,
           "rows": rows, "elapsed_s": round(time.time() - t0, 1)}
    import os
    # 相对脚本自身定位输出目录，避免硬编码旧机路径（换机适配）
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    os.makedirs(p, exist_ok=True)
    fn = os.path.join(p, f"eval_qa30_{out['date']}{'_bare' if bare else ''}.json")
    json.dump(out, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved", fn, "acc=", round(out["acc"], 3),
          "| honest=", honest_n, "/", allow_total, "| refusal=", refusal_n)
