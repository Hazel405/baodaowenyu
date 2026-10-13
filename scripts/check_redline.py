# -*- coding: utf-8 -*-
"""check_redline.py — 提交/打包前的红线自检（匿名评审 + 内容合规）。

背景
----
QGIS 保存工程时会把**本机系统用户名**写进 .qgz（saveUser 属性）；录制时若临时筛选过
图层，筛选条件也会被一并保存。这两类内容一旦进入交付物，轻则暴露身份、重则违反匿名
评审要求。仓库里 `demo_project.qgz` 曾出现 `saveUser="<本机用户名>"` 与
`boundary ... sql=name ~* 'Nantou'`（只剩南投一个县），已回滚并加装本脚本防复发。

检查项
------
  工程文件（demo_project.qgz）
    A1 saveUser 必须为中性值（白名单），不得为本机用户名
    A2 .qgs 全文不得含真名/身份线索
    A3 boundary 图层的 sql 必须为 level=2（完整 22 县市），不得是临时筛选
    A4 关键图层齐全（boundary / admin_osm / 人口层 / 底图）
  全仓文本
    B1 真名/身份线索（pipeline/_internal/identity_words.txt + 内置兜底）
    B2 红线词（pipeline/_internal/redline_words.txt，打包同源词表）
  Office/PDF 元数据（文档属性里常被自动写入撰写者身份，肉眼不可见）
    C1 docx/xlsx/pptx 的 docProps（core/app/custom）含个人邮箱或身份词
    C2 同上文档属性中 creator / lastModifiedBy 等非空且非中性
    C3 PDF 内嵌元数据（Info 字典 / XMP）含个人邮箱或身份词

用法
----
  python scripts\\check_redline.py            # 只检查，输出报告；有 FAIL 时退出码 1
  python scripts\\check_redline.py --fix      # 自动净化 qgz 与 Office 文档属性
  python scripts\\check_redline.py --qgz <路径>   # 指定待检工程文件（默认仓库根 demo_project.qgz）
  python scripts\\check_redline.py --quiet    # 仅打印一行结论（供打包脚本调用）
  python scripts\\check_redline.py --no-meta  # 跳过 Office/PDF 元数据检查

注：`pipeline/_internal/` 不入库，真名与红线词表放这里，避免词表本身出现在公开仓库。
"""
import argparse
import os
import re
import shutil
import sys
import tempfile
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_QGZ = os.path.join(ROOT, "demo_project.qgz")
_INTERNAL = os.path.join(ROOT, "pipeline", "_internal")

# saveUser 允许值（中性）：空值也允许（部分 QGIS 版本不写该属性）
SAVEUSER_OK = {"", "sirh", "team", "baodao-team", "baodaowenyu", "anonymous", "user"}
SAVEUSER_FIX = "baodao-team"

# 内置兜底真名/身份线索（另从 pipeline/_internal/identity_words.txt 追加）
# 注意：本仓库公开，真名一律不入库——身份词只维护在本地 pipeline/_internal/identity_words.txt
IDENTITY_BUILTIN = []
# 内置兜底红线词（另从 redline_words.txt 追加）
REDLINE_BUILTIN = ["兵要", "軍事設施", "军事设施", "军事要素", "軍事要素"]

# boundary 图层应有的完整口径
BOUNDARY_EXPECT_SQL = "level=2"
# 关键图层（name 属性）——任一缺失即视为工程不完整
REQUIRED_LAYERS = ["boundary", "admin_osm", "pop_county", "街道图"]

TEXT_EXT = {".py", ".md", ".txt", ".json", ".html", ".js", ".css", ".bat", ".yml", ".yaml",
            ".qgs", ".csv", ".tsv", ".xml", ".cfg", ".ini", ".ps1", ".sh"}
# _internal：本地私有词表目录，词表本身就含待检词；outputs：本地运行产物（不入交付包，见 .gitignore）
SKIP_DIRS = {".git", "__pycache__", ".idea", ".vscode", "node_modules", "提交", "data",
             "_internal", "outputs"}
# 本脚本自身内含词表常量定义，必然自命中，跳过
SKIP_FILES = {os.path.abspath(__file__)}

