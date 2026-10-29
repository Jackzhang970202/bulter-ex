# -*- coding: utf-8 -*-
"""Minimal 5-field cron scheduler running in one daemon thread."""
import datetime as dt
import threading

from .. import db
from . import runtime, services, workflows

_thread = None
_lock = threading.Lock()

FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31),
          ("month", 1, 12), ("weekday", 0, 6))


def _parse_field(text, lo, hi):
    text = (text or "*").strip()
    values = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = max(1, int(step_text))
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        values.update(range(start, end + 1, step))
    if not values or any(v < lo or v > hi for v in values):
        raise ValueError(f"cron 字段超出范围: {text}")
    return values


def parse(cron):
    parts = (cron or "").split()
    if len(parts) != 5:
        raise ValueError("cron 需要 5 个字段：分 时 日 月 周")
    out = []
    for part, (_, lo, hi) in zip(parts, FIELDS):
        out.append(_parse_field(part, lo, hi))
    return out


def compute_next(cron, after=None):
    minutes, hours, days, months, weekdays = parse(cron)
    current = (after or dt.datetime.now()).replace(second=0, microsecond=0)
    current += dt.timedelta(minutes=1)
    day_restricted = days != set(range(1, 32))
    dow_restricted = weekdays != set(range(0, 7))
    for _ in range(60 * 24 * 366):
        dom_ok = current.day in days
        # Python Monday=0; cron Sunday=0, Monday=1.
        dow_ok = ((current.weekday() + 1) % 7) in weekdays
        # Cron semantics: when both day-of-month and day-of-week are
        # restricted, either match is enough; otherwise both must match
        # (a wildcard is unrestricted and therefore always true).
        if day_restricted and dow_restricted:
            day_ok = dom_ok or dow_ok
        else:
            day_ok = dom_ok and dow_ok
        if (current.minute in minutes and current.hour in hours
                and day_ok and current.month in months):
            return current
        current += dt.timedelta(minutes=1)
    raise ValueError("无法计算下次运行时间（cron 可能永不触发）")


def create(name, kind, ref_id, cron, enabled=True, catch_up=False):
    parse(cron)  # validate early
    sid = services.uid("sc_")
    nxt = compute_next(cron).strftime("%Y-%m-%d %H:%M:%S")
    db.x("INSERT INTO pf_schedules(id,name,kind,ref_id,cron,enabled,catch_up,"
         "last_run,next_run,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (sid, name, kind, ref_id, cron, 1 if enabled else 0,
          1 if catch_up else 0, "", nxt, services.now()))
    return db.one("SELECT * FROM pf_schedules WHERE id=?", (sid,))


def run_schedule(schedule):
    """Execute one schedule synchronously; returns run result."""
    started = services.now()
    db.x("UPDATE pf_schedules SET last_run=?,next_run=? WHERE id=?",
         (started, compute_next(schedule["cron"]).strftime("%Y-%m-%d %H:%M:%S"),
          schedule["id"]))
    run_id = ""
    record_id = services.uid("sr_")
    db.x("INSERT INTO pf_schedule_runs(id,schedule_id,run_id,status,started_at) "
         "VALUES(?,?,?,?,?)", (record_id, schedule["id"], "", "running", started))
    try:
        payload = f"[定时任务] {schedule['name']}"
        if schedule["kind"] == "agent":
            result = runtime.run_sync("agent", schedule["ref_id"], payload,
                                      user_id="system")
        elif schedule["kind"] == "workflow":
            wf = db.one("SELECT * FROM pf_workflows WHERE id=?",
                        (schedule["ref_id"],))
            events = list(workflows.stream_workflow(wf, payload,
                                                    user_id="system"))
            result = {"ok": not any(e.get("type") == "error" for e in events),
                      "run_id": next((e.get("run_id") for e in events
                                      if e.get("run_id")), ""),
                      "output": next((e.get("output") for e in events
                                      if e.get("type") == "result"), ""),
                      "error": next((e.get("message") for e in events
                                     if e.get("type") == "error"), "")}
        else:
            result = {"ok": False, "error": "不支持的调度类型", "run_id": ""}
        run_id = result.get("run_id") or ""
        db.x("UPDATE pf_schedule_runs SET run_id=?,status=?,finished_at=? "
             "WHERE id=?",
             (run_id, "done" if result.get("ok") else "failed",
              services.now(), record_id))
        return result
    except Exception as exc:
        db.x("UPDATE pf_schedule_runs SET status=?,finished_at=? WHERE id=?",
             ("failed", services.now(), record_id))
        return {"ok": False, "error": str(exc), "run_id": run_id}


def _tick():
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = db.q("SELECT * FROM pf_schedules WHERE enabled=1 AND next_run!='' "
                "AND next_run<=? LIMIT 5", (now,))
    for row in rows:
        # claim by advancing next_run before executing (single scheduler process)
        try:
            nxt = compute_next(row["cron"]).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            db.x("UPDATE pf_schedules SET enabled=0 WHERE id=?", (row["id"],))
            continue
        db.x("UPDATE pf_schedules SET next_run=? WHERE id=?",
             (nxt, row["id"]))
        threading.Thread(target=run_schedule, args=(row,), daemon=True).start()


def _loop():
    import time
    while True:
        try:
            _tick()
        except Exception:
            pass
        time.sleep(20)


def start():
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="pf-scheduler",
                                   daemon=True)
        _thread.start()