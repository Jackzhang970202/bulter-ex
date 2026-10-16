"""一键导出纯 markdown 经验包（逃生舱 / 其他宿主粘贴用 / Workbuddy 包）。"""
import json
from . import config, db, __version__


def export_kit(dest=None):
    out = config.EXPORT if dest is None else dest
    out.mkdir(parents=True, exist_ok=True)
    c = db.conn()
    rules = [dict(r) for r in c.execute("SELECT * FROM rules WHERE status<>'archived' ORDER BY id")]
    profiles = [dict(r) for r in c.execute("SELECT * FROM profiles WHERE status='active' ORDER BY id")]

    lines = [f"# 经验规则（导出 v{__version__} {db.now()}）",
             "> 用法：将本文件内容提供给任意 AI 即生效。", "", "## 规则"]
    for r in rules:
        lines.append(f"- **{r['id']}** {'禁止' if r['mode']=='forbid' else '要求'}："
                     f"{r['statement']} 例：✗{r['ex_bad'] or '—'} ✓{r['ex_good'] or '—'}")
    lines += ["", "## 画像索引", ""]
    for p in profiles:
        lines.append(f"- {p['id']}: {p['statement']}")
    (out / "RULES.md").write_text("\n".join(lines), "utf-8")

    for p in profiles:
        (out / f"{p['id']}.md").write_text(f"# {p['id']}\n\n{p['statement']}\n", "utf-8")

    (out / "MANIFEST.md").write_text(
        "# 经验包 MANIFEST\n\n"
        "本目录是「经验管家」导出的纯 markdown 经验包（唯一真身=账本，此处为投影）。\n\n"
        "- RULES.md：规则+画像索引，≤2KB 可直接粘贴进任何 AI 会话\n"
        "- P-*.md：画像正文，按需读取\n"
        "- 其他宿主（Workbuddy/网页版）：会话开头粘贴 RULES.md 即可；"
        "该环境产生的经验回主场时用『记入规则』口令结算\n", "utf-8")
    return out
