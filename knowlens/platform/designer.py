# -*- coding: utf-8 -*-
"""Conversational designer: turn a natural-language goal into a draft.

The model only proposes a draft. Validation and persistence stay in the normal
platform APIs; nothing is published automatically.
"""
import json

from . import llm_client, services, workflows

WORKFLOW_SYSTEM = """你是知鉴平台的流程设计师。根据用户目标，输出一个可运行的工流 JSON。
只输出 JSON，不要 markdown，不要解释。结构：
{"name":"名称","description":"一句话","summary":"用人话说明流程步骤",
 "questions":["最多3个仍需确认的问题"],
 "input_schema":[{"key":"input","label":"主题","type":"textarea","required":true}],
 "nodes":[
   {"id":"start","type":"start","label":"开始","next":"n1"},
   {"id":"n1","type":"knowledge","label":"检索","config":{"top_k":5,"space_id":""},"next":"n2"},
   {"id":"n2","type":"llm","label":"总结","config":{"prompt":"根据资料总结：{input}"},"next":"end"},
   {"id":"end","type":"end","label":"结束"}
 ]}
规则：
- 必须有 start 和 end；节点 3-10 个；next 指向存在的 id。
- 可用类型：llm / agent / skill / tool / knowledge / condition / parallel / approval / delay。
- 智能体节点 config.agent_id 只能取给定列表；技能节点同理。
- 条件节点 config.expr 用 prev/input 变量，branches.true / branches.false 指向节点。
- 审批节点用于需要人确认的步骤。
- 不要编造不存在的 agent_id / skill_id。"""

AGENT_SYSTEM = """你是知鉴平台的智能体设计师。根据用户目标输出智能体配置 JSON。
只输出 JSON，不要 markdown。结构：
{"name":"名称","title":"标题","description":"简介","category":"general",
 "system_prompt":"角色设定与回答要求","tool_ids":["..."],"skill_ids":["..."],
 "tags":["..."],"summary":"一句话说明","questions":["最多3个待确认问题"]}
tool_ids / skill_ids 只能从给定列表选择；system_prompt 写清楚职责、依据和输出格式。"""

TEAM_SYSTEM = """你是知鉴平台的团队设计师。根据用户目标输出团队配置 JSON。
只输出 JSON，不要 markdown。结构：
{"name":"名称","description":"一句话","strategy":"roundtable|supervisor|handoff",
 "members":[{"agent_id":"...","role":"角色"}],"max_turns":4,
 "summary":"一句话说明协作方式","questions":["最多3个待确认问题"]}
members 只能使用给定智能体列表中的 id；2-4 名成员；strategy 按任务选择。"""


def db_agents():
    from .. import db
    return [dict(r) for r in db.q(
        "SELECT id,name,title,description,status FROM pf_agents "
        "ORDER BY updated_at DESC LIMIT 40")]


def _skills():
    from .. import db
    return [dict(r) for r in db.q(
        "SELECT id,name,kind FROM pf_skills WHERE enabled=1 ORDER BY name")]


def _questions(value):
    if not isinstance(value, list):
        return []
    return [str(x)[:160] for x in value if str(x).strip()][:3]


def _llm_json(system, user, retry_error=""):
    prompt = user
    if retry_error:
        prompt += "\n\n上一次输出无法使用：" + retry_error + "\n请重新输出合法 JSON。"
    out = llm_client.complete([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ], temperature=0.2, max_tokens=3000)
    return llm_client.parse_json(out.get("text") or ""), out


def draft(kind, prompt, answers=None, current=None):
    answers = answers or {}
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "请描述你要完成的事"}
    agents = db_agents()
    skills = _skills()
    agent_list = "\n".join(
        f"- {a['id']} | {a['name']} | {a.get('description') or ''}"[:180]
        for a in agents)
    skill_list = "\n".join(
        f"- {s['id']} | {s['name']} | {s['kind']}" for s in skills)
    extra = ""
    if answers:
        extra = "\n用户补充：" + json.dumps(answers, ensure_ascii=False)
    if current:
        extra += "\n当前草稿（在此基础上修改）：" + json.dumps(
            current, ensure_ascii=False)[:6000]

    if kind == "workflow":
        user = (f"用户目标：{prompt}{extra}\n\n可用智能体：\n{agent_list or '无'}"
                f"\n\n可用技能：\n{skill_list or '无'}")
        obj, _ = _llm_json(WORKFLOW_SYSTEM, user)
        return _validate_workflow(obj)
    if kind == "agent":
        user = (f"用户目标：{prompt}{extra}\n\n可用工具："
                f"{', '.join(t['id'] for t in tools_list())}\n\n可用技能：\n"
                f"{skill_list or '无'}")
        obj, _ = _llm_json(AGENT_SYSTEM, user)
        return _validate_agent(obj, skills)
    if kind == "team":
        user = (f"用户目标：{prompt}{extra}\n\n可用智能体：\n{agent_list or '无'}")
        obj, _ = _llm_json(TEAM_SYSTEM, user)
        return _validate_team(obj, agents)
    return {"ok": False, "error": "只支持 workflow / agent / team"}


