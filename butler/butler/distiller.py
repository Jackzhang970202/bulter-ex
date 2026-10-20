"""蒸馏引擎（P3）：手动触发；独立无头 claude -p 进程；对活跃会话零写入。
水位快照式并发：启动瞬间固定各会话终点，进行中新内容滚入下轮。
输入 = 材料逐条脱敏后拼装；预算切片，超量滚存。输出 = 候选（带证据索引映射）。"""
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from . import config, db, parser, repo, filterpipe, renderer

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_thread: threading.Thread | None = None
_cancel = threading.Event()             # 用户取消标志：短路回退重试与后续阶段
_cur_run: int | None = None             # 当前 run 行 id：崩溃收尾也能落库


def _kill_tree(p: subprocess.Popen):
    """杀掉 shell=True 起的整棵进程树（否则 claude.exe 成孤儿继续烧钱）。"""
    import os
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            p.kill()
    except (OSError, subprocess.TimeoutExpired):
        try:
            p.kill()
        except OSError:
            pass

PROMPT_TMPL = """你是"经验管家"的复盘蒸馏器。阅读【材料】（已脱敏的历史 AI 协作会话片段，每行以 [索引] 角色: 内容 开头），对照【当前规则库/画像库】，提炼值得沉淀的经验。

在最后一个 ```json 代码块中只输出一个 JSON 对象：
{"candidates":[{"kind":"rule|profile|filter","mode":"forbid|require","statement":"单行祈使句","ex_bad":"反例","ex_good":"正例","kind_type":"fact|pattern","subject":"对象代号","pattern":"正则","category":"类别","dedup":"new|violation_repeat|duplicate","ref":"如T7","confidence":"high|medium|low","refs":[材料索引数字],"note":"依据一句话"}],"metrics":{"summary":"本期观察"}}

硬性要求：
1. 每条候选必须能指认材料出处（refs≥1 个索引），禁止编造；没有值得沉淀的就返回空 candidates。
2. 与已有规则同义但被反复违反 → dedup=violation_repeat + ref=编号，并给出**更具体可判断**的改写 statement；绝不新增近义条目。
3. statement 要能判断"违反了没有"；空泛表述（如"减少AI味"）必须拆成具体禁止项，否则放弃。
4. rule 必须附 ex_bad/ex_good 一对例子，给不出就放弃该候选。
5. profile 是观察（用户行为/协作对象偏好），kind_type 按需选 fact|pattern；陈述不含真实姓名与贬义评价（用"某领导/A类汇报"式表述）。
6. filter 候选：材料中若出现现有过滤规则未覆盖的敏感信息形态，提议正则（宁缺毋滥）。
7. 最多 8 条，按价值排序。

【当前规则库】
{rules}

【当前画像库】
{profiles}

【现有过滤类别】{filters}

【材料】
{material}
"""

JSON_FENCE = re.compile(r"```json\s*([\s\S]*?)```", re.I)


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def start() -> tuple[bool, str]:
    global _thread
    with _lock:
        if is_running():
            return False, "already_running"
        _cancel.clear()
        _thread = threading.Thread(target=_run_guarded, name="butler-distill", daemon=True)
        _thread.start()
        return True, "started"


def cancel():
    """置取消位并杀整棵进程树；distilling 旗标由 _run_guarded 的 finally 统一清除
    （此处不清，避免"界面显示空闲但候选还在冒"的假象）。"""
    _cancel.set()
    p = _proc
    if p and p.poll() is None:
        _kill_tree(p)
    if not is_running():
        # 没有活线程收尾（陈旧旗标/恰好在两次运行之间）：就地清，防 UI 卡"取消中"
        db.kv_set("distilling", "")
        db.kv_set("distill_progress", "")
        db.kv_set("distill_started_at", "")
    repo.event("distill_cancel")


