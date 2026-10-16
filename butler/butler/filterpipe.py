"""过滤管道（红1）：filter_rules.md 是唯一规则库，DB 只记命中计数。
蒸馏输入先经此打码，模型只见净化版；审计只存元数据，绝无原文。"""
import re
from datetime import datetime
from . import config, db

SEEDS = [
    (r"AKIA[0-9A-Z]{16}", "aws_key", "AWS Access Key Id"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "github_token", "GitHub token"),
    (r"sk-[A-Za-z0-9_\-]{16,}", "api_key", "OpenAI 风格密钥"),
    (r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}", "jwt", "JWT"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,2000}?-----END [A-Z ]*PRIVATE KEY-----",
     "private_key", "PEM 私钥块"),
    (r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"'<>]{8,}",
     "credential_pair", "password=xxx 类凭据对"),
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "cn_idcard", "身份证号"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "cn_mobile", "中国大陆手机号"),
    (r"(?<!\d)(?:62|4\d|5[1-5])\d{14,17}(?!\d)", "bank_card", "银行卡号"),
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "email", "邮箱"),
]

# 行格式：正则 ||| 类别 ||| 说明   （三竖线分隔，避开正则里的 | 与引号）
_HEADER = ("<!-- 经验管家·过滤规则库（唯一真身，进每日快照）。"
           "行格式：`正则 ||| 类别 ||| 说明`；# 开头为注释。 -->\n")


def ensure_default_rules():
    """首次运行：写种子 md 并同步进 DB。"""
    if not config.FILTER_RULES_PATH.exists():
        rows = "".join(f"{p} ||| {c} ||| {n}\n" for p, c, n in SEEDS)
        config.FILTER_RULES_PATH.write_text(_HEADER + rows, "utf-8")
    sync_md_to_db()


def sync_md_to_db():
    """解析 md，UPSERT 进 filter_rules 表（保留 hits_total 计数）。"""
    c = db.conn()
    existing = {r["pattern"]: r for r in c.execute("SELECT * FROM filter_rules")}
    for pat, cat, note in _parse_md():
        if pat in existing:
            c.execute("UPDATE filter_rules SET category=?, note=? WHERE pattern=?",
                      (cat, note, pat))
        else:
            c.execute("INSERT INTO filter_rules(pattern,category,note,status,created_by)"
                      " VALUES(?,?,?,'active','seed')", (pat, cat, note))
        existing.pop(pat, None)
    for gone in existing.values():           # md 里被删的，停用
        c.execute("UPDATE filter_rules SET status='removed' WHERE id=?", (gone["id"],))
    c.commit()


def _parse_md():
    out = []
    try:
        for line in config.FILTER_RULES_PATH.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "<!--" in line:
                continue
            if "|||" not in line:               # 兼容旧表格行：跳过不炸
                continue
            cells = [x.strip() for x in line.split("|||")]
            pat = cells[0].strip("`")
            cat = cells[1] if len(cells) > 1 and cells[1] else "secret"
            note = cells[2] if len(cells) > 2 else ""
            out.append((pat, cat, note))
    except OSError:
        pass
    return out


_compiled = None          # [(regex, category)]
_compiled_mtime = None


def active_patterns():
    """带缓存的编译（md 改动即失效）；编译错误的规则跳过并记错（D8 不装死）。"""
    global _compiled, _compiled_mtime
    try:
        mtime = config.FILTER_RULES_PATH.stat().st_mtime
    except OSError:
        return []
    if _compiled is None or _compiled_mtime != mtime:
        pats = []
        for pat, cat, _ in _parse_md():
            try:
                pats.append((re.compile(pat), cat))
            except re.error as e:
                repo_log_bad_pattern(pat, str(e))
        _compiled, _compiled_mtime = pats, mtime
    return _compiled


def repo_log_bad_pattern(pat, err):
    c = db.conn()
    c.execute("INSERT INTO errors(ts,component,kind,dedup_key,last_at,sample_ref)"
              " VALUES(?,?,?,?,?,?) ON CONFLICT(dedup_key) DO UPDATE SET"
              " count=count+1, last_at=excluded.last_at",
              (db.now(), "filter", "bad_pattern", f"bad_pattern:{pat[:40]}",
               db.now(), err[:120]))
    c.commit()


def mask_text(text: str):
    """返回 (净化文本, [命中类别...])。"""
    hits = []
    out = text
    for rx, cat in active_patterns():
        out, n = rx.subn(f"[REDACTED:{cat}]", out)
        hits.extend([cat] * n)
    return out, hits


def record_audit(session_id: str, line_no: int, cats: list, run_id: str = "collect"):
    if not cats:
        return
    c = db.conn()
    for cat in cats:
        c.execute("INSERT INTO redact_audit(ts,run_id,category,session_id,line)"
                  " VALUES(?,?,?,?,?)", (db.now(), run_id, cat, session_id, line_no))
        c.execute("UPDATE filter_rules SET hits_total=hits_total+1 WHERE category=?", (cat,))
    c.commit()


def add_rule_from_candidate(pattern: str, category: str, note: str):
    """批准后追加进 md（唯一真身）再同步。"""
    txt = config.FILTER_RULES_PATH.read_text("utf-8")
    line = f"{pattern} ||| {category} ||| {note}\n"
    if pattern not in txt:
        if not txt.endswith("\n"):
            txt += "\n"
        config.FILTER_RULES_PATH.write_text(txt + line, "utf-8")
    sync_md_to_db()
