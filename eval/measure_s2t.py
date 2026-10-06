# -*- coding: utf-8 -*-
"""measure_s2t.py — 简繁归一改进的量测与复现脚本（改进 A）

用途：
  1) 量测「用户输入简体 -> 还原库内繁体地名」的准确率，对比 旧19字表 / 内置兜底表 / OpenCC词级；
  2) 重新生成内置兜底映射表（server/rag_server.py 中的 _S2T_FB）。

依赖：psycopg2（必装）、opencc-python-reimplemented（可选，用于生成/对照）
运行：python eval/measure_s2t.py
"""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import psycopg2
except ImportError:
    print("需要 psycopg2：pip install psycopg2-binary"); sys.exit(1)

try:
    from opencc import OpenCC
    cc_t2s = OpenCC("t2s")
    cc_s2tw = OpenCC("s2tw")
    HAS_CC = True
except ImportError:
    HAS_CC = False
    print("[警告] 未安装 opencc，仅能测量旧表基线。pip install opencc-python-reimplemented")

DB = dict(host="127.0.0.1", port=5433, user="postgres", dbname="tw")

# 改进前的 19 字表（历史基线，勿改）
OLD_K = "兰台莲花东云梦县里斗阿崇庄处乡号湾极与"
OLD_V = "蘭台蓮花東雲夢縣裡斗阿崇莊處鄉號灣極與"
OLD = dict(zip(OLD_K, OLD_V))
norm_old = lambda s: ''.join(OLD.get(c, c) for c in s)


def norm_fallback(s, pairs):
    return ''.join(pairs.get(c, c) for c in s)


def main():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    has_cjk = lambda s: any('\u4e00' <= c <= '\u9fff' for c in (s or ''))

    cur.execute("SELECT name FROM tw.gaz_tw")
    gaz = {r[0] for r in cur.fetchall() if has_cjk(r[0])}
    cur.execute("SELECT alt FROM tw.gaz_alias")
    alts = {r[0] for r in cur.fetchall() if has_cjk(r[0])}
    cur.execute("SELECT name FROM tw.boundary")
    bnd = {r[0] for r in cur.fetchall() if has_cjk(r[0])}
    names = list(gaz | alts | bnd)
    print(f"库内中文地名(去重): {len(names):,}  (gaz {len(gaz):,} / 别名 {len(alts):,} / boundary {len(bnd):,})")

    if not HAS_CC:
        hit = sum(1 for n in names if norm_old(n) == n)
        print(f"旧 19 字表还原率: {hit:,}/{len(names):,} = {hit/len(names)*100:.2f}%")
        return

    # 字符频次（用于生成兜底表）
    from collections import Counter
    freq = Counter(c for s in names for c in s if '\u4e00' <= c <= '\u9fff')

    pairs = {}
    for c in freq:
        sc = cc_t2s.convert(c)
        if sc == c or len(sc) != 1 or not ('\u4e00' <= sc <= '\u9fff'):
            continue
        if freq.get(sc, 0) > 0:        # 简体本字在库内有用字 -> 不转（避免误伤 里/后 等）
            continue
        v = cc_s2tw.convert(sc)        # 正查，避开"从繁体反推"的歧义误选
        if v != sc and len(v) == 1:
            pairs[sc] = v

    old_hit = sum(1 for n in names if norm_old(cc_t2s.convert(n)) == n)
    new_hit = sum(1 for n in names if cc_s2tw.convert(cc_t2s.convert(n)) == n)
    fb_hit = sum(1 for n in names if norm_fallback(cc_t2s.convert(n), pairs) == n)
    N = len(names)
    print()
    print("========== 名称级还原准确率 ==========")
    print(f"  基线 · 旧19字表         : {old_hit:>7,}/{N:,} = {old_hit/N*100:6.2f}%")
    print(f"  改进 · OpenCC s2tw 词级 : {new_hit:>7,}/{N:,} = {new_hit/N*100:6.2f}%")
    print(f"  兜底 · 内置扩展表       : {fb_hit:>7,}/{N:,} = {fb_hit/N*100:6.2f}%")
    print(f"  兜底表规模: {len(pairs)} 字对")

    out = os.path.join(os.path.dirname(__file__), "outputs", "s2t_measure.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"n_names": N, "old_rate": round(old_hit/N, 4), "opencc_rate": round(new_hit/N, 4),
               "fallback_rate": round(fb_hit/N, 4), "n_fallback_pairs": len(pairs),
               "keys": ''.join(sorted(pairs)), "vals": ''.join(pairs[k] for k in sorted(pairs))},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n结果已写出: {out}")
    conn.close()


if __name__ == "__main__":
    main()
