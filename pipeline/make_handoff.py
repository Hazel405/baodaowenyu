# -*- coding: utf-8 -*-
"""make_handoff.py — 生成"接手包"：可交给同学继续开发的自包含项目快照。

用法：python pipeline\\make_handoff.py            （默认输出到 提交\\宝岛文舆-接手包-<日期>\\）
产物：一份完整可跑的代码 + 数据库转储 + 自产瓦片 + .git 历史 + 部署说明，并打印打包清单与校验结果。

刻意排除（既省体积也是红线）：
  内部文档  docs/_internal、data/export、开发方案_*.md、获奖胜率评估_*.docx、代码/gen_odds.py
  可重建大件  data/raw(≈700MB)、data/derived(≈280MB)、basemap/hillshade 与 *.tif 中间产品、
              scripts/*.msi(≈580MB 安装包)、demo_contest(旧演示端，已被 web/ 取代)
  运行垃圾  __pycache__、*.log、*.tmp、*.aux.xml、~$* 锁文件、docs/_pagecheck 排版自检产物
"""
import fnmatch
import os
import shutil
import subprocess
import sys
import time

ROOT = r"D:\宝岛文舆"
OUT_ROOT = os.path.join(ROOT, "提交")

EXCLUDE_DIRS_FLAT = {"__pycache__", ".idea", ".vscode", "提交", "demo_contest", "_pagecheck"}
EXCLUDE_PATHS = ["docs/_internal", "pipeline/_internal", "data/raw", "data/derived", "data/export",
                 "basemap/hillshade"]
EXCLUDE_PATTERNS = ["*.msi", "*.log", "*.tmp", "*.aux.xml", "~$*", "*.bak",
                    "开发方案_*.md", "获奖胜率评估_*.docx", "gen_odds.py", "*_v2.docx"]
# 目标：这些目录必须完整进包（源/目标文件数一致），用于自证"没漏代码"
MUST_COMPLETE = ["plugin", "server", "web", "eval", "scripts", "docs", "submission", "代码", "pipeline"]
# 出现即判定打包失败的关键词（红线自检，用 / 分隔的相对路径写法）
BANNED = ["_internal", "data/export", "hillshade", ".msi", "敏感", "开发方案", "胜率评估", "gen_odds"]


def relnorm(p):
    p = os.path.normpath(p).replace("\\", "/")
    return "" if p == "." else p


def excluded(rel):
    """rel 为相对仓库根的 posix 风格路径（目录或文件均可）。"""
    r = relnorm(rel)
    if not r:
        return False
    parts = r.split("/")
    if any(p in EXCLUDE_DIRS_FLAT for p in parts):
        return True
    if any(r == p or r.startswith(p + "/") for p in EXCLUDE_PATHS):
        return True
    return any(fnmatch.fnmatch(parts[-1], pat) for pat in EXCLUDE_PATTERNS)


def count_tree(base, rel_top):
    """统计 base/rel_top 下"应当进包"的文件数（与打包用同一套排除规则，才能对得上）。"""
    top = os.path.join(base, rel_top.replace("/", os.sep))
    n = 0
    for dp, dns, fns in os.walk(top):
        rel = relnorm(os.path.relpath(dp, base))
        dns[:] = [d for d in dns if not excluded(rel + "/" + d)]
        for fn in fns:
            if not excluded(rel + "/" + fn):
                n += 1
    return n


def force_rmtree(path):
    """.git 里的对象文件是只读的，Windows 下 rmtree 会 PermissionError——先清只读位再删。"""
    import stat

    def onexc(func, p, err):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            raise err
    try:
        shutil.rmtree(path, onexc=onexc)
    except TypeError:                      # Python < 3.12 的参数名
        shutil.rmtree(path, onerror=onexc)


def main():
    dest = os.path.join(OUT_ROOT, "宝岛文舆-接手包-" + time.strftime("%Y%m%d"))
    if os.path.isdir(dest):
        force_rmtree(dest)
    os.makedirs(dest)

    copied, total = [], 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = relnorm(os.path.relpath(dirpath, ROOT))
        dirnames[:] = [d for d in dirnames if not excluded(rel_dir + "/" + d)]
        for fn in filenames:
            rel = rel_dir + "/" + fn if rel_dir else fn
            if excluded(rel):
                continue
            src, dst = os.path.join(ROOT, rel.replace("/", os.sep)), os.path.join(dest, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)
            total += os.path.getsize(dst)

    # 部署说明放到包根，同学第一眼就看到；包内再留一份清单
    guide = os.path.join(ROOT, "docs", "部署说明.md")
    if os.path.exists(guide):
        shutil.copy2(guide, os.path.join(dest, "部署说明.md"))
        copied.append("部署说明.md（包根副本）")

    sha = ""
    try:
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        pass
    with open(os.path.join(dest, "打包清单.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"宝岛文舆 接手包  生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')}  git HEAD {sha}\n")
        f.write(f"文件 {len(copied)} 个，合计 {total/1024/1024:.1f} MB\n\n")
        f.write("[刻意排除] 内部文档（docs/_internal、data/export、开发方案、胜率评估、gen_odds）"
                "；可重建大件（data/raw、data/derived、basemap/hillshade 与 *.tif、scripts/*.msi）"
                "；运行垃圾（__pycache__、*.log、临时与锁文件）\n\n[文件清单]\n")
        f.write("\n".join(sorted(copied)))

    print(f"copied {len(copied)} files, {total/1024/1024:.1f} MB -> {dest}")

    bad = [p for p in copied if any(b.lower() in p.lower() for b in BANNED)]
    print("红线自检:", "PASS" if not bad else f"FAIL {bad[:5]}")
    for d in MUST_COMPLETE:
        s, t = count_tree(ROOT, d), count_tree(dest, d)
        print(f"  {d:12s} 应进包 {s:5d}  实进包 {t:5d}  {'OK' if s == t else '差异 ' + str(s - t)}")
    for probe in ("data/db", "basemap/street", "basemap/terrain", "web/tiles", ".git"):
        p = os.path.join(dest, probe.replace("/", os.sep))
        print(f"  {probe:16s} {'存在' if os.path.isdir(p) else '缺失'}  {count_tree(dest, probe) if os.path.isdir(p) else 0} 个文件")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