# ── Office / PDF 元数据 ──
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".dotx", ".xltx", ".potx"}
OFFICE_PROP_FILES = ("docprops/core.xml", "docprops/app.xml", "docprops/custom.xml")
# 需要清空的文档属性标签（保留标签结构，仅置空内容）
OFFICE_CLEAR_TAGS = ["dc:creator", "cp:lastModifiedBy", "cp:lastPrinted", "dc:description",
                     "cp:keywords", "cp:category", "Manager", "Company"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 允许出现的团队匿名邮箱域（团队自用，不构成身份泄露）
EMAIL_ALLOW_DOMAIN = {"example.com", "example.org", "example.net"}


def load_words(path, fallback):
    words = list(fallback)
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                w = ln.strip()
                if w and not w.startswith("#") and w not in words:
                    words.append(w)
    except FileNotFoundError:
        pass
    return words


def identity_words():
    return load_words(os.path.join(_INTERNAL, "identity_words.txt"), IDENTITY_BUILTIN)


def redline_words():
    return load_words(os.path.join(_INTERNAL, "redline_words.txt"), REDLINE_BUILTIN)


# ────────────────────────── 工程文件检查 ──────────────────────────
def _read_qgs(qgz_path):
    """从 .qgz 中读出 .qgs 文本（qgz 即含单个 .qgs 的 zip）。"""
    with zipfile.ZipFile(qgz_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".qgs")]
        if not names:
            raise ValueError("qgz 内未找到 .qgs")
        return names[0], z.read(names[0]).decode("utf-8", errors="replace")


def check_qgz(qgz_path=None, fix=False, quiet=False):
    """检查（可选净化）工程文件。返回 (ok, problems, fixed)。"""
    qgz_path = qgz_path or DEFAULT_QGZ
    problems, fixed = [], []
    if not os.path.exists(qgz_path):
        return True, [f"[跳过] 未找到工程文件 {qgz_path}"], fixed

    try:
        name, text = _read_qgs(qgz_path)
    except Exception as e:
        return False, [f"qgz 无法解析：{e}"], fixed

    new_text = text

    # A1 saveUser / saveUserFull（QGIS 3.44+ 会另写 saveUserFull=本机账户全名，须一并净化）
    users = re.findall(r'saveUser="([^"]*)"', text)
    bad_users = [u for u in users if u not in SAVEUSER_OK]
    fulls = re.findall(r'saveUserFull="([^"]*)"', text)
    bad_fulls = [u for u in fulls if u and u not in SAVEUSER_OK]
    if bad_users or bad_fulls:
        if fix:
            new_text = re.sub(r'saveUserFull="[^"]*"', f'saveUserFull="{SAVEUSER_FIX}"', new_text)
            new_text = re.sub(r'saveUser="[^"]*"', f'saveUser="{SAVEUSER_FIX}"', new_text)
            if bad_fulls:
                fixed.append(f"saveUserFull {bad_fulls} → {SAVEUSER_FIX}")
            if bad_users:
                fixed.append(f"saveUser {bad_users} → {SAVEUSER_FIX}")
        else:
            if bad_users:
                problems.append(f"A1 工程内 saveUser 非中性值：{bad_users}（会暴露身份）")
            if bad_fulls:
                problems.append(f"A1 工程内 saveUserFull 非中性值：{bad_fulls}（会暴露身份）")

    # A2 真名/身份线索
    for w in identity_words():
        if w in new_text:
            problems.append(f"A2 工程内含身份线索「{w}」")

    # A3 boundary 的 sql 口径
    for m in re.finditer(r'table=&quot;tw&quot;\.&quot;boundary&quot; \(geom\) sql=([^"]*)', text):
        sql = m.group(1).strip()
        if sql != BOUNDARY_EXPECT_SQL:
            if fix:
                new_text = re.sub(
                    r'(table=&quot;tw&quot;\.&quot;boundary&quot; \(geom\) sql=)[^"]*',
                    rf'\g<1>{BOUNDARY_EXPECT_SQL}', new_text)
                fixed.append(f"boundary sql「{sql}」→「{BOUNDARY_EXPECT_SQL}」")
            else:
                problems.append(
                    f"A3 boundary 图层带临时筛选 sql=「{sql}」（应为 {BOUNDARY_EXPECT_SQL}，"
                    f"否则评委打开只看到部分县市）")

    # A4 关键图层
    present = set(re.findall(r'name="([^"]+)"', text))
    missing = [l for l in REQUIRED_LAYERS if l not in present]
    if missing:
        problems.append(f"A4 工程缺少关键图层：{missing}")

    # 其余图层的非空 sql（仅供人工确认，不算 FAIL）
    others = []
    for m in re.finditer(r'<layer-tree-layer[^>]*name="([^"]+)"', text):
        pass  # 图层 sql 在 maplayer 段，下面统一扫
    for m in re.finditer(r'(?:table=&quot;tw&quot;\.&quot;([^&]+)&quot;.*?sql=)([^"]*)', text):
        tbl, sql = m.group(1), m.group(2).strip()
        if tbl != "boundary" and sql:
            others.append(f"{tbl}: sql={sql}")

    if fix and new_text != text:
        _rewrite_qgz(qgz_path, name, new_text)

    return (not problems), problems, fixed


