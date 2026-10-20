"""采集器（P1）：后台线程只读 tail 所有 CLI 实例的 transcript。
- 每 session 字节水位；半行容忍；uuid 去重（compact 重复追加 bug 的防御）
- usage 四字段聚合 → usage_daily（会话侧 token）
- 漂移探测器：未知行比例超阈 → 存样本 + 记错 + 重置
- 归档：空闲 ≥60s 的文件复制进 archive/（对抗 ~30 天自动清理）
- inbox 笔记 → 候选；项目发现（transcript 里的真实 cwd）→ 待确认
- 暂停期间（隐私阀）空转不解析。"""
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from . import config, db, parser, repo, filterpipe, connector


class Collector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="butler-collector")
        self.stop_evt = threading.Event()
        self.seen_uuids: dict[str, set] = {}
        self.drift_unknown = 0
        self.drift_total = 0
        self.last_expire = 0.0
        self.last_snapshot = 0.0

    # ---------- 主循环 ----------
    def run(self):
        db.init_db()
        connector.ensure_hook_bat()
        while not self.stop_evt.is_set():
            try:
                repo.heartbeat("collector")
                if not repo.is_paused():
                    self.sweep_once()
                    self.sweep_inbox()
                if time.time() - self.last_expire > 3600:
                    repo.passive_expire()
                    self.last_expire = time.time()
                if time.time() - self.last_snapshot > 86400:
                    self.daily_snapshot()
                    self.last_snapshot = time.time()
            except Exception as e:                       # D8：记录、上报、循环不死
                repo.log_error("collector", f"loop:{type(e).__name__}", str(e))
            time.sleep(config.load_settings()["poll_interval_sec"])

    def stop(self):
        self.stop_evt.set()

    # ---------- 一次全量扫描 ----------
    def sweep_once(self):
        if not config.CLAUDE_PROJECTS.is_dir():
            return
        today = datetime.now().strftime("%Y-%m-%d")
        usage_acc: dict[tuple, int] = {}
        for path in config.CLAUDE_PROJECTS.glob("*/*.jsonl"):
            try:
                self.process_file(path, usage_acc)
            except Exception as e:
                repo.log_error("collector", f"file:{type(e).__name__}", str(path))
        c = db.conn()
        for (day, proj), n in usage_acc.items():
            c.execute("INSERT INTO usage_daily(day,project,scope,tokens) VALUES(?,?, 'session',?)"
                      " ON CONFLICT(day,project,scope) DO UPDATE SET tokens=tokens+excluded.tokens",
                      (day, proj, n))
        c.commit()

    def process_file(self, path: Path, usage_acc: dict):
        sid = path.stem
        slug = path.parent.name
        srow = db.conn().execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
        if srow is None:
            db.conn().execute("INSERT INTO sessions(session_id,slug,project_path,"
                              "first_seen,last_event_at,total_lines) VALUES(?,?,?, '','',0)",
                              (sid, slug, ""))
            db.conn().commit()
            srow = db.conn().execute("SELECT * FROM sessions WHERE session_id=?",
                                     (sid,)).fetchone()
        if srow["is_internal"]:
            return
        offset, start_line = srow["file_offset"], srow["total_lines"] + 1
        entries, new_off, unknown, samples, next_line = \
            parser.read_increment(path, offset, start_line)
        if not srow["project_path"]:
            for e in entries:
                if e.get("cwd"):
                    proj = e["cwd"]
                    internal = proj.lower().startswith(str(config.DATA).lower()) or \
                               proj.lower().startswith(str(config.APP_ROOT).lower())
                    db.conn().execute("UPDATE sessions SET project_path=?, is_internal=? "
                                      "WHERE session_id=?", (proj, int(internal), sid))
                    db.conn().commit()
                    if internal:
                        return
                    break
        proj = srow["project_path"] or ""
        for e in entries:
            seen = self.seen_uuids.setdefault(sid, set())
            dup = bool(e["uuid"]) and e["uuid"] in seen          # compact 重复追加去重
            if e["uuid"]:
                seen.add(e["uuid"])
            if not dup:
                u = e["usage"]
                tok = u["in"] + u["cache_w"] + u["cache_r"] + u["out"]
                if tok:
                    day = (e.get("ts") or "")[:10]           # 按消息自身时间戳归日
                    if not re.match(r"\d{4}-\d{2}-\d{2}", day):
                        day = datetime.now().strftime("%Y-%m-%d")
                    key = (day, proj or slug)
                    usage_acc[key] = usage_acc.get(key, 0) + tok
                if e.get("compact") and e["kind"] == "system":
                    repo.event("compact", session=sid, project=proj,
                               detail={"line": e.get("line_no")})
            if e.get("cwd") and not proj:
                repo.project_seen(e["cwd"])
        # 仅在有新增内容时刷新活跃度（历史项目的已耗尽文件不再被每轮"续命"，
        # 从而随 3 天窗口自然滑出待确认队列）
        if proj and (entries or unknown):
            repo.project_seen(proj)
        self.drift_unknown += unknown
        self.drift_total += len(entries) + unknown
        if self.drift_total >= 200:
            ratio = self.drift_unknown / max(1, self.drift_total)
            if ratio > 0.05:
                self.save_drift_samples(path, samples)
            self.drift_unknown = self.drift_total = 0
        c = db.conn()
        # total_lines 用 next_line 绝对回写：空行/坏行同样计行号，杜绝证据回放行号漂移
        c.execute("UPDATE sessions SET file_offset=?, total_lines=?, "
                  "parse_errors=parse_errors+?, last_event_at=? WHERE session_id=?",
                  (new_off, next_line - 1, unknown, db.now(), sid))
        c.commit()
        self.maybe_archive(path, slug, sid, srow)

    def save_drift_samples(self, path, samples):
        if not samples:
            return
        fn = config.SAMPLES / f"drift-{datetime.now():%Y%m%d-%H%M%S}.txt"
        fn.write_text(f"source={path}\n" + "\n---\n".join(samples), "utf-8")
        repo.log_error("collector", "schema_drift", f"ratio>5% sample={fn.name}")
        repo.event("drift", detail={"sample": fn.name})

    # ---------- 归档 ----------
    def maybe_archive(self, path: Path, slug, sid, srow):
        try:
            st = path.stat()
        except OSError:
            return
        idle = time.time() - st.st_mtime
        dest = config.ARCHIVE / slug / path.name
        if idle > 60 and (not srow["archived"] or
                          (dest.exists() and dest.stat().st_size != st.st_size)):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            db.conn().execute("UPDATE sessions SET archived=1 WHERE session_id=?", (sid,))
            db.conn().commit()

    # ---------- inbox：会话→候选 ----------
    def sweep_inbox(self):
        for f in sorted(config.INBOX.glob("*.md")):
            if f.name.startswith("README"):
                continue
            try:
                self.ingest_note(f)
            except Exception as e:
                repo.log_error("collector", f"note:{type(e).__name__}", f.name)
            finally:
                try:
                    f.rename(f.with_suffix(f.suffix + ".done"))
                except OSError:
                    pass

    def ingest_note(self, f: Path):
        blocks, cur = [], {}
        for ln in f.read_text("utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                if cur:
                    blocks.append(cur); cur = {}
                continue
            m = parser_kv(ln)
            if m:
                k, v = m
                if k == "kind" and "kind" in cur:  # 新块开始（kind 重复出现）
                    blocks.append(cur); cur = {}
                cur[k] = v
        if cur:
            blocks.append(cur)
        for b in blocks:
            kind = b.get("kind", "rule")
            if kind not in ("rule", "profile", "filter"):
                continue
            stmt = b.get("statement", "").strip()
            if not stmt:
                continue
            payload = {"mode": b.get("mode", "forbid"), "statement": stmt,
                       "ex_bad": b.get("ex_bad", ""), "ex_good": b.get("ex_good", ""),
                       "kind": b.get("kind_type", "fact"),
                       "subject": b.get("subject", ""),
                       "pattern": b.get("pattern", ""), "category": b.get("category", "secret")}
            ev = []
            if b.get("evidence"):
                ev = [{"session": b.get("session", ""), "line": 0,
                       "ts": db.now(), "quote": b["evidence"][:300]}]
            dedup = b.get("dedup", "new")
            if dedup not in ("new", "violation_repeat", "duplicate"):
                dedup = "new"
            cid = repo.add_candidate(kind, payload, origin=f"note:{f.name}",
                                     evidence=ev, dedup=dedup,
                                     dedup_ref=b.get("ref", ""),
                                     confidence=b.get("confidence", "medium"))
            if kind == "rule" and dedup == "violation_repeat" and b.get("ref"):
                repo.bump_rule_stats(b["ref"], 1)
            repo.event("note_ingest", detail={"candidate": cid})

    def daily_snapshot(self):
        """sqlite3 Backup API，每日一份，保留 N 份。"""
        import sqlite3
        dest = config.SNAPS / f"snap-{datetime.now():%Y%m%d}.db"
        try:
            s = sqlite3.connect(str(config.DB_PATH))
            t = sqlite3.connect(str(dest))
            with t:
                s.backup(t)
            t.close(); s.close()
        except Exception as e:
            repo.log_error("collector", f"snapshot:{type(e).__name__}", str(e))
        # 保留 N 份 + 过期清理
        snaps = sorted(config.SNAPS.glob("snap-*.db"))
        keep = config.load_settings()["snapshot_keep"]
        for old in snaps[:-keep] if len(snaps) > keep else []:
            try:
                old.unlink()
            except OSError:
                pass
        cutoff = datetime.now() - timedelta(days=config.load_settings()["archive_keep_days"])
        for f in config.ARCHIVE.rglob("*.jsonl"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
            except OSError:
                pass


def parser_kv(line: str):
    import re
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*[:：]\s*(.+)$", line)
    if m:
        return m.group(1).lower(), m.group(2).strip()
    return None