def _run_guarded():
    global _cur_run
    db.kv_set("distilling", "1")
    db.kv_set("distill_started_at", db.now())
    db.kv_set("distill_progress", "准备中")
    try:
        _run()
    except Exception as e:
        repo.log_error("distiller", f"crash:{type(e).__name__}", str(e))
        # 带上 run_id：更新已有 running 行为 error，而不是再造一条无主行
        _finish(status="error", run_id=_cur_run, note=f"crash:{type(e).__name__}:{str(e)[:160]}")
    finally:
        _cur_run = None
        _cancel.clear()
        db.kv_set("distilling", "")
        db.kv_set("distill_progress", "")
        _cleanup_old()


def _claude_bin() -> str:
    import os
    return os.environ.get("BUTLER_CLAUDE_BIN") or shutil.which("claude") or ""


class Assembler:
    """把各会话 [consumed, snapshot_end) 区间拼成带全局索引的脱敏材料。
    预算按字符计；截断只发生在"某条文本放不进"时——之前所有行（含非文本行）
    计为已消费（推进 byte+line），未放进行及其后留到下轮。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.budget = cfg["distill_max_input_chars"]
        self.used = 0
        self.idx = 0
        self.parts: list[str] = []
        self.plan: dict[str, dict] = {}       # sid → {last_byte, consumed_lines, complete}
        self.indexmap: dict[int, dict] = {}   # idx → evidence
        self.rolled = 0
        self.stopped = False

    def feed_session(self, sid, path, start_off, end_off, start_line, proj):
        entries, _, _, _, _ = parser.read_increment(path, start_off, start_line)
        entries = [e for e in entries if e.get("byte_off", 0) <= end_off]   # 钳制到快照终点
        sess_lines, consumed_bytes, consumed_lines = [], start_off, start_line - 1
        for e in entries:
            keep = e["kind"] in ("user", "assistant") and bool(e["text"])
            if keep:
                masked, cats = filterpipe.mask_text(e["text"][:2000])
                if cats:
                    filterpipe.record_audit(sid, e.get("line_no", 0), cats, run_id="distill")
                line = f"[{self.idx}] {e['role']}: {masked}\n"
                if self.used + len(line) > self.budget:
                    self.stopped = True
                    break
                sess_lines.append(line)
                self.indexmap[self.idx] = {"session": sid, "line": e.get("line_no", 0),
                                           "ts": e.get("ts", ""), "quote": masked[:160]}
                self.idx += 1
                self.used += len(line)
            consumed_bytes = e.get("byte_off", consumed_bytes)
            consumed_lines = e.get("line_no", consumed_lines)
        if sess_lines:
            self.parts.append(f"### session={sid} project={proj}\n" + "".join(sess_lines))
        complete = not self.stopped
        self.plan[sid] = {"last_byte": consumed_bytes, "consumed_lines": consumed_lines,
                          "complete": complete, "end_off": end_off,
                          "fed_bytes": consumed_bytes - start_off}
        if self.stopped:
            self.rolled += max(0, end_off - consumed_bytes)

    @property
    def material(self) -> str:
        return "\n".join(self.parts)


def _run():
    cfg = config.load_settings()
    c = db.conn()

    # 1) 水位快照
    jobs = []
    missing = 0
    for row in c.execute("SELECT * FROM sessions WHERE is_internal=0 AND "
                         "file_offset>consumed_offset ORDER BY project_path, session_id"):
        if repo.is_paused():
            _finish(status="ok", note="采集暂停中，本轮未开始")
            return
        path = None
        for p in config.CLAUDE_PROJECTS.glob(f"*/{row['session_id']}.jsonl"):
            path = p
            break
        if path is None:
            a = config.ARCHIVE / row["slug"] / f"{row['session_id']}.jsonl"
            path = a if a.exists() else None
        if path is None:
            # 源文件已被官方清理且未及归档 → 视为已消费，不再永久卡"未消化增量"
            c.execute("UPDATE sessions SET consumed_offset=file_offset, "
                      "consumed_lines=total_lines WHERE session_id=?", (row["session_id"],))
            missing += 1
            continue
        jobs.append((row["session_id"], path, row["consumed_offset"], row["file_offset"],
                     row["consumed_lines"] + 1, row["project_path"] or row["slug"]))
    if missing:
        c.commit()
        repo.event("distill_phase", detail={"stage": "missing_consumed", "sessions": missing})
    if not jobs:
        _finish(status="ok", note="无新内容（水位即最新）", candidates=0)
        return
    db.kv_set("distill_progress", f"快照完成 · {len(jobs)} 个待消化会话")
    repo.event("distill_phase", detail={"stage": "snapshot", "sessions": len(jobs)})

    # 2) 材料（脱敏 + 预算 + 索引）
    asm = Assembler(cfg)
    for j, job in enumerate(jobs, 1):
        if asm.stopped:
            asm.plan[job[0]] = {"last_byte": job[2], "consumed_lines": job[4] - 1,
                                "complete": False, "end_off": job[3], "fed_bytes": 0}
            asm.rolled += job[3] - job[2]
            continue
        asm.feed_session(*job)
        db.kv_set("distill_progress",
                  f"脱敏组装 {j}/{len(jobs)} 会话 · 已 {asm.used // 1000}K 字符")
    if not asm.material.strip():
        _finish(status="ok", note="材料脱敏后为空", candidates=0)
        return

    rules = [dict(r) for r in c.execute("SELECT id,mode,statement FROM rules "
                                        "WHERE status<>'archived'")]
    profiles = [dict(r) for r in c.execute("SELECT id,kind,statement FROM profiles "
                                           "WHERE status='active'")]
    filters = ", ".join({r["category"] for r in c.execute(
        "SELECT category FROM filter_rules WHERE status='active'")}) or "（空）"
    prompt = (PROMPT_TMPL
              .replace("{rules}", json.dumps(rules, ensure_ascii=False))
              .replace("{profiles}", json.dumps(profiles, ensure_ascii=False))
              .replace("{filters}", filters)
              .replace("{material}", asm.material))

    # 3) 无头执行（prompt 走 stdin，规避命令行长度限制）
    global _cur_run
    run_id = c.execute("INSERT INTO distill_runs(started,status,input_chars)"
                       " VALUES(?,?,?)", (db.now(), "running", len(prompt))).lastrowid
    _cur_run = run_id
    c.commit()
    config.RUNS.mkdir(parents=True, exist_ok=True)
    (config.RUNS / f"run{run_id}.prompt.txt").write_text(prompt, "utf-8")
    db.kv_set("distill_progress", f"材料 {len(asm.material)} 字符 · 调用 claude -p")
    out = _call_claude(prompt, str(config.RUNS))
    if _cancel.is_set():
        _finish(status="cancelled", run_id=run_id, note="用户取消（本轮不推进水位）")
        return
    text = (out or {}).get("result") or ""
    usage = (out or {}).get("usage") or {}
    tokens = sum(v for v in usage.values() if isinstance(v, int))
    cost = (out or {}).get("total_cost_usd")
    if out is None:
        _finish(status="error", run_id=run_id, note="claude -p 失败（errors 表可查）")
        return

    # 4) 解析候选；失败用同一材料重试一次并强化格式约束
    #   （旧话术"请只重发上次输出"无效——claude -p 是无上下文新会话，根本看不到上次输出）
    data = _extract_json(text)
    if data is None:
        out2 = _call_claude(prompt + "\n\n【格式硬约束】不要任何解释文字。最后一个 "
                            "```json 代码块内有且只有一个 JSON 对象"
                            "（candidates 可以是空数组）。", str(config.RUNS))
        if out2:
            u2 = out2.get("usage") or {}
            tokens += sum(v for v in u2.values() if isinstance(v, int))
            if isinstance(out2.get("total_cost_usd"), (int, float)):
                cost = (cost or 0) + out2["total_cost_usd"]
        data = _extract_json((out2 or {}).get("result", ""))
    if data is None:
        _finish(status="error", run_id=run_id, tokens=tokens, cost=cost,
                note="输出两次不可解析；水位未推进")
        return

    # 5) 入库候选
    n = 0
    for cand in data.get("candidates", []) or []:
        kind = cand.get("kind")
        stmt = (cand.get("statement") or "").strip()
        pat = (cand.get("pattern") or "").strip()
        if kind == "rule" and not (stmt and (cand.get("ex_bad") or cand.get("ex_good"))):
            continue
        if kind == "profile" and not stmt:
            continue
        if kind == "filter" and not pat:
            continue
        if kind not in ("rule", "profile", "filter"):
            continue
        evidence = [asm.indexmap[i] for i in cand.get("refs", [])
                    if isinstance(i, int) and i in asm.indexmap]
        payload = {k: cand.get(k, "") for k in
                   ("mode", "statement", "ex_bad", "ex_good", "kind_type",
                    "subject", "pattern", "category", "note")}
        if payload["mode"] not in ("forbid", "require"):
            payload["mode"] = "forbid"
        if payload["kind_type"] not in ("fact", "pattern"):
            payload["kind_type"] = "fact"
        payload["kind"] = payload["kind_type"]        # decide 晋升时读 kind，双键对齐
        dedup = cand.get("dedup", "new")
        if dedup not in ("new", "violation_repeat", "duplicate"):
            dedup = "new"
        cid = repo.add_candidate(kind, payload, origin=f"distill#{run_id}",
                                 evidence=evidence, dedup=dedup,
                                 dedup_ref=(cand.get("ref") or "").strip(),
                                 confidence=cand.get("confidence", "medium"))
        if cid:
            n += 1
        if kind == "rule" and dedup == "violation_repeat" and cand.get("ref"):
            repo.bump_rule_stats(str(cand["ref"]).strip(), 1)

    # 6) 推进水位（仅推进已消费区间；截断点之后留滚存）
    for sid, info in asm.plan.items():
        advanced = info["complete"] or info.get("fed_bytes", 0) > 0
        target = info["end_off"] if info["complete"] else info["last_byte"]
        if advanced and target > 0:
            c.execute("UPDATE sessions SET consumed_offset=?, consumed_lines=? "
                      "WHERE session_id=? AND consumed_offset<?",
                      (target, info["consumed_lines"], sid, target))
    c.commit()

    summary = (data.get("metrics") or {}).get("summary", "")
    _finish(status="ok", run_id=run_id, tokens=tokens, cost=cost, candidates=n,
            rolled=asm.rolled, note=summary[:300] or ("有滚存" if asm.rolled else ""))
    repo.event("distill_done", detail={"run": run_id, "candidates": n,
                                       "rolled_chars": asm.rolled})
    try:
        renderer.ensure_current()
    except Exception as e:
        repo.log_error("distiller", "ensure_render_failed", str(e))


def stream_consume(lines, on_event):
    """逐行解析 stream-json 事件；坏行容忍。返回最终 result 事件 dict（或 None）。"""
    result = None
    for raw in lines:
        raw = (raw or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        on_event(ev)
        if ev.get("type") == "result":
            result = ev
    return result


class _Cancelled(Exception):
    pass


def _call_claude(prompt: str, cwd: str, timeout: int = 1800):
    """优先 stream-json（实时心跳进度）；仅当流模式【瞬间失败】才回退纯 json。
    已经流出过内容再断（含用户取消）一律不重跑——回退重跑＝同一材料花两遍钱。"""
    global _proc
    bin_ = _claude_bin()
    if not bin_:
        repo.log_error("distiller", "claude_not_found",
                       "PATH 中无 claude；可设 BUTLER_CLAUDE_BIN")
        return None
    import os
    import time as _t
    env = dict(os.environ, CLAUDE_ENTRYPOINT="butler-distiller")
    stats = {"msgs": 0, "chars": 0}

    def on_ev(ev):
        if ev.get("type") == "assistant":
            m = ev.get("message") or {}
            txt = "".join(b.get("text", "") for b in (m.get("content") or [])
                          if isinstance(b, dict) and b.get("type") == "text")
            stats["msgs"] += 1
            stats["chars"] += len(txt)
            db.kv_set("distill_progress",
                      f"模型输出中 · {stats['msgs']} 条消息 · {stats['chars'] // 1000}K 字符")

    def spawn(fmt):
        cmd = f'"{bin_}" -p --output-format {fmt}' + (" --verbose" if fmt == "stream-json" else "")
        return subprocess.Popen(cmd, shell=True, cwd=cwd, env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8")

    # 第一遍：stream-json
    try:
        _proc = spawn("stream-json")
        err_buf: list[str] = []

        def _drain():     # stderr 持续排空：管道缓冲写满会吊死子进程（stream 模式从不读它）
            try:
                for ln in _proc.stderr:
                    if len(err_buf) < 50:
                        err_buf.append(ln)
            except (OSError, ValueError):
                pass

        threading.Thread(target=_drain, daemon=True, name="butler-stderr").start()
        _proc.stdin.write(prompt)
        _proc.stdin.close()
        t0 = _t.time()

        def guarded():
            for line in _proc.stdout:
                if _cancel.is_set():
                    _kill_tree(_proc)
                    raise _Cancelled()
                if _t.time() - t0 > timeout:
                    raise subprocess.TimeoutExpired("", timeout)
                yield line

        result = stream_consume(guarded(), on_ev)
        rc = _proc.wait(timeout=30)
        _proc = None
        if result:
            return result
        if rc != 0:
            repo.log_error("distiller", f"stream_exit_{rc}", "".join(err_buf)[:200])
        if stats["msgs"] > 0 or stats["chars"] > 0:
            return None                     # 出过流却没走到 result：不重跑（防双花）
    except _Cancelled:
        _proc = None
        return None
    except subprocess.TimeoutExpired:
        if _proc:
            _kill_tree(_proc)
        _proc = None
        repo.log_error("distiller", "timeout_1800s")
        return None
    except OSError as e:
        _proc = None
        repo.log_error("distiller", f"spawn:{e}")

    # 第二遍回退：仅限 stream 模式瞬间失败，且用户未取消
    if _cancel.is_set():
        return None
    try:
        db.kv_set("distill_progress", "回退 json 模式重试")
        _proc = spawn("json")
        out, err = _proc.communicate(input=prompt, timeout=timeout)
        rc = _proc.returncode
        _proc = None
        if rc != 0:
            repo.log_error("distiller", f"exit_{rc}", (err or "")[:200])
            return None
        d = json.loads(out)
        return d if isinstance(d, dict) else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        if _proc and isinstance(e, subprocess.TimeoutExpired):
            _kill_tree(_proc)
        _proc = None
        repo.log_error("distiller", f"fallback:{type(e).__name__}", str(e)[:200])
        return None


def _extract_json(text: str):
    blocks = JSON_FENCE.findall(text or "")
    raw = blocks[-1] if blocks else (text or "")
    raw = raw.strip()
    if not raw.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        raw = m.group(0)
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get("candidates"), list) else None


def _finish(status="ok", run_id=None, tokens=0, cost=0, candidates=0,
            rolled=0, note=""):
    c = db.conn()
    if run_id:
        c.execute("UPDATE distill_runs SET finished=?, status=?, input_tokens=?, "
                  "cost_usd=?, candidates_count=?, rolled_chars=?, note=? WHERE id=?",
                  (db.now(), status, tokens, cost or 0, candidates, rolled, note, run_id))
    else:
        c.execute("INSERT INTO distill_runs(started,finished,status,note)"
                  " VALUES(?,?,?,?)", (db.now(), db.now(), status, note))
    c.commit()
    if tokens:
        c.execute("INSERT INTO usage_daily(day,project,scope,tokens) VALUES(?,?,'distill',?)"
                  " ON CONFLICT(day,project,scope) DO UPDATE SET tokens=tokens+excluded.tokens",
                  (datetime.now().strftime("%Y-%m-%d"), "(蒸馏)", tokens))
        c.commit()
    db.kv_set("distill_progress", "")
    db.kv_set("distill_started_at", "")
    db.kv_set("digest_dirty", db.now())


def _cleanup_old():
    cutoff = datetime.now() - timedelta(days=7)
    for f in list(config.RUNS.glob("*.txt")) + list(config.SANITIZED.glob("*")):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except OSError:
            pass