def tools_list():
    from . import tools as tool_registry
    return tool_registry.list_tools()


def _validate_workflow(obj):
    if not isinstance(obj, dict):
        return {"ok": False, "error": "模型没有返回 JSON 草稿"}
    nodes = obj.get("nodes")
    if not isinstance(nodes, list):
        return {"ok": False, "error": "草稿缺少 nodes", "draft": obj}
    errors = workflows.validate(nodes)
    if errors:
        return {"ok": False, "error": "; ".join(errors), "draft": obj}
    return {"ok": True, "kind": "workflow", "draft": {
        "name": str(obj.get("name") or "AI 创建的流程")[:60],
        "description": str(obj.get("description") or "")[:300],
        "nodes": nodes,
        "input_schema": obj.get("input_schema")
        if isinstance(obj.get("input_schema"), list) else [
            {"key": "input", "label": "输入", "type": "textarea",
             "required": True}],
    }, "summary": str(obj.get("summary") or "")[:800],
        "questions": _questions(obj.get("questions"))}


def _validate_agent(obj, skills):
    if not isinstance(obj, dict):
        return {"ok": False, "error": "模型没有返回 JSON 草稿"}
    valid_skills = {s["id"] for s in skills}
    valid_tools = {t["id"] for t in tools_list()}
    return {"ok": True, "kind": "agent", "draft": {
        "name": str(obj.get("name") or "AI 创建的智能体")[:60],
        "title": str(obj.get("title") or obj.get("name") or "")[:60],
        "description": str(obj.get("description") or "")[:300],
        "category": str(obj.get("category") or "general")[:40],
        "system_prompt": str(obj.get("system_prompt") or "")[:8000],
        "tool_ids": [x for x in (obj.get("tool_ids") or [])
                     if x in valid_tools][:12],
        "skill_ids": [x for x in (obj.get("skill_ids") or [])
                      if x in valid_skills][:12],
        "tags": services.parse_tags(obj.get("tags")),
    }, "summary": str(obj.get("summary") or "")[:800],
        "questions": _questions(obj.get("questions"))}


def _validate_team(obj, agents):
    if not isinstance(obj, dict):
        return {"ok": False, "error": "模型没有返回 JSON 草稿"}
    names = {a["id"]: a["name"] for a in agents}
    valid = set(names)
    members = [{"agent_id": m.get("agent_id"),
                "agent_name": names.get(m.get("agent_id"), ""),
                "role": str(m.get("role") or "")[:40]}
               for m in (obj.get("members") or [])
               if isinstance(m, dict) and m.get("agent_id") in valid]
    if not members:
        return {"ok": False, "error": "草稿没有可用的团队成员", "draft": obj}
    strategy = obj.get("strategy")
    if strategy not in ("roundtable", "supervisor", "handoff"):
        strategy = "roundtable"
    return {"ok": True, "kind": "team", "draft": {
        "name": str(obj.get("name") or "AI 创建的团队")[:60],
        "description": str(obj.get("description") or "")[:300],
        "strategy": strategy,
        "members": members[:4],
        "max_turns": max(1, min(int(obj.get("max_turns") or 4), 8)),
        "synthesizer_id": obj.get("synthesizer_id")
        if obj.get("synthesizer_id") in valid else "",
    }, "summary": str(obj.get("summary") or "")[:800],
        "questions": _questions(obj.get("questions"))}

CHAT_RULES = """你在与用户多轮对话，逐步完善草稿。每轮只输出 JSON：
{"reply":"给用户的简短回复，可提问，最多3个问题","changes":["本轮具体改动，1-4条"],
 "ready":true/false,"draft":{...}}
reply 用中文，说人话；不要输出 JSON 以外的内容。
草稿结构沿用对应类型；只有草稿可用时 ready=true。
如果用户只是提问/补充信息，可以返回当前草稿并把 changes 设为 []。
"""

