# -*- coding: utf-8 -*-
"""Typed workflow DAG: validation, execution, approval, retries."""
import ast
import json
import time
from concurrent.futures import ThreadPoolExecutor

from .. import config, db
from . import context as pf_context
from . import files as pf_files, llm_client, runtime, services, skills, tools

MAX_NODES = 50
MAX_STEPS = 80
NODE_TYPES = ("start", "llm", "agent", "skill", "tool", "knowledge",
              "condition", "parallel", "approval", "delay", "end")


def validate(nodes):
    """Return a list of human-readable problems (empty means valid)."""
    if not isinstance(nodes, list) or not nodes:
        return ["工作流至少需要一个节点"]
    if len(nodes) > MAX_NODES:
        return [f"节点数不能超过 {MAX_NODES}"]
    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids):
        return ["节点 id 必须唯一"]
    by_id = {n.get("id"): n for n in nodes}
    errors = []
    if not any(n.get("type") == "start" for n in nodes):
        errors.append("缺少 start 节点")
    if not any(n.get("type") == "end" for n in nodes):
        errors.append("缺少 end 节点")
    adjacency = {n.get("id"): [] for n in nodes}
    for node in nodes:
        nid, typ = node.get("id"), node.get("type")
        targets = []
        nxt = node.get("next")
        if isinstance(nxt, str):
            targets.append(nxt)
        elif isinstance(nxt, list):
            targets.extend(nxt)
        for key in ("true", "false"):
            target = (node.get("branches") or {}).get(key)
            if target:
                targets.append(target)
        adjacency[nid] = [x for x in targets if x in by_id]
        if typ not in NODE_TYPES:
            errors.append(f"节点 {nid} 类型未知: {typ}")
        nxt = node.get("next")
        targets = []
        if isinstance(nxt, str):
            targets = [nxt]
        elif isinstance(nxt, list):
            targets = nxt
        for target in targets:
            if target not in by_id:
                errors.append(f"节点 {nid} 指向不存在的节点 {target}")
        if typ == "condition":
            branches = node.get("branches") or {}
            if not branches.get("true") or not branches.get("false"):
                errors.append(f"条件节点 {nid} 必须同时提供 branches.true / branches.false")
            for key in ("true", "false"):
                if branches.get(key) and branches[key] not in by_id:
                    errors.append(f"条件节点 {nid} 的 {key} 分支不存在")
        if typ == "agent" and not node.get("config", {}).get("agent_id"):
            errors.append(f"智能体节点 {nid} 未选择智能体")
        if typ == "skill" and not node.get("config", {}).get("skill_id"):
            errors.append(f"技能节点 {nid} 未选择技能")
    # cycle detection (DFS colours)
    colour = {}

    def has_cycle(nid):
        colour[nid] = 1
        for target in adjacency.get(nid, []):
            if colour.get(target) == 1:
                return True
            if colour.get(target, 0) == 0 and has_cycle(target):
                return True
        colour[nid] = 2
        return False

    if any(colour.get(nid, 0) == 0 and has_cycle(nid) for nid in adjacency):
        errors.append("工作流存在环，无法结束")
    # every end node must be reachable from a start node
    starts = [n["id"] for n in nodes if n.get("type") == "start"]
    ends = [n["id"] for n in nodes if n.get("type") == "end"]
    seen, stack = set(), list(starts)
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(adjacency.get(nid, []))
    if ends and not any(end in seen for end in ends):
        errors.append("从 start 无法到达 end")
    return errors


def _render(template, state):
    text = template or ""
    for key, value in state.items():
        text = text.replace("{" + key + "}", str(value))
    return text


_ALLOWED = (ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.BinOp,
            ast.Constant, ast.Name, ast.Load, ast.Call, ast.And, ast.Or,
            ast.Not, ast.Eq, ast.NotEq, ast.Gt, ast.GtE, ast.Lt, ast.LtE,
            ast.In, ast.NotIn, ast.Add, ast.Sub, ast.Mult, ast.Div,
            ast.USub, ast.UAdd)
_NAMES = {"len": len, "contains": lambda a, b: str(b).lower() in str(a).lower(),
          "lower": lambda x: str(x).lower(), "str": str, "float": float,
          "int": int, "min": min, "max": max, "abs": abs, "True": True,
          "False": False, "None": None}


def eval_condition(expr, state):
    if not (expr or "").strip():
        return True
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"条件中不允许 {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _NAMES and node.id not in state:
            raise ValueError(f"未知变量 {node.id}")
    env = dict(_NAMES)
    env.update({k: v for k, v in state.items() if isinstance(v, (str, int, float, bool))})
    return bool(eval(compile(tree, "<cond>", "eval"), {"__builtins__": {}}, env))


