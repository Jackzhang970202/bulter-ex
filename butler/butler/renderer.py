"""渲染器：把账本渲染成静态"发行版"文件（hook 注入的唯一内容源）。
核心不变式：发行版是普通 md 文件——程序死了它还在；体积超限拒渲，防 compact 注入死循环。"""
import secrets
from . import config, db


def _central_section(ver: int, nonce: str, s: dict) -> str:
    lines = []
    lines.append(f"<!-- BUTLER-DIGEST v{ver} nonce={nonce} -->")
    lines.append("# 经验管家·规则发行版（本会话必须遵守）")
    lines.append("使用画像时亮明引用（如“按 P-A…”）。冲突优先级：**当前用户指令 > 本发行版 > 内置记忆**。")
    lines.append("")
    lines.append("## 规则")
    rules = [r for r in s["rules"] if r["status"] in ("active", "trial")]
    if not rules:
        lines.append("（暂无规则——这是冷启动，正常。）")
    for r in rules:
        tag = "" if r["status"] == "active" else "（trial）"
        line = f"- **{r['id']}**{'禁止' if r['mode'] == 'forbid' else '要求'}：{r['statement']}{tag}"
        if r["ex_bad"] or r["ex_good"]:
            line += f" 例：✗{r['ex_bad'] or '—'} ✓{r['ex_good'] or '—'}"
        lines.append(line)
    lines.append("")
    lines.append("## 画像索引（正文按需 Read，勿凭索引猜内容）")
    if not s["profiles"]:
        lines.append("（暂无画像。）")
    for p in s["profiles"]:
        lines.append(f"- {p['id']}: {p['statement']} → 读 `{config.FULL / (p['id'] + '.md')}`")
    lines.append("")
    lines.append("## 会话内可选指令（经验提取主通道=程序定期复盘自动蒸馏全会话，无需任何口令；以下只是快捷入口）")
    lines.append(f"- 用户说“**记入规则**”：①对照上方规则查是否已有近义条目；②已有但你违反了→草拟**改写**（dedup:violation_repeat, ref:编号）；没有→草拟新条目（必须含可判断的祈使句+一对✗✓例子）；③将候选写入笔记文件 `{config.INBOX}\\note-<随机8位>.md`，格式为 key: value 行（kind/mode/statement/ex_bad/ex_good/dedup/ref/confidence/evidence）。**不要直接修改规则库或数据库**。")
    lines.append("- 用户说“**重新读发行版**”：用 Read 工具重读本文件所在路径的最新内容并立即遵守。")
    return "\n".join(lines)


def current_ver() -> int:
    r = db.conn().execute("SELECT MAX(ver) v FROM digest_versions").fetchone()
    return r["v"] or 0


def render_all(force=False) -> dict:
    """渲染所有已接入项目的发行版 + 画像正文。返回 {ver, stale, bytes, projects}。"""
    cfg = config.load_settings()
    from . import repo
    st = repo.stats()
    active = st["active_rule_count"]
    if active > cfg["active_rule_cap"]:
        repo.log_error("renderer", f"active_rules_over_cap:{active}")

    ver = current_ver()
    nonce = secrets.token_hex(4)
    central = _central_section(ver + 1, nonce, st)
    size_guard = cfg["digest_max_bytes"]

    # 画像正文文件
    config.FULL.mkdir(parents=True, exist_ok=True)
    for p in st["profiles"]:
        body = [f"# {p['id']}", "", p["statement"], ""]
        if p["kind"] == "pattern":
            body.append("（模式性观察：概率性，引用时注明依据近期行为；被纠正立即降级。）")
        (config.FULL / f"{p['id']}.md").write_text("\n".join(body), "utf-8")

    connected = st["connected"]
    results = []
    over_limit = []
    for prj in connected:
        overlay = (prj.get("overlay_text") or "").strip()
        body = central + ("\n\n## 本项目侧重\n" + overlay if overlay else "")
        if len(body.encode("utf-8")) > size_guard:
            # 拒渲该项目、保留其旧版文件（宁可不更新，不可撑爆注入）。
            # 不再往 digest_versions 写同版本号的 stale 行——它随后必被中央行
            # INSERT OR REPLACE 覆盖（假记录），可见性由 errors:digest_over_limit 承担。
            over_limit.append(prj["path"])
            repo.log_error("renderer", "digest_over_limit",
                           f"{prj['path']} 发行版 {len(body.encode('utf-8'))}B 超上限")
            continue
        h = config.project_hash(prj["path"])
        (config.RELEASES / f"{h}.md").write_text(body, "utf-8")
        results.append(h)
    # 中央版（未接入项目手动粘贴 / 导出用）
    (config.RELEASES / "default.md").write_text(central, "utf-8")

    c = db.conn()
    c.execute("INSERT OR REPLACE INTO digest_versions(ver,rendered_at,bytes,est_tokens,"
              "rule_ids,nonce,stale) VALUES(?,?,?,?,?,?,0)",
              (ver + 1, db.now(), len(central.encode("utf-8")),
               len(central) // 3,
               ",".join(r["id"] for r in st["rules"] if r["status"] == "active"), nonce))
    c.commit()
    db.kv_set("digest_dirty", "")
    db.kv_set("digest_nonce", nonce)
    if len(central.encode("utf-8")) > size_guard:      # 无接入项目时也要可见（红线：注入体积）
        repo.log_error("renderer", "central_over_limit",
                       f"中央版 {len(central.encode('utf-8'))}B 超上限，请精简规则或归档")
    repo.event("render", detail={"ver": ver + 1, "projects": results,
                                 **({"over_limit": len(over_limit)} if over_limit else {})})
    return {"ver": ver + 1, "stale": bool(over_limit), "over_limit": over_limit,
            "projects": results, "bytes": len(central.encode())}


def ensure_current():
    """脏则重渲（server 轮询/审批后调用）。"""
    import threading
    lock = getattr(ensure_current, "_lock", None)
    if lock is None:
        lock = ensure_current._lock = threading.Lock()
    if not lock.acquire(blocking=False):
        return
    try:
        if db.kv_get("digest_dirty"):
            render_all()
    finally:
        lock.release()