CHAT_WORKFLOW = CHAT_RULES + """工作流 draft：
{"name","description","summary","questions",
 "input_schema":[{"key","label","type","required"}],
 "nodes":[{"id","type","label","config","next","branches"}]}
节点 3-10 个，必须有 start/end；类型 llm/agent/skill/tool/knowledge/condition/parallel/approval/delay；
condition 必须有 branches.true 和 branches.false；agent/skill 只能使用给定 id。
"""

CHAT_AGENT = CHAT_RULES + """智能体 draft：
{"name","title","description","category","system_prompt","tool_ids","skill_ids","tags","summary","questions"}
tool_ids/skill_ids 只能从给定列表选。
"""

CHAT_TEAM = CHAT_RULES + """团队 draft：
{"name","description","strategy":"roundtable|supervisor|handoff",
 "members":[{"agent_id","role"}],"max_turns","synthesizer_id","summary","questions"}
members 只能使用给定智能体；2-4 人。
"""


def _chat_context(kind):
    agents = db_agents()
    skills = _skills()
    agent_list = "\n".join(
        f"- {a['id']} | {a['name']} | {(a.get('description') or '')[:120]}"
        for a in agents)
    skill_list = "\n".join(
        f"- {s['id']} | {s['name']} | {s['kind']}" for s in skills)
    tool_list = ", ".join(t["id"] for t in tools_list())
    if kind == "workflow":
        return f"\n可用智能体：\n{agent_list or '无'}\n\n可用技能：\n{skill_list or '无'}"
    if kind == "agent":
        return f"\n可用工具：{tool_list}\n\n可用技能：\n{skill_list or '无'}"
    return f"\n可用智能体：\n{agent_list or '无'}"


def chat(kind, messages, draft=None):
    if kind not in ("workflow", "agent", "team"):
        return {"ok": False, "error": "只支持 workflow / agent / team"}
    if not isinstance(messages, list) or not messages:
        return {"ok": False, "error": "缺少对话内容"}
    system = {"workflow": CHAT_WORKFLOW, "agent": CHAT_AGENT,
              "team": CHAT_TEAM}[kind]
    history = "\n".join(
        f"{'用户' if m.get('role') == 'user' else 'AI'}："
        f"{str(m.get('content') or '')[:1200]}"
        for m in messages[-10:])
    payload = (f"{_chat_context(kind)}\n\n对话记录：\n{history}")
    if draft:
        payload += ("\n\n当前草稿：\n"
                    + json.dumps(draft, ensure_ascii=False)[:7000])
    out = llm_client.complete([
        {"role": "system", "content": system},
        {"role": "user", "content": payload},
    ], temperature=0.3, max_tokens=3200)
    obj = llm_client.parse_json(out.get("text") or "")
    if not isinstance(obj, dict):
        return {"ok": False, "error": "AI 没有返回可解析的草稿",
                "reply": "我没有理解这次修改，请换一种说法再试一次。"}
    reply = str(obj.get("reply") or "我已经根据你的描述更新了草稿。")[:1500]
    changes = [str(x)[:120] for x in (obj.get("changes") or [])
               if str(x).strip()][:5]
    ready = bool(obj.get("ready"))
    new_draft = obj.get("draft") if isinstance(obj.get("draft"), dict) else None
    if not new_draft:
        return {"ok": True, "reply": reply, "draft": draft or {},
                "changes": changes, "ready": False,
                "questions": _questions(obj.get("questions"))}
    if kind == "workflow":
        checked = _validate_workflow(new_draft)
    elif kind == "agent":
        checked = _validate_agent(new_draft, _skills())
    else:
        checked = _validate_team(new_draft, db_agents())
    if not checked.get("ok"):
        return {"ok": True,
                "reply": reply + "\n\n草稿还没通过校验：" + str(
                    checked.get("error") or ""),
                "draft": draft or {}, "changes": changes, "ready": False,
                "questions": _questions(obj.get("questions"))}
    return {"ok": True, "reply": reply, "draft": checked["draft"],
            "changes": changes, "ready": bool(ready or checked["draft"]),
            "questions": _questions(obj.get("questions"))}