def _rewrite_qgz(qgz_path, qgs_name, new_text):
    """把净化后的 .qgs 写回 .qgz（其余成员原样保留，deflate 压缩）。"""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".qgz", dir=os.path.dirname(qgz_path) or ".")
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(qgz_path) as zin, \
                zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == qgs_name:
                    data = new_text.encode("utf-8")
                zout.writestr(item, data)
        shutil.move(tmp_path, qgz_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ────────────────────────── Office / PDF 元数据扫描 ──────────────────────────
def _bad_emails(text, self_email_ok=True):
    out = []
    for m in EMAIL_RE.finditer(text):
        full = m.group(0)
        domain = full.split("@", 1)[1].lower()
        if domain in EMAIL_ALLOW_DOMAIN:
            continue
        if full not in out:
            out.append(full)
    return out


def check_office(path, fix=False):
    """检查（可选净化）一个 Office 容器的文档属性。返回 (problems, fixed)。"""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    problems, fixed = [], []
    try:
        with zipfile.ZipFile(path) as z:
            names = {n.lower(): n for n in z.namelist()}
            targets = [(n, names[n]) for n in OFFICE_PROP_FILES if n in names]
            if not targets:
                return problems, fixed
            items = [(i, z.read(i.filename)) for i in z.infolist()]
    except Exception as e:
        return [f"C1 {rel} 无法解析（{e}）"], fixed

    changed = False
    for low, real in targets:
        idx = [k for k, (i, _) in enumerate(items) if i.filename == real]
        if not idx:
            continue
        k = idx[0]
        txt = items[k][1].decode("utf-8", errors="ignore")

        # C1 个人邮箱
        for m in _bad_emails(txt):
            if fix:
                txt = txt.replace(m, "")
                changed = True
                fixed.append(f"{rel} 清除个人邮箱「{m}」（属性 {low}）")
            else:
                problems.append(f"C1 {rel} 文档属性含个人邮箱「{m}」（{low}，匿名评审高风险）")

        # C2 身份词
        for w in identity_words():
            if w in txt:
                problems.append(f"C2 {rel} 文档属性含身份线索「{w}」（{low}）")

        # 中性性说明：非空 creator/lastModifiedBy 且非工具名 → 仅提示
        for tag in ("dc:creator", "cp:lastModifiedBy"):
            mm = re.search(r"<%s\b[^>]*>([^<]*)</%s>" % (re.escape(tag), re.escape(tag)), txt)
            if mm and mm.group(1).strip() and mm.group(1).strip() not in ("python-docx", "python-pptx"):
                if fix:
                    pass  # 下面统一清空
                else:
                    problems.append(f"C2 {rel} 文档属性 {tag} 非空：「{mm.group(1).strip()}」"
                                    f"（建议置空，避免残留撰写者线索）")
        if fix:
            before = txt
            for tag in OFFICE_CLEAR_TAGS:
                txt = re.sub(r"(<%s\b[^>]*>)[^<]*(</%s>)" % (re.escape(tag), re.escape(tag)),
                             r"\1\2", txt)
            if txt != before:
                changed = True
                items[k] = (items[k][0], txt.encode("utf-8"))
                fixed.append(f"{rel} 清空文档属性（{low}）")

    if fix and changed:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(path)[1],
                                            dir=os.path.dirname(path) or ".")
        os.close(tmp_fd)
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zo:
                for info, data in items:
                    zo.writestr(info, data)
            shutil.move(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    return problems, fixed


def check_pdf(path):
    """检查 PDF 内嵌元数据（Info 字典 / XMP）。返回 problems。"""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    problems = []
    try:
        data = open(path, "rb").read()
    except Exception as e:
        return [f"C3 {rel} 无法读取（{e}）"]
    text = data.decode("latin-1", errors="ignore")
    for m in _bad_emails(text):
        problems.append(f"C3 {rel} PDF 元数据含个人邮箱「{m}」（匿名评审高风险）")
    for w in identity_words():
        if (w.encode("utf-8") in data) or (w.encode("utf-16-be") in data):
            problems.append(f"C3 {rel} PDF 元数据含身份线索「{w}」")
    return problems


def scan_meta(fix=False):
    """遍历仓库内的 Office/PDF 文件，检查（可选净化）元数据。返回 (problems, fixed)。"""
    problems, fixed = [], []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in OFFICE_EXT and ext != ".pdf":
                continue
            if fn.endswith(".bak"):
                continue
            p = os.path.join(dp, fn)
            if ext in OFFICE_EXT:
                pr, fx = check_office(p, fix=fix)
            else:
                pr, fx = check_pdf(p), []
            problems += pr
            fixed += fx
    return problems, fixed


# ────────────────────────── 全仓文本扫描 ──────────────────────────
def scan_text():
    """扫描仓库文本文件中的身份线索与红线词。返回 problems 列表。"""
    idw = identity_words()
    redw = redline_words()
    problems = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            p = os.path.join(dp, fn)
            if os.path.abspath(p) in SKIP_FILES:
                continue
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
            except Exception:
                continue
            for w in idw:
                if w in txt:
                    problems.append(f"B1 {rel} 含身份线索「{w}」")
            for w in redw:
                if w in txt:
                    problems.append(f"B2 {rel} 含红线词「{w}」")
    return problems


def main():
    ap = argparse.ArgumentParser(description="提交/打包前的红线自检")
    ap.add_argument("--fix", action="store_true", help="自动净化 qgz（saveUser + boundary sql）")
    ap.add_argument("--qgz", default=None, help="指定待检工程文件路径")
    ap.add_argument("--quiet", action="store_true", help="仅打印一行结论")
    ap.add_argument("--text-only", action="store_true", help="跳过全仓文本扫描（只查工程）")
    ap.add_argument("--no-meta", action="store_true", help="跳过 Office/PDF 元数据检查")
    args = ap.parse_args()

    ok_qgz, prob_qgz, fixed = check_qgz(args.qgz, fix=args.fix, quiet=args.quiet)
    prob_txt = [] if args.text_only else scan_text()
    prob_meta, fixed_meta = ([], []) if args.no_meta else scan_meta(fix=args.fix)
    fixed = fixed + fixed_meta

    all_problems = [p for p in (prob_qgz + prob_txt + prob_meta) if not p.startswith("[跳过]")]
    ok = not all_problems

    if args.quiet:
        print("REDLINE:" + ("PASS" if ok else f"FAIL({len(all_problems)})"))
        return 0 if ok else 1

    print("=" * 62)
    print("红线自检（匿名评审 + 内容合规）")
    print("=" * 62)
    for p in prob_qgz:
        print(" ", p)
    if fixed:
        print("  [已净化]", "；".join(fixed))
    for p in prob_txt[:30]:
        print(" ", p)
    if len(prob_txt) > 30:
        print(f"  ... 另有 {len(prob_txt) - 30} 条文本命中")
    for p in prob_meta:
        print(" ", p)
    print("-" * 62)
    print("结论：", "PASS 无命中，可提交/打包" if ok else f"FAIL 共 {len(all_problems)} 条，需处理后重跑")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
