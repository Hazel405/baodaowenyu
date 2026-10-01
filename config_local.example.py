# -*- coding: utf-8 -*-
"""config_local.example.py — 换机部署时的本地配置模板。

用法：复制本文件为同目录下的 `config_local.py`，只改需要改的键（不写就用默认值）。
`config_local.py` 已被 .gitignore 排除，不会入库、不会随包分发（避免把口令带进仓库）。
被 `server/rag_server.py` 读取；也可用环境变量覆盖：BAODAO_PG_HOST / BAODAO_PG_PORT /
BAODAO_PG_DB / BAODAO_PG_USER / BAODAO_PG_PASSWORD / BAODAO_PGCTL / BAODAO_PGDATA / BAODAO_OLLAMA。
"""

PG = {
    # ── 数据库连接（默认值对应原开发机：本机 5433 端口、库名 tw、postgres 用户、无密码）──
    "host": "127.0.0.1",
    "port": 5433,
    "db": "tw",
    "user": "postgres",
    "pwd": "",

    # ── 可选：让 rag_server 自动拉起 PostgreSQL。两项路径都存在才会尝试 pg_ctl start，
    #     否则视为"数据库由你自己/系统服务负责"，不会因此报错 ──
    "pgctl": r"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe",
    "pgdata": r"C:\Program Files\PostgreSQL\18\data",
    "pg_log": r"C:\Temp\pg_start.log",

    # ── 本地大模型服务（Ollama，需已 ollama pull qwen3:8b）──
    "ollama": "http://127.0.0.1:11434",
}

# 注意：eval/eval_qa30.py、eval/eval_disambig300.py、pipeline/export_web_data.py、
# pipeline/render_tiles.py 等脚本顶部各有一份 PG 连接常量（同为上面这组默认值）。
# 若你改了端口/库名/密码，这几处也要同步改——它们不读 config_local.py，是为了让
# 评测脚本保持"单文件即可跑"，代价是配置分散（已列入 docs/已知限制与改进项.md）。