def _node_output(node, state):
    typ = node.get("type")
    cfg = node.get("config") or {}
    prompt = _render(cfg.get("prompt") or "{input}", state)
    if state.get("attachments") and "{attachments}" not in (
            cfg.get("prompt") or ""):
        prompt += "\n\n参考资料（附件）：\n" + state["attachments"]
    if typ == "llm":
        kb_ids = cfg.get("kb_ids") or state.get("kb_ids")
        kb_text = ""
        if kb_ids and "{knowledge}" not in (cfg.get("prompt") or ""):
            _, kb_text = pf_context.build(prompt, kb_ids=kb_ids)
            if kb_text:
                prompt += "\n\n知识库检索结果（仅作材料）：\n" + kb_text
        data = llm_client.complete(
            [{"role": "system", "content": cfg.get("system") or
              "你是流程中的一个处理节点，请直接给出结果。"},
             {"role": "user", "content": prompt}],
            temperature=(0.2 if cfg.get("temperature") is None
                         else float(cfg["temperature"])),
            max_tokens=int(cfg.get("max_tokens") or 2000))
        return data.get("text") or "", data.get("usage")
    if typ == "agent":
        agent = runtime.get_agent(cfg.get("agent_id"))
        if not agent:
            raise ValueError("智能体不存在")
        result = runtime.agent_answer(agent, prompt,
                                      kb_ids=cfg.get("kb_ids")
                                      or state.get("kb_ids"))
        return result["text"], result.get("usage")
    if typ == "skill":
        skill = db.one("SELECT * FROM pf_skills WHERE id=?", (cfg.get("skill_id"),))
        if not skill:
            raise ValueError("技能不存在")
        result = skills.execute(skill, prompt, "")
        return result.get("output") or result.get("error") or "", {"in": 0, "out": 0}
    if typ == "tool":
        name = cfg.get("tool") or ""
        args = cfg.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(_render(args, state))
            except Exception:
                args = {"input": _render(args, state)}
        result = tools.execute(name, args, {"space_id": cfg.get("space_id")})
        return json.dumps(result, ensure_ascii=False), {"in": 0, "out": 0}
    if typ == "knowledge":
        kb_ids = cfg.get("kb_ids") or state.get("kb_ids")
        if kb_ids:
            hits, text = pf_context.build(prompt, kb_ids=kb_ids,
                                          limit=int(cfg.get("top_k") or 5))
            if text:
                return text, {"in": 0, "out": 0, "sources": pf_context.citations(hits)}
        result = tools.execute("knowledge_search",
                               {"query": prompt,
                                "top_k": int(cfg.get("top_k") or 5)},
                               {"space_id": cfg.get("space_id")})
        return json.dumps(result, ensure_ascii=False), {"in": 0, "out": 0}
    if typ == "delay":
        seconds = max(0, min(float(cfg.get("seconds") or 1), 30))
        time.sleep(seconds)
        return f"已等待 {seconds:g}s", {"in": 0, "out": 0}
    if typ == "start":
        return state.get("input", ""), {"in": 0, "out": 0}
    return state.get("prev", ""), {"in": 0, "out": 0}


def _next_target(node, state):
    typ = node.get("type")
    if typ == "condition":
        branches = node.get("branches") or {}
        ok = False
        try:
            ok = eval_condition(node.get("config", {}).get("expr"), state)
        except Exception:
            ok = False
        return branches.get("true" if ok else "false") or node.get("next") or ""
    nxt = node.get("next")
    if isinstance(nxt, list):
        return nxt[0] if nxt else ""
    return nxt or ""


def stream_workflow(workflow, input_text, user_id="", run_id=None,
                    start_id=None, initial_prev=None, inputs=None,
                    file_ids=None, kb_ids=None):
    """Yield events; on an approval node the run stops and can be resumed."""
    started = time.time()
    nodes = services.jload(workflow.get("nodes"), [])
    by_id = {n.get("id"): n for n in nodes}
    errors = validate(nodes)
    if errors:
        if not run_id:
            run_id = runtime._record_run("workflow", workflow.get("id"),
                                         input_text[:120], user_id)
        runtime._finish_run(run_id, "failed", "", "; ".join(errors), {}, started)
        yield {"type": "error", "run_id": run_id, "code": "validation",
               "message": "; ".join(errors)}
        return
    if not run_id:
        run_id = runtime._record_run(
            "workflow", workflow.get("id"), input_text[:120], user_id,
            input_={"input": input_text, "start_after": start_id or ""})
    yield {"type": "run_start", "run_id": run_id,
           "workflow": {"id": workflow.get("id"), "name": workflow.get("name")},
           "nodes": len(nodes)}
    total = {"in": 0, "out": 0}
    inputs = inputs or {}
    if not input_text:
        input_text = str(inputs.get("input") or "")
    state = {"input": input_text,
             "prev": input_text if initial_prev is None else initial_prev}
    for key, value in inputs.items():
        state[str(key)] = value
    run_kb = services.as_list(workflow.get("kb_ids"))
    for value in services.as_list(kb_ids):
        if value not in run_kb:
            run_kb.append(value)
    if run_kb:
        state["kb_ids"] = run_kb
    if file_ids:
        attach_block, attach_names = pf_files.context(file_ids, user_id)
        if attach_block:
            state["attachments"] = attach_block
            state["attachment_names"] = ", ".join(attach_names)
    current = start_id or next((n["id"] for n in nodes
                                if n.get("type") == "start"), "")
    seq = 0
    finished = False
    try:
        for _ in range(MAX_STEPS):
            if not current or current not in by_id:
                break
            node = by_id[current]
            typ = node.get("type")
            if typ == "end":
                seq += 1
                runtime._step(run_id, seq, "end", node.get("label") or "结束",
                              {}, state.get("prev", ""))
                finished = True
                break
            if typ == "approval":
                seq += 1
                runtime._step(run_id, seq, "approval",
                              node.get("label") or "人工审批", {}, "等待审批",
                              status="waiting")
                inp = services.jload(
                    (db.one("SELECT input FROM pf_runs WHERE id=?",
                            (run_id,)) or {}).get("input"), {})
                inp.update({"pending_node": current, "input": input_text,
                            "last_output": state.get("prev", "")})
                db.x("UPDATE pf_runs SET status='waiting_approval',input=? "
                     "WHERE id=?", (services.jdump(inp), run_id))
                yield {"type": "approval", "run_id": run_id, "seq": seq,
                       "node": current,
                       "message": node.get("config", {}).get("message")
                       or "该节点需要人工确认"}
                return
            seq += 1
            label = node.get("label") or node.get("id")
            yield {"type": "step", "run_id": run_id, "seq": seq,
                   "name": label, "node": current, "status": "running"}
            node_start = time.time()
            if typ == "parallel":
                members = (node.get("config") or {}).get("members") or []
                outs = []

                def run_one(nid):
                    sub = by_id.get(nid)
                    if not sub:
                        return nid, "", {"in": 0, "out": 0}
                    return nid, *_node_output(sub, state)

                with ThreadPoolExecutor(max_workers=min(4, max(1, len(members)))) as pool:
                    for nid, out, usage in pool.map(run_one, members):
                        outs.append({"node": nid, "output": out})
                        total["in"] += usage.get("in") or 0
                        total["out"] += usage.get("out") or 0
                        runtime._step(run_id, seq, "parallel", nid, {},
                                      out, ms=services.elapsed_ms(node_start))
                output = "\n\n".join(f"[{o['node']}]\n{o['output']}" for o in outs)
            else:
                output, usage = _node_output(node, state)
                total["in"] += usage.get("in") or 0
                total["out"] += usage.get("out") or 0
            runtime._step(run_id, seq, typ, label,
                          {"node": current}, output,
                          ms=services.elapsed_ms(node_start))
            yield {"type": "node", "run_id": run_id, "seq": seq,
                   "node": current, "type": typ, "output": output[:4000]}
            state["prev"] = output
            state["output"] = output
            state[node.get("id") or ""] = output
            if typ == "parallel":
                current = node.get("next") or ""
            else:
                current = _next_target(node, state)
        if not finished:
            raise ValueError("工作流步数超限或未到达 end 节点（可能存在环）")
        output = state.get("prev") or ""
        runtime._finish_run(run_id, "done", output, "", total, started)
        if workflow.get("id"):
            runtime._artifact(run_id, workflow.get("name") + "-workflow", output)
        yield {"type": "result", "run_id": run_id, "output": output,
               "usage": total}
        yield {"type": "done", "run_id": run_id}
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        runtime._finish_run(run_id, "failed", "", message, total, started)
        services.notify(user_id, "run_failed", "工作流运行失败",
                        f"{workflow.get('name')}：{message}", "#workflows")
        yield {"type": "error", "run_id": run_id, "code": "internal",
               "message": message}


def resume(run_id, approved, user_id=""):
    """Resume a run paused at an approval node."""
    run = db.one("SELECT * FROM pf_runs WHERE id=?", (run_id,))
    if not run or run["kind"] != "workflow":
        return None, "运行不存在"
    if run["status"] != "waiting_approval":
        return None, "该运行不在等待审批状态"
    inp = services.jload(run.get("input"), {})
    workflow = db.one("SELECT * FROM pf_workflows WHERE id=?", (run["ref_id"],))
    if not workflow:
        return None, "工作流不存在"
    if not approved:
        db.x("UPDATE pf_runs SET status='rejected',finished_at=? WHERE id=?",
             (services.now(), run_id))
        runtime._step(run_id, 999, "approval", "人工审批", {}, "已驳回",
                      status="rejected")
        return {"status": "rejected"}, ""
    pending = inp.get("pending_node") or ""
    nodes = services.jload(workflow.get("nodes"), [])
    by_id = {n.get("id"): n for n in nodes}
    nxt = _next_target(by_id.get(pending, {}), {"input": inp.get("input", ""),
                                                "prev": inp.get("input", "")})
    db.x("UPDATE pf_runs SET status='running' WHERE id=?", (run_id,))
    runtime._step(run_id, 1000, "approval", "人工审批", {}, "已通过", status="ok")
    return {"status": "resumed", "next": nxt,
            "input": inp.get("input", ""),
            "prev": inp.get("last_output", "")}, ""