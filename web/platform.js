/* KnowLens platform UI: agents, skills, experts, teams, workflows, automation, ops.
 * Loaded after app.js; reuses its helpers (api, toast, modal, esc, charts). */
/* global api, toast, openModal, closeModal, state, initChart, disposeCharts */

window.KP = (function () {
  "use strict";

  const VIEWS = ["agents", "skills", "experts", "teams", "workflows",
    "automation", "ops", "designer", "kb2", "models"];
  const S = {
    agents: [], installed: [], catalog: [], catTotal: 0, catOffset: 0,
    experts: [], teams: [], workflows: [], schedules: [], tools: [],
    spaces: [], libs: [], users: [], audits: [], artifacts: [],
    overview: null, reviews: null, catQ: "", catCat: "", catVerified: false,
    skillTab: "installed", opsTab: "overview", autoTab: "schedules",
    agentFilter: { q: "", status: "" },
  };

  const E = (s) => String(s === undefined || s === null ? "" : s).replace(
    /[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;" }[c]));
  const el = (id) => document.getElementById(id);
  const val = (id) => { const n = el(id); return n ? n.value.trim() : ""; };
  const arr = (v) => (Array.isArray(v) ? v : []);
  const fmt = (t) => (t ? String(t).slice(5, 16) : "—");
  const num = (n) => Number(n || 0).toLocaleString("zh-CN");
  const json = (v, d) => { try { return JSON.parse(v); } catch (e) { return d; } };

  function badge(status) {
    const map = { published: ["ok", "已发布"], draft: ["gray", "草稿"],
      review: ["warn", "待审核"], testing: ["info", "测试中"],
      offline: ["err", "已下线"], installed: ["ok", "已安装"],
      disabled: ["gray", "已停用"], ready: ["ok", "就绪"],
      running: ["info", "运行中"], done: ["ok", "完成"],
      failed: ["err", "失败"], waiting_approval: ["warn", "待审批"],
      processing: ["info", "解析中"],
      cancelled: ["gray", "已取消"], pending: ["warn", "待审核"],
      active: ["ok", "生效"], rejected: ["err", "已驳回"] };
    const [cls, text] = map[status] || ["gray", status || "未知"];
    return `<span class="badge ${cls}">${E(text)}</span>`;
  }

  function skeleton() {
    return '<div class="kp-skeleton"></div><div class="kp-skeleton" style="width:70%"></div><div class="kp-skeleton" style="width:45%"></div>';
  }

  function empty(title, hint) {
    return `<div class="kp-empty"><b>${E(title)}</b>${E(hint || "")}</div>`;
  }

  function head(title, actions) {
    return `<div class="kp-head"><div><b style="font-size:15px">${E(title)}</b></div>
      <div class="spacer"></div>${actions || ""}</div>`;
  }

  function debounce(fn, ms) {
    let t = null;
    return function () { clearTimeout(t); t = setTimeout(() => fn.apply(this, arguments), ms); };
  }

  async function stream(path, body, onEvent) {
    const resp = await fetch(path, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) });
    if (!resp.ok) {
      let msg = resp.status + " " + resp.statusText;
      try { msg = (await resp.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          try { onEvent(JSON.parse(line.slice(6))); } catch (e) {}
        }
      }
    }
  }

  function makeTrace(host, opts) {
    const options = opts || {};
    host.innerHTML = '<div class="kp-console"><div class="answer"></div></div>';
    const box = host.querySelector(".kp-console");
    const answerEl = box.querySelector(".answer");
    let answer = "";
    let nodeEl = null;
    function line(label, text, cls) {
      const row = document.createElement("div");
      row.className = "ev " + (cls || "");
      row.innerHTML = `<b>${E(label)}</b><span></span>`;
      row.querySelector("span").textContent = text || "";
      box.insertBefore(row, answerEl);
      box.scrollTop = box.scrollHeight;
      return row;
    }
    return {
      event(ev) {
        const t = ev.type;
        if (t === "run_start") line("开始", `运行 ${ev.run_id || ""}`, "");
        else if (t === "step") line("步骤", ev.name || "", "");
        else if (t === "node") line("节点", `${ev.node || ""}: ${String(ev.output || "").slice(0, 240)}`, "");
        else if (t === "handoff") line("交接", `${(ev.agent || {}).name || ""}: ${String(ev.output || "").slice(0, 200)}`, "");
        else if (t === "tool_call") line("工具", `${ev.name} ${JSON.stringify(ev.arguments || {})}`.slice(0, 300), "");
        else if (t === "tool_result") line("结果", String(ev.output || "").slice(0, 400), "");
        else if (t === "approval") {
          line("审批", ev.message || "等待人工确认", "");
          if (options.onApproval) options.onApproval(ev);
        } else if (t === "delta") {
          answer += ev.text || "";
          answerEl.textContent = answer;
          box.scrollTop = box.scrollHeight;
        } else if (t === "result") {
          answer = ev.output || answer;
          answerEl.textContent = answer;
        } else if (t === "error") {
          line("错误", ev.message || "", "err");
        } else if (t === "done") {
          line("完成", "运行结束", "");
        }
      },
      text() { return answer; },
    };
  }

  function openDrawerLike(title, html, foot) {
    openModal(title, html, foot || '<button class="btn" onclick="closeModal()">关闭</button>');
  }

  function options(list, selected, valueKey, labelKey) {
    return list.map((x) => {
      const v = valueKey ? x[valueKey] : x;
      const label = labelKey ? x[labelKey] : x;
      return `<option value="${E(v)}" ${String(v) === String(selected) ? "selected" : ""}>${E(label)}</option>`;
    }).join("");
  }

  async function ensureSpaces() {
    if (!S.spaces.length) {
      try {
        const rows = await api("/api/spaces");
        S.spaces = rows;
      } catch (e) { S.spaces = []; }
    }
    return S.spaces;
  }

  async function ensureTools() {
    if (!S.tools.length) {
      try { S.tools = (await api("/api/platform/tools")).items; } catch (e) { S.tools = []; }
    }
    return S.tools;
  }

  /* ══════════════════════════ agents ══════════════════════════ */

  async function renderAgents() {
    const v = el("view");
    v.innerHTML = `${head("智能体", `
      <div class="kp-search"><span>⌕</span><input id="kp-agent-q" placeholder="搜索名称、描述、标签"></div>
      <select id="kp-agent-status" class="btn sm" style="padding:7px 10px">
        <option value="">全部状态</option><option value="draft">草稿</option>
        <option value="review">待审核</option><option value="published">已发布</option>
        <option value="offline">已下线</option></select>
      <button class="btn sm" onclick="KP.startDesigner('agent')">AI 创建</button>
      <button class="btn sm" onclick="KP.agentTemplates()">从模板创建</button>
      <button class="btn primary sm" onclick="KP.agentForm()">＋ 新建智能体</button>`)}
      <div id="kp-agent-list">${skeleton()}</div>`;
    try {
      S.agents = (await api("/api/platform/agents")).items;
    } catch (e) { el("kp-agent-list").innerHTML = empty("加载失败", e.message); return; }
    const paint = () => {
      const q = val("kp-agent-q").toLowerCase();
      const st = val("kp-agent-status");
      const rows = S.agents.filter((a) =>
        (!st || a.status === st) &&
        (!q || (a.name + " " + a.title + " " + a.description + " " +
          arr(a.tags).join(" ")).toLowerCase().includes(q)));
      el("kp-agent-list").innerHTML = rows.length ? `<div class="kp-grid">${rows.map(agentCard).join("")}</div>`
        : empty("还没有符合条件的智能体", "新建一个，或调整筛选条件");
    };
    el("kp-agent-q").oninput = debounce(paint, 200);
    el("kp-agent-status").onchange = paint;
    paint();
  }

  function agentCard(a) {
    const st = a.stats || {};
    return `<div class="kp-card">
      <div class="kp-card-top"><div class="kp-avatar">${E(a.avatar || "🤖")}</div>
        <div style="flex:1;min-width:0"><h4>${E(a.name)}</h4>
          <div class="kp-meta">${E(a.title || "")}</div></div>${badge(a.status)}</div>
      <p>${E(a.description || "暂无描述")}</p>
      <div class="kp-card-foot">
        ${arr(a.tags).slice(0, 3).map((t) => `<span class="kp-tag">${E(t)}</span>`).join("")}
        <span class="spacer" style="flex:1"></span>
        <span class="kp-meta">${num(st.runs)} 次 · v${a.version || 0}</span>
      </div>
      <div class="kp-card-foot">
        <button class="btn sm primary" onclick="KP.useTarget('agent','${a.id}')">对话</button>
        <button class="btn sm" onclick="KP.agentDetail('${a.id}')">详情</button>
        <button class="btn sm ghost" onclick="KP.agentTest('${a.id}')">测试</button>
      </div></div>`;
  }

  async function agentForm(id, preset) {
    const [spaces, tools] = await Promise.all([ensureSpaces(), ensureTools()]);
    let skills = [];
    try { skills = (await api("/api/platform/skills")).items; } catch (e) {}
    const a = id ? await api(`/api/platform/agents/${id}`) : (preset || {
      name: "", title: "", description: "", avatar: "🤖", category: "general",
      system_prompt: "", model: "", temperature: 0.2, max_steps: 6,
      space_id: state.space || (spaces[0] || {}).id || "", library_ids: [],
      skill_ids: [], tool_ids: [], memory_enabled: true, tags: [],
    });
    if (!S.libs.length && a.space_id) {
      try { S.libs = (await api(`/api/spaces/${a.space_id}/libraries`)); } catch (e) {}
    }
    const libs = a.space_id === state.space ? (state.libs || []) : S.libs;
    const html = `<div class="kp-form">
      <div class="row2">
        <label>名称<input id="kf-name" value="${E(a.name)}" placeholder="例如：制度问答助手"></label>
        <label>简称/标题<input id="kf-title" value="${E(a.title)}"></label>
      </div>
      <label>描述<textarea id="kf-desc" placeholder="这个智能体解决什么问题">${E(a.description)}</textarea></label>
      <label>角色设定（系统提示词）<textarea id="kf-prompt" style="min-height:140px" placeholder="你是一名…">${E(a.system_prompt)}</textarea></label>
      <div class="row2">
        <label>模型（留空用默认通道）<input id="kf-model" value="${E(a.model)}" placeholder="qwen3.8-flash"></label>
        <label>温度<input id="kf-temp" type="number" min="0" max="1.5" step="0.1" value="${E(a.temperature)}"></label>
      </div>
      <div class="row2">
        <label>最大推理步数<input id="kf-steps" type="number" min="1" max="12" value="${E(a.max_steps)}"></label>
        <label>分类<input id="kf-cat" value="${E(a.category)}"></label>
      </div>
      <div class="row2">
        <label>知识空间<select id="kf-space">
          <option value="">不绑定知识</option>${options(spaces, a.space_id, "id", "name")}
        </select></label>
        <label>标签（逗号分隔）<input id="kf-tags" value="${E(arr(a.tags).join(", "))}"></label>
      </div>
      <label>知识库范围（不选则整个空间）<div class="kp-checks" id="kf-libs">
        ${libs.map((l) => `<label><input type="checkbox" value="${E(l.id)}"
          ${arr(a.library_ids).includes(l.id) ? "checked" : ""}> ${E(l.name)}</label>`).join("") || '<span class="kp-meta">当前空间没有库</span>'}
      </div></label>
      <label>技能（可选，最多注入 8 个）<div class="kp-checks">
        ${skills.map((s) => `<label><input type="checkbox" value="${E(s.id)}"
          ${arr(a.skill_ids).includes(s.id) ? "checked" : ""}> ${E(s.name)}</label>`).join("") || '<span class="kp-meta">尚未安装技能</span>'}
      </div></label>
      <label>工具权限<div class="kp-checks">
        ${tools.map((t) => `<label><input type="checkbox" value="${E(t.id)}"
          ${arr(a.tool_ids).includes(t.id) ? "checked" : ""}> ${E(t.name)}
          <span class="kp-meta">${E(t.risk)}</span></label>`).join("")}
      </div></label>
      <label style="display:flex;align-items:center;gap:8px">
        <input type="checkbox" id="kf-mem" ${a.memory_enabled ? "checked" : ""}> 启用记忆注入</label>
    </div>`;
    openModal(id ? "编辑智能体" : "新建智能体", html,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.saveAgent('${id || ""}')">保存</button>`);
    el("kf-space").onchange = async (ev) => {
      const sid = ev.target.value;
      const box = el("kf-libs");
      if (!sid) { box.innerHTML = '<span class="kp-meta">未绑定空间</span>'; return; }
      try {
        const rows = await api(`/api/spaces/${sid}/libraries`);
        box.innerHTML = rows.map((l) => `<label><input type="checkbox" value="${E(l.id)}"> ${E(l.name)}</label>`).join("") || '<span class="kp-meta">该空间没有库</span>';
      } catch (e) { box.innerHTML = '<span class="kp-meta">库加载失败</span>'; }
    };
  }

  function checkedValues(selector) {
    return Array.from(document.querySelectorAll(selector + " input:checked"))
      .map((n) => n.value);
  }

  async function saveAgent(id) {
    const payload = {
      name: val("kf-name"), title: val("kf-title"), description: val("kf-desc"),
      system_prompt: val("kf-prompt"), model: val("kf-model"),
      temperature: Number(val("kf-temp") || 0.2),
      max_steps: Number(val("kf-steps") || 6), category: val("kf-cat") || "general",
      space_id: val("kf-space"), tags: val("kf-tags"),
      library_ids: checkedValues("#kf-libs"),
      skill_ids: checkedValues("#kf-skills") ,
      tool_ids: checkedValues("#kf-tools"),
      memory_enabled: el("kf-mem") ? el("kf-mem").checked : true,
    };
    // checkboxes have no wrapper ids; collect by section order
    const boxes = document.querySelectorAll(".kp-checks");
    if (boxes.length >= 3) {
      payload.library_ids = Array.from(boxes[0].querySelectorAll("input:checked")).map((n) => n.value);
      payload.skill_ids = Array.from(boxes[1].querySelectorAll("input:checked")).map((n) => n.value);
      payload.tool_ids = Array.from(boxes[2].querySelectorAll("input:checked")).map((n) => n.value);
    }
    if (!payload.name) return toast("名称必填", "err");
    try {
      if (id) await api(`/api/platform/agents/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      else await api("/api/platform/agents", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      closeModal(); toast(id ? "已保存" : "已创建", "ok");
      if (state.view === "agents") renderAgents();
    } catch (e) { toast(e.message, "err"); }
  }


  async function agentTemplates() {
    let items = [];
    try { items = (await api("/api/platform/agents/templates")).items; } catch (e) {}
    openDrawerLike("从模板创建智能体", `<div class="kp-list">
      ${items.map((t) => `<div class="kp-row"><div class="kp-avatar" style="width:34px;height:34px;font-size:16px">${E(t.avatar)}</div>
        <div><b>${E(t.name)}</b><small>${E(t.description)}</small></div>
        <span class="spacer"></span>
        <button class="btn sm primary" onclick="KP.useAgentTemplate('${t.id}')">使用</button></div>`).join("")}
      </div>`, `<button class="btn" onclick="closeModal()">关闭</button>`);
    S.agentTemplates = items;
  }

  function useAgentTemplate(id) {
    const t = (S.agentTemplates || []).find((x) => x.id === id);
    if (!t) return;
    closeModal();
    agentForm("", { ...t, space_id: state.space || "", library_ids: [],
      skill_ids: [], memory_enabled: true, temperature: 0.2, max_steps: 6 });
  }

  async function agentDetail(id) {
    const a = await api(`/api/platform/agents/${id}`);
    const st = a.stats || {};
    const versions = await api(`/api/platform/agents/${id}/versions`);
    const actions = [];
    if (a.status === "draft") actions.push(["submit", "提交审核"]);
    if (a.status === "review") actions.push(["approve", "审核通过"], ["reject", "驳回"]);
    if (a.status === "testing") actions.push(["publish", "发布"]);
    if (a.status === "published") actions.push(["offline", "下线"]);
    if (a.status === "offline") actions.push(["reopen", "重新编辑"]);
    openDrawerLike(`智能体 · ${a.name}`, `
      <div class="kp-kpis">
        <div class="kp-kpi"><b>${num(st.runs)}</b><small>运行次数</small></div>
        <div class="kp-kpi"><b>${(st.success_rate * 100).toFixed(0)}%</b><small>成功率</small></div>
        <div class="kp-kpi"><b>${num(st.tokens_in + st.tokens_out)}</b><small>Token</small></div>
        <div class="kp-kpi"><b>v${a.version || 0}</b><small>版本</small></div>
      </div>
      <div class="kp-drawer-sec"><h5>状态</h5>${badge(a.status)}
        ${actions.map(([act, label]) => `<button class="btn sm" onclick="KP.agentAction('${id}','${act}')">${label}</button>`).join(" ")}</div>
      <div class="kp-drawer-sec"><h5>角色设定</h5><div class="kp-pre">${E(a.system_prompt || "（空）")}</div></div>
      <div class="kp-drawer-sec"><h5>绑定</h5>
        <div class="kp-meta">空间：${E((S.spaces.find((s) => s.id === a.space_id) || {}).name || "未绑定")}</div>
        <div class="kp-meta">技能：${E(arr(a.skill_names).join("、") || "无")}</div>
        <div class="kp-meta">工具：${E(arr(a.tools).map((t) => t.name).join("、") || "无")}</div>
        <div class="kp-meta">标签：${E(arr(a.tags).join("、") || "无")}</div></div>
      <div class="kp-drawer-sec"><h5>版本</h5>
        ${arr(versions.items).length ? versions.items.map((v) => `<div class="kp-row"><b>v${v.version}</b>
          <small>${fmt(v.created_at)}</small><span class="spacer"></span>
          <button class="btn sm ghost" onclick="KP.compareVersion('${id}',${v.version})">对比</button>
          <button class="btn sm ghost" onclick="KP.agentRollback('${id}',${v.version})">回滚</button></div>`).join("")
          : '<div class="kp-meta">尚无发布版本</div>'}</div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn" onclick="KP.agentTest('${id}')">测试</button>
       <button class="btn" onclick="KP.openAgentEvals('${id}')">评测</button>
       <button class="btn" onclick="KP.aiEditAgent('${id}')">AI 修改</button>
       <button class="btn primary" onclick="KP.useTarget('agent','${id}')">对话</button>
       <button class="btn danger" onclick="KP.agentDelete('${id}')">删除</button>`);
  }

  async function agentAction(id, action) {
    try {
      await api(`/api/platform/agents/${id}/transition`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }) });
      toast("状态已更新", "ok");
      closeModal();
      if (state.view === "agents") renderAgents();
      agentDetail(id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function agentRollback(id, version) {
    try {
      await api(`/api/platform/agents/${id}/rollback`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version }) });
      toast("已回滚到 v" + version, "ok"); closeModal();
      if (state.view === "agents") renderAgents();
    } catch (e) { toast(e.message, "err"); }
  }

  async function agentDelete(id) {
    if (!confirm("删除后不可恢复，确认删除该智能体？")) return;
    try {
      await api(`/api/platform/agents/${id}`, { method: "DELETE" });
      toast("已删除", "ok"); closeModal();
      if (state.view === "agents") renderAgents();
    } catch (e) { toast(e.message, "err"); }
  }

  function agentTest(id) {
    openDrawerLike("智能体测试", `
      <div class="kp-form"><label>测试输入<textarea id="kp-test-q" style="min-height:90px"
        placeholder="输入一个真实问题">你能做什么？</textarea></label>
      <div id="kp-test-trace"></div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.runAgentTest('${id}')">运行</button>`);
  }

  async function runAgentTest(id) {
    const q = val("kp-test-q");
    if (!q) return toast("请输入测试内容", "err");
    const host = el("kp-test-trace");
    const trace = makeTrace(host);
    try {
      await stream(`/api/platform/agents/${id}/run`, { message: q }, (ev) => trace.event(ev));
    } catch (e) { trace.event({ type: "error", message: e.message }); }
  }

  function openAgentChat(id) {
    openDrawerLike("与智能体对话", `
      <div class="kp-chat" id="kp-chat-log"></div>
      <div class="kp-chat-input">
        <textarea id="kp-chat-q" placeholder="Enter 发送，Shift+Enter 换行"></textarea>
        <button class="btn primary" onclick="KP.sendAgentChat('${id}')">发送</button>
      </div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>`);
    const ta = el("kp-chat-q");
    ta.onkeydown = (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendAgentChat(id); }
    };
  }

  async function sendAgentChat(id) {
    const q = val("kp-chat-q");
    if (!q || S.chatBusy) return;
    S.agentSession = S.agentSession || {};
    S.chatBusy = true;
    const log = el("kp-chat-log");
    log.insertAdjacentHTML("beforeend", `<div class="kp-msg user">${E(q)}</div>`);
    const bubble = document.createElement("div");
    bubble.className = "kp-msg assistant";
    bubble.textContent = "…";
    log.appendChild(bubble);
    el("kp-chat-q").value = "";
    log.scrollTop = log.scrollHeight;
    let text = "";
    try {
      await stream(`/api/platform/agents/${id}/run`,
        { message: q, session_id: S.agentSession[id] || "" },
        (ev) => {
          if (ev.type === "run_start" && ev.session_id) S.agentSession[id] = ev.session_id;
          if (ev.type === "delta") { text += ev.text || ""; bubble.textContent = text; }
          else if (ev.type === "result") { text = ev.output || text; bubble.textContent = text; }
          else if (ev.type === "error") { bubble.textContent = "错误：" + ev.message; }
          log.scrollTop = log.scrollHeight;
        });
    } catch (e) { bubble.textContent = "错误：" + e.message; }
    S.chatBusy = false;
  }

  async function openAgentEvals(id) {
    let sets = { items: [] };
    try { sets = await api(`/api/platform/agents/${id}/evals`); } catch (e) {}
    openDrawerLike("智能体评测", `
      <div class="kp-form"><label>评测集名称<input id="kp-eval-name" value="回归评测"></label>
      <label>用例（JSON 数组：[{q, expect:[关键词]}]）<textarea id="kp-eval-cases"
        style="min-height:140px">[{"q":"介绍一下你能做什么","expect":[]}]</textarea></label></div>
      <div class="kp-drawer-sec"><h5>已有评测集</h5>
      ${arr(sets.items).map((s) => `<div class="kp-row"><b>${E(s.name)}</b>
        <small>${arr(s.cases).length} 条</small><span class="spacer"></span>
        <button class="btn sm" onclick="KP.runEval('${id}','${s.id}')">运行</button></div>`).join("") || '<div class="kp-meta">暂无</div>'}</div>
      <div id="kp-eval-out"></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.createEval('${id}')">创建评测集</button>`);
  }

  async function createEval(id) {
    let cases;
    try { cases = JSON.parse(val("kp-eval-cases")); } catch (e) { return toast("用例 JSON 格式错误", "err"); }
    try {
      await api(`/api/platform/agents/${id}/evals`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: val("kp-eval-name") || "评测集", cases }) });
      toast("评测集已创建", "ok");
      openAgentEvals(id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function runEval(id, eid) {
    const host = el("kp-eval-out");
    host.innerHTML = '<div class="kp-meta">运行中…</div>';
    try {
      const r = await api(`/api/platform/agents/${id}/evals/${eid}/run`, { method: "POST" });
      host.innerHTML = `<div class="kp-drawer-sec"><h5>得分 ${(r.score * 100).toFixed(0)}%</h5>
        ${r.detail.map((d) => `<div class="kp-row"><b>${d.ok ? "✓" : "✗"}</b>
          <small>${E(d.question)}</small><span class="spacer"></span>
          <small>${E(d.matched.join(","))}</small></div>`).join("")}</div>`;
    } catch (e) { host.innerHTML = `<div class="kp-meta">${E(e.message)}</div>`; }
  }

  /* ══════════════════════════ skills ══════════════════════════ */

  async function renderSkills() {
    const v = el("view");
    v.innerHTML = `${head("技能市场", `
      <button class="btn sm" onclick="KP.skillCustom()">＋ 自定义技能</button>
      <button class="btn sm" onclick="KP.skillImport()">导入链接</button>
      <button class="btn primary sm" onclick="KP.skillTab('catalog')">浏览开源目录</button>`)}
      <div class="kp-tabs">
        <button class="kp-tab ${S.skillTab === "installed" ? "on" : ""}" onclick="KP.skillTab('installed')">已安装</button>
        <button class="kp-tab ${S.skillTab === "catalog" ? "on" : ""}" onclick="KP.skillTab('catalog')">开源技能目录</button>
        <button class="kp-tab ${S.skillTab === "packs" ? "on" : ""}" onclick="KP.skillTab('packs')">技能包</button>
        <button class="kp-tab ${S.skillTab === "mcp" ? "on" : ""}" onclick="KP.skillTab('mcp')">MCP 服务</button>
      </div>
      <div id="kp-skill-body">${skeleton()}</div>`;
    if (S.skillTab === "installed") await paintInstalled();
    else if (S.skillTab === "packs") await paintPacks();
    else if (S.skillTab === "mcp") await paintMcp();
    else await paintCatalog(true);
  }

  function skillTab(tab) { S.skillTab = tab; renderSkills(); }

  async function paintInstalled() {
    const host = el("kp-skill-body");
    try { S.installed = (await api("/api/platform/skills")).items; }
    catch (e) { host.innerHTML = empty("加载失败", e.message); return; }
    host.innerHTML = S.installed.length ? `<div class="kp-grid">${S.installed.map(skillCard).join("")}</div>`
      : empty("还没有安装技能", "从开源目录安装，或创建自定义技能");
  }

  function skillCard(s) {
    return `<div class="kp-card">
      <div class="kp-card-top"><div class="kp-avatar">🧩</div>
        <div style="flex:1;min-width:0"><h4>${E(s.name)}</h4>
          <div class="kp-meta">${E(s.category)} · ${E(s.kind)}</div></div>
        ${badge(s.enabled ? "installed" : "disabled")}</div>
      <p>${E(s.description || "暂无描述")}</p>
      <div class="kp-card-foot"><span class="kp-meta">${num(s.body_chars)} 字</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn sm" onclick="KP.skillDetail('${s.id}')">详情</button>
        <button class="btn sm ghost" onclick="KP.skillToggle('${s.id}',${s.enabled ? 0 : 1})">${s.enabled ? "停用" : "启用"}</button>
        <button class="btn sm" onclick="KP.skillRun('${s.id}')">运行</button>
        <button class="btn sm ghost" onclick="KP.skillDelete('${s.id}')">卸载</button>
      </div></div>`;
  }

  async function paintCatalog(reset) {
    const host = el("kp-skill-body");
    if (reset) {
      S.catOffset = 0; S.catalog = [];
      host.innerHTML = `<div class="kp-head">
        <div class="kp-search"><span>⌕</span><input id="kp-cat-q" value="${E(S.catQ)}" placeholder="搜索 1100+ 开源技能"></div>
        <select id="kp-cat-cat" class="btn sm" style="padding:7px 10px"><option value="">全部分类</option></select>
        <label class="kp-meta" style="display:flex;gap:6px;align-items:center">
          <input type="checkbox" id="kp-cat-verified" ${S.catVerified ? "checked" : ""}> 仅宽松许可</label>
        <span class="kp-meta" id="kp-cat-count">加载中…</span>
        <button class="btn sm ghost" onclick="KP.refreshCatalog()">刷新目录</button></div>
        <div class="kp-catalog" id="kp-cat-list"></div>
        <div style="text-align:center;margin-top:12px"><button class="btn sm" id="kp-cat-more" onclick="KP.catMore()">加载更多</button></div>`;
      try {
        const f = await api("/api/platform/skills/catalog/facets");
        el("kp-cat-cat").innerHTML = '<option value="">全部分类</option>' +
          f.categories.map((c) => `<option value="${E(c.name)}" ${c.name === S.catCat ? "selected" : ""}>${E(c.name)} (${c.count})</option>`).join("");
        el("kp-cat-count").textContent = `${num(f.total)} 个开源技能 · 来源 ${(f.sources || []).length || 4} 个仓库`;
      } catch (e) {}
      el("kp-cat-q").oninput = debounce(() => { S.catQ = val("kp-cat-q"); paintCatalog(true); }, 300);
      el("kp-cat-cat").onchange = () => { S.catCat = val("kp-cat-cat"); paintCatalog(true); };
      el("kp-cat-verified").onchange = () => { S.catVerified = el("kp-cat-verified").checked; paintCatalog(true); };
    }
    await loadCatalogPage();
  }

  async function refreshCatalog() {
    try {
      const r = await api("/api/platform/skills/catalog/refresh", { method: "POST" });
      toast(`目录已刷新：${r.total} 个技能`, "ok");
      paintCatalog(true);
    } catch (e) { toast(e.message, "err"); }
  }

  async function loadCatalogPage() {
    const list = el("kp-cat-list");
    if (!list) return;
    const params = new URLSearchParams({ q: S.catQ, category: S.catCat,
      limit: "40", offset: String(S.catOffset) });
    if (S.catVerified) params.set("verified", "1");
    try {
      const r = await api("/api/platform/skills/catalog?" + params.toString());
      S.catTotal = r.total;
      S.catalog = S.catalog.concat(r.items);
      S.catOffset += r.items.length;
      list.innerHTML = S.catalog.map(catalogRow).join("") ||
        empty("没有匹配的技能", "换个关键词或分类");
      const more = el("kp-cat-more");
      if (more) more.style.display = S.catOffset >= S.catTotal ? "none" : "";
      const count = el("kp-cat-count");
      if (count) count.textContent = `显示 ${S.catalog.length} / ${num(S.catTotal)}`;
    } catch (e) { list.innerHTML = empty("加载失败", e.message); }
  }

  function catalogRow(s) {
    return `<div class="kp-cat-item"><div class="kp-avatar" style="width:34px;height:34px;font-size:16px">🧠</div>
      <div class="body"><h5>${E(s.title || s.name)} ${s.verified ? '<span class="kp-tag">宽松许可</span>' : ""}</h5>
      <p>${E(s.description || "")}</p>
      <div class="meta"><span class="kp-tag">${E(s.category)}</span>
        <span class="kp-tag">${E(s.license || "未知许可")}</span>
        <span class="kp-meta">${E(s.repo || "")}</span></div></div>
      <div style="display:flex;flex-direction:column;gap:6px">
        <button class="btn sm primary" onclick="KP.installSkill('${E(s.id)}')">安装</button>
        <button class="btn sm ghost" onclick="KP.catalogDetail('${E(s.id)}')">详情</button>
      </div></div>`;
  }

  async function catalogDetail(id) {
    const s = await api(`/api/platform/skills/catalog/${id}`);
    openDrawerLike(`技能 · ${s.title || s.name}`, `
      <div class="kp-meta">${E(s.category)} · ${E(s.kind)} · ${E(s.license || "未知许可")} ${s.verified ? "· 宽松许可" : ""}</div>
      <div class="kp-drawer-sec"><h5>说明</h5><div>${E(s.description || "")}</div></div>
      <div class="kp-drawer-sec"><h5>来源</h5><a href="${E(s.source_url)}" target="_blank" rel="noopener">${E(s.source_url)}</a></div>
      <div class="kp-drawer-sec"><h5>标签</h5><div>${arr(s.tags).map((t) => `<span class="kp-tag">${E(t)}</span>`).join(" ") || "无"}</div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       ${s.installed ? '<span class="badge ok">已安装</span>' : `<button class="btn primary" onclick="KP.installSkill('${E(s.id)}')">安装</button>`}`);
  }

  async function installSkill(catalogId) {
    try {
      const r = await api("/api/platform/skills/install", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ catalog_id: catalogId }) });
      toast(`安装成功（${r.file_count || 0} 个文件）`, "ok");
      if (S.skillTab === "catalog") paintCatalog(true);
      else renderSkills();
    } catch (e) { toast(e.message, "err"); }
  }

  function catMore() { loadCatalogPage(); }

  async function viewSkillFile(skillId, path) {
    try {
      const r = await api(`/api/platform/skills/${skillId}/file?path=`
        + encodeURIComponent(path));
      openModal("技能文件 · " + path,
        `<div class="kp-pre" style="max-height:60vh;overflow:auto">${E(r.content)}</div>`,
        `<button class="btn" onclick="closeModal()">关闭</button>`);
    } catch (e) { toast(e.message, "err"); }
  }

  async function skillDetail(id) {
    const list = await api("/api/platform/skills");
    const s = list.items.find((x) => x.id === id);
    if (!s) return;
    openDrawerLike(`技能 · ${s.name}`, `
      <div class="kp-meta">${E(s.category)} · ${E(s.kind)} · ${E(s.license || "自定义")}</div>
      <div class="kp-drawer-sec"><h5>说明</h5><div>${E(s.description || "")}</div></div>
      ${s.source_url ? `<div class="kp-drawer-sec"><h5>来源</h5><a href="${E(s.source_url)}" target="_blank" rel="noopener">${E(s.source_url)}</a></div>` : ""}
      <div class="kp-drawer-sec"><h5>配置</h5><div class="kp-pre">${E(JSON.stringify(s.config || {}, null, 2))}</div></div>
      ${(s.files || []).length ? `<div class="kp-drawer-sec"><h5>已下载文件（${s.file_count}）</h5>
        ${arr(s.files).map((f) => `<div class="kp-row" style="cursor:pointer"
          data-path="${E(f.path)}" onclick="KP.viewSkillFile('${s.id}', this.dataset.path)">
          <b>${E(f.path)}</b><span class="spacer"></span>
          <small>${num(f.bytes)} B</small></div>`).join("")}</div>`
        : '<div class="kp-drawer-sec"><h5>文件</h5><div class="kp-meta">只保存了 SKILL.md（上游目录不可达）</div></div>'}`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.skillRun('${id}')">运行</button>`);
  }

  async function skillToggle(id, enabled) {
    try {
      await api(`/api/platform/skills/${id}/${enabled ? "enable" : "disable"}`,
        { method: "POST" });
      toast(enabled ? "已启用" : "已停用", "ok");
      renderSkills();
    } catch (e) { toast(e.message, "err"); }
  }

  async function skillDelete(id) {
    if (!confirm("卸载后将从已绑定的智能体中移除，确认？")) return;
    try {
      await api(`/api/platform/skills/${id}`, { method: "DELETE" });
      toast("已卸载", "ok"); renderSkills();
    } catch (e) { toast(e.message, "err"); }
  }

  function skillRun(id) {
    openDrawerLike("运行技能", `
      <div class="kp-form"><label>输入<textarea id="kp-skill-in" style="min-height:100px"></textarea></label>
      <div id="kp-skill-trace"></div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.doSkillRun('${id}')">运行</button>`);
  }

  async function doSkillRun(id) {
    const trace = makeTrace(el("kp-skill-trace"));
    try {
      await stream(`/api/platform/skills/${id}/run`, { input: val("kp-skill-in") },
        (ev) => trace.event(ev));
    } catch (e) { trace.event({ type: "error", message: e.message }); }
  }

  function skillCustom() {
    openDrawerLike("自定义技能", `
      <div class="kp-form">
        <div class="row2"><label>名称<input id="ks-name"></label>
          <label>类型<select id="ks-kind"><option value="prompt">提示词</option>
          <option value="http">HTTP API</option><option value="script">脚本操作</option>
          <option value="mcp">MCP 服务</option></select></label></div>
        <label>描述<input id="ks-desc"></label>
        <label>正文（提示词技能必填）<textarea id="ks-content" style="min-height:120px"
          placeholder="支持 {{input}} 占位符"></textarea></label>
        <label>配置 JSON（HTTP/脚本/MCP）<textarea id="ks-config" style="min-height:80px"
          placeholder='{"url":"https://...","method":"POST"}'></textarea></label>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.saveSkill()">保存</button>`);
  }

  async function saveSkill() {
    let config = {};
    try { config = val("ks-config") ? JSON.parse(val("ks-config")) : {}; }
    catch (e) { return toast("配置 JSON 格式错误", "err"); }
    try {
      await api("/api/platform/skills", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: val("ks-name"), description: val("ks-desc"),
          kind: val("ks-kind"), content: val("ks-content"), config }) });
      closeModal(); toast("技能已创建", "ok"); S.skillTab = "installed"; renderSkills();
    } catch (e) { toast(e.message, "err"); }
  }

  function skillImport() {
    openDrawerLike("从链接导入技能", `
      <div class="kp-form"><label>SKILL.md 或 GitHub 链接<input id="ks-url"
        placeholder="https://github.com/owner/repo/blob/main/skills/foo/SKILL.md"></label></div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.doImport()">导入</button>`);
  }

  async function doImport() {
    try {
      await api("/api/platform/skills/import", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: val("ks-url") }) });
      closeModal(); toast("导入成功", "ok"); S.skillTab = "installed"; renderSkills();
    } catch (e) { toast(e.message, "err"); }
  }


  async function paintPacks() {
    const host = el("kp-skill-body");
    try { S.packs = (await api("/api/platform/skill-packs")).items; }
    catch (e) { host.innerHTML = empty("加载失败", e.message); return; }
    host.innerHTML = `<div class="kp-head"><span class="kp-meta">把常用技能组合成包，一键应用到智能体</span>
      <span class="spacer"></span><button class="btn primary sm" onclick="KP.packForm()">＋ 新建技能包</button></div>
      ${S.packs.length ? `<div class="kp-list">${S.packs.map((pk) => `
        <div class="kp-row"><div><b>${E(pk.name)}</b>
          <small>${E(pk.description || "")} · ${arr(pk.skills).length} 个技能</small></div>
          <span class="spacer"></span>
          ${arr(pk.skills).slice(0, 4).map((x) => `<span class="kp-tag">${E(x.name)}</span>`).join("")}
          <button class="btn sm primary" onclick="KP.applyPack('${pk.id}')">应用到智能体</button>
          <button class="btn sm ghost" onclick="KP.packForm('${pk.id}')">编辑</button>
          <button class="btn sm ghost" onclick="KP.deletePack('${pk.id}')">删除</button></div>`).join("")}</div>`
        : empty("还没有技能包", "新建一个，把常用技能打包")}`;
  }

  async function packForm(id) {
    const skills = (await api("/api/platform/skills")).items;
    const pack = id ? (S.packs || []).find((x) => x.id === id) : {
      name: "", description: "", skill_ids: [],
    };
    openDrawerLike(id ? "编辑技能包" : "新建技能包", `
      <div class="kp-form">
        <label>名称<input id="kp-pack-name" value="${E(pack.name)}"></label>
        <label>描述<input id="kp-pack-desc" value="${E(pack.description || "")}"></label>
        <label>包含技能<div class="kp-checks">
          ${skills.map((x) => `<label><input type="checkbox" class="kp-pack-skill" value="${x.id}"
            ${arr(pack.skill_ids).includes(x.id) ? "checked" : ""}> ${E(x.name)}</label>`).join("") || '<span class="kp-meta">尚未安装技能</span>'}
        </div></label>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.savePack('${id || ""}')">保存</button>`);
  }

  async function savePack(id) {
    const payload = {
      name: val("kp-pack-name"), description: val("kp-pack-desc"),
      skill_ids: Array.from(document.querySelectorAll(".kp-pack-skill:checked")).map((n) => n.value),
    };
    if (!payload.name) return toast("名称必填", "err");
    try {
      if (id) await api(`/api/platform/skill-packs/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      else await api("/api/platform/skill-packs", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      closeModal(); toast("已保存", "ok"); paintPacks();
    } catch (e) { toast(e.message, "err"); }
  }

  async function deletePack(id) {
    if (!confirm("确认删除该技能包？（不会卸载其中技能）")) return;
    try {
      await api(`/api/platform/skill-packs/${id}`, { method: "DELETE" });
      toast("已删除", "ok"); paintPacks();
    } catch (e) { toast(e.message, "err"); }
  }

  async function applyPack(id) {
    const agents = (await api("/api/platform/agents")).items;
    openDrawerLike("应用技能包", `<div class="kp-form"><label>目标智能体
      <select id="kp-pack-agent">${agents.map((a) => `<option value="${a.id}">${E(a.name)}</option>`).join("")}</select>
      </label><div class="kp-meta">技能会合并到智能体已有技能，不会覆盖。</div></div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.doApplyPack('${id}')">应用</button>`);
  }

  async function doApplyPack(id) {
    try {
      const r = await api(`/api/platform/skill-packs/${id}/apply`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: val("kp-pack-agent") }) });
      closeModal(); toast(`已应用，智能体现有 ${r.skill_ids.length} 个技能`, "ok");
    } catch (e) { toast(e.message, "err"); }
  }


  async function paintMcp() {
    const host = el("kp-skill-body");
    host.innerHTML = `<div class="kp-head">
      <div class="kp-search"><span>⌕</span><input id="kp-mcp-q"
        placeholder="搜索官方 MCP 服务器（GitHub、数据库、搜索…）"></div>
      <span class="kp-meta" id="kp-mcp-count">加载中…</span>
      <button class="btn sm" onclick="KP.mcpManual()">手动接入 URL</button></div>
      <div class="kp-list" id="kp-mcp-list"></div>
      <div style="text-align:center;margin-top:10px">
        <button class="btn sm ghost" id="kp-mcp-more" onclick="KP.mcpMore()">加载更多</button></div>`;
    el("kp-mcp-q").oninput = debounce(() => { S.mcpQ = val("kp-mcp-q"); S.mcpCursor = ""; loadMcp(true); }, 400);
    await loadMcp(true);
  }

  async function loadMcp(reset) {
    const list = el("kp-mcp-list");
    if (!list) return;
    if (reset) { S.mcpItems = []; S.mcpCursor = ""; }
    const params = new URLSearchParams({ q: S.mcpQ || "", limit: "40" });
    if (S.mcpCursor) params.set("cursor", S.mcpCursor);
    list.innerHTML = skeleton();
    try {
      const r = await api("/api/platform/mcp/servers?" + params.toString());
      S.mcpItems = (S.mcpItems || []).concat(r.items);
      S.mcpCursor = r.next_cursor || "";
      el("kp-mcp-count").textContent = `本页 ${r.items.length} 个 · 累计 ${S.mcpItems.length}`;
      list.innerHTML = S.mcpItems.map((m, i) => `<div class="kp-cat-item">
        <div class="kp-avatar" style="width:34px;height:34px;font-size:16px">🔌</div>
        <div class="body"><h5>${E(m.title || m.name)} <span class="kp-tag">${E(m.transport)}</span></h5>
          <p>${E(m.description || "")}</p>
          <div class="meta"><span class="kp-tag">${E(m.version || "v?")}</span>
            <span class="kp-meta">${E(m.name)}</span>
            <a class="kp-meta" href="${E(m.repository || m.url)}" target="_blank" rel="noopener">来源</a></div></div>
        <div style="display:flex;flex-direction:column;gap:6px">
          <button class="btn sm primary" onclick="KP.mcpInstall(${i})">接入</button></div>
      </div>`).join("") || empty("没有找到 MCP 服务", "换个关键词试试");
      const more = el("kp-mcp-more");
      if (more) more.style.display = S.mcpCursor ? "" : "none";
    } catch (e) {
      list.innerHTML = empty("注册表不可用", e.message);
    }
  }

  function mcpMore() { loadMcp(false); }


  function mcpManual() {
    openDrawerLike("手动接入 MCP", `
      <div class="kp-form">
        <label>MCP 地址（streamable HTTP）<input id="kp-mcp-url"
          placeholder="https://mcp.example.com/mcp"></label>
        <div class="row2">
          <label>技能名称<input id="kp-mcp-name2" placeholder="my-mcp-skill"></label>
          <label>工具名<input id="kp-mcp-tool2"></label>
        </div>
        <button class="btn sm" onclick="KP.mcpToolsManual()">读取工具列表</button>
        <div id="kp-mcp-tools2" class="kp-meta"></div>
        <label>参数 JSON（支持 {input} 模板）<textarea id="kp-mcp-args2"
          style="min-height:80px" placeholder='{"repoName":"facebook/react"}'></textarea></label>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.doMcpManualInstall()">接入</button>`);
  }

  async function mcpToolsManual() {
    const url = val("kp-mcp-url");
    if (!url) return toast("请填写 MCP 地址", "err");
    const host = el("kp-mcp-tools2");
    host.textContent = "读取中…";
    try {
      const r = await api("/api/platform/mcp/tools?url=" + encodeURIComponent(url));
      host.innerHTML = `<select id="kp-mcp-tool-sel2" style="margin-top:6px">
        ${r.tools.map((t) => `<option value="${E(t.name)}">${E(t.name)} — ${E((t.description || "").slice(0, 60))}</option>`).join("")}
      </select>`;
      el("kp-mcp-tool-sel2").onchange = (ev) => { el("kp-mcp-tool2").value = ev.target.value; };
      if (r.tools[0]) el("kp-mcp-tool2").value = r.tools[0].name;
    } catch (e) { host.textContent = "读取失败：" + e.message; }
  }

  async function doMcpManualInstall() {
    const url = val("kp-mcp-url"), tool = val("kp-mcp-tool2");
    if (!url || !tool) return toast("地址与工具名必填", "err");
    let args = {};
    try { args = val("kp-mcp-args2") ? JSON.parse(val("kp-mcp-args2")) : {}; }
    catch (e) { return toast("参数 JSON 格式错误", "err"); }
    try {
      await api("/api/platform/mcp/install", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: val("kp-mcp-name2") || tool, title: tool,
          description: "手动接入的 MCP 技能", url, tool, arguments: args }) });
      closeModal(); toast("MCP 技能已接入", "ok");
      S.skillTab = "installed"; renderSkills();
    } catch (e) { toast(e.message, "err"); }
  }

  async function mcpInstall(index) {
    const m = (S.mcpItems || [])[index];
    if (!m) return;
    openDrawerLike(`接入 MCP · ${m.title || m.name}`, `
      <div class="kp-meta">${E(m.url)}</div>
      <div class="kp-form" style="margin-top:10px">
        <div class="row2">
          <label>技能名称<input id="kp-mcp-name" value="${E((m.title || m.name).replace(/[^\w\u4e00-\u9fa5.-]+/g, "-").slice(0, 40))}"></label>
          <label>工具名<input id="kp-mcp-tool" placeholder="先读取工具列表"></label>
        </div>
        <button class="btn sm" onclick="KP.mcpTools('${E(m.url)}')">读取工具列表</button>
        <div id="kp-mcp-tools" class="kp-meta"></div>
        <label>参数 JSON（模板值支持 {input}）<textarea id="kp-mcp-args"
          style="min-height:80px" placeholder='{"repoName":"owner/repo"}'></textarea></label>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.doMcpInstall(${index})">接入</button>`);
  }

  async function mcpTools(url) {
    const host = el("kp-mcp-tools");
    host.textContent = "读取中…";
    try {
      const r = await api("/api/platform/mcp/tools?url=" + encodeURIComponent(url));
      host.innerHTML = `<select id="kp-mcp-tool-sel" style="margin-top:6px">
        ${r.tools.map((t) => `<option value="${E(t.name)}">${E(t.name)} — ${E((t.description || "").slice(0, 60))}</option>`).join("")}
      </select>`;
      el("kp-mcp-tool-sel").onchange = (ev) => { el("kp-mcp-tool").value = ev.target.value; };
      if (r.tools[0]) el("kp-mcp-tool").value = r.tools[0].name;
    } catch (e) { host.textContent = "读取失败：" + e.message; }
  }

  async function doMcpInstall(index) {
    const m = (S.mcpItems || [])[index];
    if (!m) return;
    let args = {};
    try { args = val("kp-mcp-args") ? JSON.parse(val("kp-mcp-args")) : {}; }
    catch (e) { return toast("参数 JSON 格式错误", "err"); }
    const tool = val("kp-mcp-tool");
    if (!tool) return toast("请选择或填写工具名", "err");
    try {
      await api("/api/platform/mcp/install", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: val("kp-mcp-name"), title: m.title,
          description: m.description, url: m.url, tool, arguments: args,
          repository: m.repository }) });
      closeModal();
      toast("MCP 技能已接入", "ok");
      S.skillTab = "installed"; renderSkills();
    } catch (e) { toast(e.message, "err"); }
  }

  /* ══════════════════════════ experts ══════════════════════════ */

  async function renderExperts() {
    const v = el("view");
    v.innerHTML = `${head("专家广场", `
      <button class="btn primary sm" onclick="KP.expertForm()">＋ 创建专家</button>`)}
      <div id="kp-expert-list">${skeleton()}</div>`;
    try { S.experts = (await api("/api/platform/experts")).items; }
    catch (e) { el("kp-expert-list").innerHTML = empty("加载失败", e.message); return; }
    el("kp-expert-list").innerHTML = S.experts.length
      ? `<div class="kp-grid">${S.experts.map(expertCard).join("")}</div>`
      : empty("还没有专家", "把一个已发布的智能体包装成可检索的专家");
  }

  function expertCard(x) {
    return `<div class="kp-card">
      <div class="kp-card-top"><div class="kp-avatar">🎓</div>
        <div style="flex:1;min-width:0"><h4>${E(x.name)} ${x.featured ? "⭐" : ""}</h4>
          <div class="kp-meta">${E(x.title || "")}</div></div>${badge(x.status)}</div>
      <p>${E(x.bio || "暂无简介")}</p>
      <div class="kp-card-foot">
        ${arr(x.expertise).slice(0, 3).map((t) => `<span class="kp-tag">${E(t)}</span>`).join("")}
        <span class="spacer" style="flex:1"></span>
        <span class="kp-meta">${num(x.uses)} 次 · ${Number(x.rating || 0).toFixed(1)} 分</span></div>
      <div class="kp-card-foot">
        <button class="btn sm primary" onclick="KP.useTarget('expert','${x.id}')">咨询</button>
        <button class="btn sm" onclick="KP.expertDetail('${x.id}')">详情</button>
      </div></div>`;
  }

  async function expertForm(id) {
    const agents = (await api("/api/platform/agents")).items;
    const x = id ? (await api("/api/platform/experts")).items.find((r) => r.id === id) : {
      name: "", title: "", bio: "", agent_id: "", expertise: [], tags: [],
      featured: false, status: "draft",
    };
    openDrawerLike(id ? "编辑专家" : "创建专家", `
      <div class="kp-form">
        <div class="row2"><label>名称<input id="ke-name" value="${E(x.name)}"></label>
          <label>头衔<input id="ke-title" value="${E(x.title)}"></label></div>
        <label>绑定智能体<select id="ke-agent"><option value="">请选择</option>
          ${options(agents, x.agent_id, "id", "name")}</select></label>
        <label>简介<textarea id="ke-bio">${E(x.bio)}</textarea></label>
        <div class="row2"><label>专长（逗号分隔）<input id="ke-exp" value="${E(arr(x.expertise).join(", "))}"></label>
          <label>标签<input id="ke-tags" value="${E(arr(x.tags).join(", "))}"></label></div>
        <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="ke-feat"
          ${x.featured ? "checked" : ""}> 推荐专家</label>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.saveExpert('${id || ""}')">保存</button>`);
  }

  async function saveExpert(id) {
    const payload = { name: val("ke-name"), title: val("ke-title"),
      bio: val("ke-bio"), agent_id: val("ke-agent"),
      expertise: val("ke-exp"), tags: val("ke-tags"),
      featured: el("ke-feat").checked };
    if (!payload.name) return toast("名称必填", "err");
    try {
      if (id) await api(`/api/platform/experts/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      else await api("/api/platform/experts", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      closeModal(); toast("已保存", "ok"); renderExperts();
    } catch (e) { toast(e.message, "err"); }
  }

  async function expertDetail(id) {
    const x = S.experts.find((r) => r.id === id);
    if (!x) return;
    let stats = {};
    try { stats = await api(`/api/platform/experts/${id}/stats`); } catch (e) {}
    openDrawerLike(`专家 · ${x.name}`, `
      <div class="kp-meta">${E(x.title || "")}</div>
      <div class="kp-drawer-sec"><h5>简介</h5><div>${E(x.bio || "")}</div></div>
      <div class="kp-drawer-sec"><h5>专长</h5>${arr(x.expertise).map((t) => `<span class="kp-tag">${E(t)}</span>`).join(" ") || "无"}</div>
      <div class="kp-drawer-sec"><h5>绑定智能体</h5>${E((x.agent || {}).name || "未绑定")}</div>
      <div class="kp-drawer-sec"><h5>数据</h5><div class="kp-meta">咨询 ${num(x.uses)} 次 · 评分 ${Number(x.rating || 0).toFixed(1)}
        · 智能体运行 ${num(stats.agent_runs)} 次 · 成功率 ${((stats.success_rate || 0) * 100).toFixed(0)}%
        · Token ${num(stats.tokens)}</div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn" onclick="KP.rateExpert('${id}')">评分</button>
       <button class="btn" onclick="KP.expertForm('${id}')">编辑</button>
       <button class="btn primary" onclick="KP.useTarget('expert','${id}')">咨询</button>`);
  }

  function expertChat(id) {
    openDrawerLike("专家咨询", `
      <div class="kp-chat" id="kp-expert-log"></div>
      <div class="kp-chat-input"><textarea id="kp-expert-q" placeholder="Enter 发送"></textarea>
        <button class="btn primary" onclick="KP.sendExpert('${id}')">发送</button></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>`);
    const ta = el("kp-expert-q");
    ta.onkeydown = (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendExpert(id); }
    };
  }

  async function sendExpert(id) {
    const q = val("kp-expert-q");
    if (!q || S.chatBusy) return;
    S.expertSession = S.expertSession || {};
    S.chatBusy = true;
    const log = el("kp-expert-log");
    log.insertAdjacentHTML("beforeend", `<div class="kp-msg user">${E(q)}</div>`);
    const bubble = document.createElement("div");
    bubble.className = "kp-msg assistant"; bubble.textContent = "…";
    log.appendChild(bubble); el("kp-expert-q").value = "";
    let text = "";
    try {
      await stream(`/api/platform/experts/${id}/run`,
        { message: q, session_id: S.expertSession[id] || "" }, (ev) => {
        if (ev.type === "run_start" && ev.session_id) S.expertSession[id] = ev.session_id;
        if (ev.type === "delta") { text += ev.text || ""; bubble.textContent = text; }
        else if (ev.type === "result") { text = ev.output || text; bubble.textContent = text; }
        else if (ev.type === "error") bubble.textContent = "错误：" + ev.message;
        log.scrollTop = log.scrollHeight;
      });
    } catch (e) { bubble.textContent = "错误：" + e.message; }
    S.chatBusy = false;
  }

  async function rateExpert(id) {
    const score = Number(prompt("评分 1-5", "5") || 0);
    if (!score) return;
    try {
      await api(`/api/platform/experts/${id}/rate`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ score }) });
      toast("已评分", "ok"); renderExperts();
    } catch (e) { toast(e.message, "err"); }
  }

  /* ══════════════════════════ teams ══════════════════════════ */

  async function renderTeams() {
    const v = el("view");
    v.innerHTML = `${head("团队协作", `<button class="btn sm"
      onclick="KP.startDesigner('team')">AI 创建</button>
      <button class="btn sm" onclick="KP.teamTemplates()">从模板创建</button>
      <button class="btn primary sm" onclick="KP.teamForm()">＋ 新建团队</button>`)}
      <div id="kp-team-list">${empty("加载中…", "")}</div>`;
    try { S.teams = (await api("/api/platform/teams")).items; }
    catch (e) { el("kp-team-list").innerHTML = empty("加载失败", e.message); return; }
    el("kp-team-list").innerHTML = S.teams.length
      ? `<div class="kp-grid">${S.teams.map(teamCard).join("")}</div>`
      : empty("还没有团队", "把多个智能体组合成圆桌、主管调度或交接流程");
  }

  function teamCard(t) {
    const names = arr(t.members).map((m) =>
      ((m.agent || {}).name || m.role || "成员")).join("、");
    return `<div class="kp-card">
      <div class="kp-card-top"><div class="kp-avatar">👥</div>
        <div style="flex:1;min-width:0"><h4>${E(t.name)}</h4>
          <div class="kp-meta">${E(t.strategy)} · 最多 ${t.max_turns} 轮</div></div></div>
      <p>${E(t.description || "暂无描述")}</p>
      <div class="kp-card-foot"><span class="kp-tag">${E(names || "无成员")}</span></div>
      <div class="kp-card-foot">
        <button class="btn sm primary" onclick="KP.useTarget('team','${t.id}')">运行</button>
        <button class="btn sm" onclick="KP.explainTeam('${t.id}')">说明</button>
        <button class="btn sm ghost" onclick="KP.aiEditTeam('${t.id}')">AI 修改</button>
        <button class="btn sm ghost" onclick="KP.teamForm('${t.id}')">编辑</button>
        <button class="btn sm ghost" onclick="KP.teamDelete('${t.id}')">删除</button>
      </div></div>`;
  }


  async function teamTemplates() {
    let items = [];
    try { items = (await api("/api/platform/teams/templates")).items; } catch (e) {}
    openDrawerLike("从模板创建团队", `<div class="kp-list">
      ${items.map((t) => `<div class="kp-row"><div><b>${E(t.name)}</b>
        <small>${E(t.description)}</small></div><span class="spacer"></span>
        <span class="kp-tag">${E(t.strategy)}</span>
        <button class="btn sm primary" onclick="KP.useTeamTemplate('${t.id}')">使用</button></div>`).join("")}
      </div>`, `<button class="btn" onclick="closeModal()">关闭</button>`);
    S.teamTemplates = items;
  }

  function useTeamTemplate(id) {
    const t = (S.teamTemplates || []).find((x) => x.id === id);
    if (!t) return;
    closeModal();
    teamForm("", { ...t, members: [], synthesizer_id: "" });
  }

  async function teamForm(id, preset) {
    const agents = (await api("/api/platform/agents")).items;
    const t = id ? S.teams.find((r) => r.id === id) : (preset || {
      name: "", description: "", strategy: "roundtable", max_turns: 4,
      members: [], synthesizer_id: "",
    });
    const memberIds = arr(t.members).map((m) => m.agent_id);
    openDrawerLike(id ? "编辑团队" : "新建团队", `
      <div class="kp-form">
        <label>名称<input id="kt-name" value="${E(t.name)}"></label>
        <label>描述<textarea id="kt-desc">${E(t.description)}</textarea></label>
        <div class="row2">
          <label>协作策略<select id="kt-strategy">
            <option value="roundtable" ${t.strategy === "roundtable" ? "selected" : ""}>圆桌（每人发言后综合）</option>
            <option value="supervisor" ${t.strategy === "supervisor" ? "selected" : ""}>主管调度（模型指派）</option>
            <option value="handoff" ${t.strategy === "handoff" ? "selected" : ""}>交接（依次处理）</option>
          </select></label>
          <label>最大轮次<input id="kt-turns" type="number" min="1" max="12" value="${t.max_turns}"></label>
        </div>
        <label>综合智能体（可选）<select id="kt-synth"><option value="">由模型综合</option>
          ${options(agents, t.synthesizer_id, "id", "name")}</select></label>
        <label>成员与角色<div class="kp-checks">
          ${agents.map((a) => `<label><input type="checkbox" class="kt-member" value="${a.id}"
            ${memberIds.includes(a.id) ? "checked" : ""}> ${E(a.name)}
            <input class="kt-role" data-agent="${a.id}" placeholder="角色"
              value="${E((arr(t.members).find((m) => m.agent_id === a.id) || {}).role || "")}"
              style="width:90px;padding:3px 6px"></label>`).join("")}
        </div></label>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.saveTeam('${id || ""}')">保存</button>`);
  }

  async function saveTeam(id) {
    const members = Array.from(document.querySelectorAll(".kt-member:checked")).map((n) => ({
      agent_id: n.value,
      role: (document.querySelector(`.kt-role[data-agent="${n.value}"]`) || {}).value || "",
    }));
    const payload = { name: val("kt-name"), description: val("kt-desc"),
      strategy: val("kt-strategy"), max_turns: Number(val("kt-turns") || 4),
      members, synthesizer_id: val("kt-synth") };
    if (!payload.name) return toast("名称必填", "err");
    if (!members.length) return toast("至少选择一名成员", "err");
    try {
      if (id) await api(`/api/platform/teams/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      else await api("/api/platform/teams", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      closeModal(); toast("已保存", "ok"); renderTeams();
    } catch (e) { toast(e.message, "err"); }
  }

  function teamRun(id) {
    openDrawerLike("运行团队", `
      <div class="kp-form"><label>任务<textarea id="kp-team-q" style="min-height:90px"></textarea></label>
        <div id="kp-team-trace"></div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.doTeamRun('${id}')">开始</button>`);
  }

  async function doTeamRun(id) {
    const q = val("kp-team-q");
    if (!q) return toast("请输入任务", "err");
    const trace = makeTrace(el("kp-team-trace"));
    try {
      await stream(`/api/platform/teams/${id}/run`, { message: q }, (ev) => trace.event(ev));
    } catch (e) { trace.event({ type: "error", message: e.message }); }
  }

  async function teamDelete(id) {
    if (!confirm("确认删除该团队？")) return;
    try {
      await api(`/api/platform/teams/${id}`, { method: "DELETE" });
      toast("已删除", "ok"); renderTeams();
    } catch (e) { toast(e.message, "err"); }
  }

  /* ══════════════════════════ workflows ══════════════════════════ */

  async function renderWorkflows() {
    const v = el("view");
    v.innerHTML = `${head("工作流", `
      <button class="btn sm" onclick="KP.startDesigner('workflow')">AI 创建</button>
      <button class="btn sm" onclick="KP.workflowTemplates()">从模板创建</button>
      <button class="btn primary sm" onclick="KP.workflowForm()">＋ 新建工作流</button>`)}
      <div id="kp-workflow-list">${skeleton()}</div>`;
    try { S.workflows = (await api("/api/platform/workflows")).items; }
    catch (e) { el("kp-workflow-list").innerHTML = empty("加载失败", e.message); return; }
    el("kp-workflow-list").innerHTML = S.workflows.length
      ? `<div class="kp-grid">${S.workflows.map(workflowCard).join("")}</div>`
      : empty("还没有工作流", "用模板创建一个：知识简报、评审或条件分流");
  }

  function workflowCard(w) {
    const nodes = arr(w.nodes);
    return `<div class="kp-card">
      <div class="kp-card-top"><div class="kp-avatar">🔀</div>
        <div style="flex:1;min-width:0"><h4>${E(w.name)}</h4>
          <div class="kp-meta">${nodes.length} 个节点 · v${w.version || 0}</div></div>${badge(w.status)}</div>
      <p>${E(w.description || "暂无描述")}</p>
      <div class="kp-card-foot">
        <button class="btn sm primary" onclick="KP.useTarget('workflow','${w.id}')">运行</button>
        <button class="btn sm" onclick="KP.explainWorkflow('${w.id}')">说明</button>
        <button class="btn sm ghost" onclick="KP.aiEditWorkflow('${w.id}')">AI 修改</button>
        <button class="btn sm ghost" onclick="KP.workflowForm('${w.id}')">编辑</button>
        <button class="btn sm ghost" onclick="KP.workflowDelete('${w.id}')">删除</button>
      </div></div>`;
  }

  function renderDag(nodes) {
    if (!arr(nodes).length) return '<div class="kp-meta">暂无节点</div>';
    const order = [];
    const byId = {};
    arr(nodes).forEach((n) => { byId[n.id] = n; });
    let cur = (arr(nodes).find((n) => n.type === "start") || {}).id || arr(nodes)[0].id;
    const seen = new Set();
    while (cur && byId[cur] && !seen.has(cur) && order.length < 20) {
      seen.add(cur);
      const node = byId[cur];
      order.push(node);
      if (node.type === "condition") cur = (node.branches || {}).true || "";
      else cur = Array.isArray(node.next) ? node.next[0] : node.next;
    }
    return `<div class="kp-dag">${order.map((n, i) =>
      `${i ? '<span class="kp-arrow">→</span>' : ""}<div class="kp-node">
        <b>${E(n.label || n.id)}</b><small>${E(n.type)}</small></div>`).join("")}
      ${order.length < arr(nodes).length ? `<span class="kp-arrow">…</span>` : ""}</div>`;
  }


  function nodeSummary(node) {
    const typ = node.type;
    const cfg = node.config || {};
    if (typ === "llm") return (cfg.prompt || "").slice(0, 90);
    if (typ === "agent") return "调用智能体：" + (cfg.agent_id || "");
    if (typ === "skill") return "执行技能：" + (cfg.skill_id || "");
    if (typ === "tool") return "调用工具：" + (cfg.tool || "");
    if (typ === "knowledge") return "检索知识（top " + (cfg.top_k || 5) + "）";
    if (typ === "condition") return "条件：" + (cfg.expr || "未设置");
    if (typ === "approval") return cfg.message || "需要人工确认";
    if (typ === "delay") return "等待 " + (cfg.seconds || 1) + " 秒";
    if (typ === "parallel") return "并行执行 " + arr(cfg.members).length + " 个分支";
    return "";
  }


  const AI_KIND = {
    workflow: { title: "AI 创建流程", hint: "描述目标，例如：每周一总结新增制度并生成简报，最后人工确认。" },
    agent: { title: "AI 创建智能体", hint: "描述角色，例如：一个依据制度文档回答流程与时限的助手。" },
    team: { title: "AI 创建团队", hint: "描述协作，例如：制度助手先答，数据师补充口径，最后综合。" },
  };


  const DESIGNER_KIND = {
    workflow: { label: "工作流", hint: "例如：每周一总结新增制度，生成简报，最后人工确认。" },
    agent: { label: "智能体", hint: "例如：一个依据制度文档回答流程与时限的助手。" },
    team: { label: "团队", hint: "例如：制度助手先答，数据师补充口径，最后综合。" },
  };

  function startDesigner(kind, initial, targetId) {
    let draft = null;
    if (Array.isArray(initial)) {
      draft = { name: "AI 修改的流程", nodes: initial, description: "",
        input_schema: [{ key: "input", label: "输入", type: "textarea",
                         required: true }] };
    } else if (initial && typeof initial === "object") {
      draft = initial;
    }
    S.designer = {
      kind: kind || "workflow",
      messages: [{ role: "assistant",
        content: draft ? "我已经读入现有配置。你想改哪一部分？" :
          "告诉我你想完成什么，我会边聊边把草稿画出来。" }],
      draft, changes: [], ready: !!draft, busy: false,
      targetId: targetId || "",
    };
    if (state.view === "designer") renderDesigner();
    else switchView("designer");
  }


  async function aiEditWorkflow(id) {
    try {
      const w = await api(`/api/platform/workflows/${id}`);
      startDesigner("workflow", {
        name: w.name, description: w.description || "",
        nodes: w.nodes, input_schema: w.input_schema || [],
      }, id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function aiEditAgent(id) {
    try {
      const a = await api(`/api/platform/agents/${id}`);
      startDesigner("agent", {
        name: a.name, title: a.title, description: a.description,
        category: a.category, system_prompt: a.system_prompt,
        tool_ids: a.tool_ids, skill_ids: a.skill_ids, tags: a.tags,
      }, id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function aiEditTeam(id) {
    try {
      const t = await api(`/api/platform/teams`);
      const team = (t.items || []).find((x) => x.id === id);
      if (!team) return toast("团队不存在", "err");
      startDesigner("team", {
        name: team.name, description: team.description,
        strategy: team.strategy, max_turns: team.max_turns,
        members: team.members, synthesizer_id: team.synthesizer_id,
      }, id);
    } catch (e) { toast(e.message, "err"); }
  }

  function designerMessages() {
    const d = S.designer;
    return (d.messages || []).map((m) => `<div class="kp-dmsg ${m.role === "user" ? "user" : "assistant"}">
      ${m.role === "user" ? E(m.content) : md2html(m.content || "").html}</div>`).join("")
      + (d.busy ? '<div class="kp-dmsg assistant kp-typing">AI 正在修改草稿…</div>' : "");
  }

  function designerPreview() {
    const d = S.designer;
    if (!d.draft) {
      return `<div class="kp-ws-empty">还没有草稿。在左侧描述目标，AI 会自动生成并实时更新这里。</div>`;
    }
    const draft = d.draft;
    const changes = arr(d.changes);
    let body = "";
    if (d.kind === "workflow") {
      const nodes = arr(draft.nodes);
      body = `<div class="kp-drawer-sec"><h5>流程</h5>${renderFlowGraph(nodes)}</div>
        <div class="kp-drawer-sec"><h5>步骤</h5>
          <ol class="kp-flow-steps">${nodes.filter((n) => n.type !== "end").map((n) =>
            `<li><b>${E(n.label || n.id)}</b><span class="kp-tag">${E(n.type)}</span>
             <small>${E(nodeSummary(n))}</small></li>`).join("")}</ol></div>`;
    } else if (d.kind === "agent") {
      body = `<div class="kp-drawer-sec"><h5>草稿</h5>
        <div><b>${E(draft.name || "")}</b> <span class="kp-meta">${E(draft.title || "")}</span></div>
        <div class="kp-meta">${E(draft.description || "")}</div>
        <div class="kp-pre">${E(draft.system_prompt || "")}</div></div>
        <div class="kp-meta">工具：${E(arr(draft.tool_ids).join("、") || "无")} · 技能：${arr(draft.skill_ids).length} 个</div>`;
    } else {
      body = `<div class="kp-drawer-sec"><h5>协作方式</h5>
        <div><b>${E(draft.strategy || "roundtable")}</b> · 最多 ${draft.max_turns || 4} 轮</div>
        ${renderTeamGraph(draft)}</div>`;
    }
    return `${changes.length ? `<div class="kp-changes">${changes.map((c) =>
      `<span>${E(c)}</span>`).join("")}</div>` : ""}
      ${arr(draft.questions).length ? `<div class="kp-meta" style="margin:6px 0 10px">
        待确认：${E(arr(draft.questions).join("；"))}</div>` : ""}
      ${body}`;
  }

  function renderDesigner() {
    const d = S.designer;
    if (!d) return startDesigner("workflow");
    const v = el("view");
    v.innerHTML = `<div class="kp-designer">
      <section class="kp-designer-chat">
        <header class="kp-designer-head">
          <b>AI 创建</b>
          <div class="kp-designer-tabs">${Object.keys(DESIGNER_KIND).map((k) =>
            `<button data-k="${k}" class="${d.kind === k ? "on" : ""}">${DESIGNER_KIND[k].label}</button>`).join("")}</div>
        </header>
        <div class="kp-dmsgs" id="kp-dmsgs">${designerMessages()}</div>
        <div class="kp-dcomposer">
          <textarea id="kp-dinput" rows="2"
            placeholder="描述目标，Enter 发送，Shift+Enter 换行"></textarea>
          <button class="send ${d.busy ? "stop" : "icon-only"}" id="kp-dsend"
            ${d.busy ? "disabled" : ""}>${d.busy ? "…" : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`}</button>
        </div>
      </section>
      <section class="kp-designer-preview">
        <header class="kp-designer-head">
          <b>草稿预览</b><span class="kp-meta">${E(DESIGNER_KIND[d.kind].label)}</span>
          <span class="spacer" style="flex:1"></span>
          <button class="btn sm primary" id="kp-dsave" onclick="KP.saveDesignerDraft()"
            ${d.draft ? "" : "disabled"}>保存为草稿</button>
        </header>
        <div class="kp-dpreview" id="kp-dpreview">${designerPreview()}</div>
      </section>
    </div>`;
    el("kp-dsend").onclick = sendDesigner;
    el("kp-dinput").onkeydown = (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendDesigner(); }
    };
    document.querySelectorAll(".kp-designer-tabs button").forEach((b) => {
      b.onclick = () => startDesigner(b.dataset.k);
    });
    const box = el("kp-dmsgs");
    if (box) box.scrollTop = box.scrollHeight;
    setTimeout(() => el("kp-dinput")?.focus(), 30);
  }

  async function sendDesigner() {
    const d = S.designer;
    const input = el("kp-dinput");
    const text = (input?.value || "").trim();
    if (!text || d.busy) return;
    d.messages.push({ role: "user", content: text });
    d.busy = true;
    input.value = "";
    renderDesigner();
    try {
      const r = await api("/api/platform/design/chat", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: d.kind, messages: d.messages,
          draft: d.draft || null }) });
      if (!r.ok) {
        d.messages.push({ role: "assistant",
          content: "这次没有生成成功：" + (r.error || "未知错误") });
      } else {
        d.messages.push({ role: "assistant", content: r.reply || "草稿已更新。" });
        if (r.draft) d.draft = r.draft;
        d.changes = arr(r.changes);
        d.ready = !!r.ready;
      }
    } catch (e) {
      d.messages.push({ role: "assistant", content: "请求失败：" + e.message });
    } finally {
      d.busy = false;
      renderDesigner();
    }
  }

  async function saveDesignerDraft() {
    const d = S.designer;
    if (!d || !d.draft) return toast("还没有草稿", "err");
    try {
      const editing = !!d.targetId;
      const method = editing ? "PATCH" : "POST";
      const suffix = editing ? `/${d.targetId}` : "";
      if (d.kind === "workflow") {
        await api("/api/platform/workflows" + suffix, { method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: d.draft.name,
            description: d.draft.description || "", nodes: d.draft.nodes,
            input_schema: d.draft.input_schema }) });
        toast(editing ? "已更新工作流" : "已保存为工作流草稿", "ok");
        switchView("workflows");
      } else if (d.kind === "agent") {
        await api("/api/platform/agents" + suffix, { method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(d.draft) });
        toast(editing ? "已更新智能体" : "已保存为智能体草稿", "ok");
        switchView("agents");
      } else {
        await api("/api/platform/teams" + suffix, { method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(d.draft) });
        toast(editing ? "已更新团队" : "已保存为团队草稿", "ok");
        switchView("teams");
      }
    } catch (e) { toast(e.message, "err"); }
  }

  function aiDesign(kind) {
    S.ai = { kind, draft: null };
    openDrawerLike(AI_KIND[kind].title, `
      <div class="kp-form">
        <label>你想要什么
          <textarea id="kp-ai-prompt" style="min-height:100px"
            placeholder="${E(AI_KIND[kind].hint)}"></textarea></label>
        <div class="kp-meta">AI 最多追问 3 个问题；只生成草稿，不会自动发布。</div>
      </div>
      <div id="kp-ai-result"></div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn" id="kp-ai-go" onclick="KP.runAiDesign()">生成草稿</button>
       <button class="btn primary" id="kp-ai-save" onclick="KP.saveAiDraft()" disabled>保存为草稿</button>`);
  }

  function aiPreview(kind, d) {
    if (kind === "workflow") {
      const steps = arr(d.nodes).filter((n) => n.type !== "end");
      return `<div class="kp-drawer-sec"><h5>流程预览</h5>
        <ol class="kp-flow-steps">${steps.map((n) => `<li><b>${E(n.label || n.id)}</b>
          <span class="kp-tag">${E(n.type)}</span>
          <small>${E(nodeSummary(n))}</small></li>`).join("")}</ol>
        ${renderDag(d.nodes)}</div>`;
    }
    if (kind === "agent") {
      return `<div class="kp-drawer-sec"><h5>角色设定</h5>
        <div class="kp-pre">${E(d.system_prompt || "")}</div>
        <div class="kp-meta">工具：${E(arr(d.tool_ids).join("、") || "无")}</div>
        <div class="kp-meta">技能：${E(arr(d.skill_ids).length)} 个</div></div>`;
    }
    return `<div class="kp-drawer-sec"><h5>成员</h5>
      ${arr(d.members).map((m, i) => `<div class="kp-row"><b>${i + 1}</b>
        <div><b>${E(m.agent_id)}</b><small>${E(m.role || "成员")}</small></div></div>`).join("")}
      <div class="kp-meta">策略：${E(d.strategy)} · 最多 ${d.max_turns || 4} 轮</div></div>`;
  }

  async function runAiDesign() {
    const prompt = val("kp-ai-prompt");
    if (!prompt) return toast("请先描述目标", "err");
    const kind = S.ai.kind;
    const go = el("kp-ai-go");
    if (go) { go.disabled = true; go.textContent = "生成中…"; }
    const box = el("kp-ai-result");
    if (box) box.innerHTML = '<div class="kp-meta" style="padding:10px 0">AI 正在设计…</div>';
    try {
      const r = await api("/api/platform/design/draft", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, prompt, draft: S.ai.draft }) });
      if (!r.ok) {
        if (box) box.innerHTML = `<div class="kp-drawer-sec"><h5>还不能保存</h5>
          <div class="kp-meta">${E(r.error || "生成失败")}</div></div>`;
        return;
      }
      S.ai.draft = r.draft;
      const questions = arr(r.questions);
      if (box) box.innerHTML = `
        <div class="kp-drawer-sec"><h5>AI 的理解</h5>
          <div>${E(r.summary || r.draft.description || "")}</div></div>
        ${questions.length ? `<div class="kp-drawer-sec"><h5>还需要确认</h5>
          ${questions.map((q) => `<div class="kp-meta">· ${E(q)}</div>`).join("")}</div>` : ""}
        ${aiPreview(kind, r.draft)}`;
      if (el("kp-ai-save")) el("kp-ai-save").disabled = false;
      if (go) go.textContent = "继续修改";
    } catch (e) {
      if (box) box.innerHTML = `<div class="kp-meta">${E(e.message)}</div>`;
    } finally {
      if (go) { go.disabled = false; if (go.textContent === "生成中…") go.textContent = "生成草稿"; }
    }
  }

  async function saveAiDraft() {
    const { kind, draft } = S.ai || {};
    if (!draft) return toast("还没有草稿", "err");
    try {
      if (kind === "workflow") {
        await api("/api/platform/workflows", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: draft.name, description: draft.description,
            nodes: draft.nodes, input_schema: draft.input_schema }) });
        closeModal(); toast("已保存为工作流草稿", "ok"); renderWorkflows();
      } else if (kind === "agent") {
        await api("/api/platform/agents", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(draft) });
        closeModal(); toast("已保存为智能体草稿", "ok"); renderAgents();
      } else {
        await api("/api/platform/teams", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(draft) });
        closeModal(); toast("已保存为团队草稿", "ok"); renderTeams();
      }
    } catch (e) { toast(e.message, "err"); }
  }


  function flowIcon(type) {
    const map = {
      start: '<path d="M8 5l11 7-11 7V5Z"/>',
      end: '<path d="M6 4v16M6 5h11l-2 3 2 3H6"/>',
      llm: '<path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8L12 3Z"/>',
      agent: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M9 3v4M15 3v4M9 13h.01M15 13h.01"/>',
      skill: '<path d="M12 3l2 4.5L18.5 9 14 11l-2 4.5L10 11 5.5 9 10 7.5 12 3Z"/>',
      tool: '<path d="M14 6a4 4 0 0 0-5 5l-6 6 2 2 6-6a4 4 0 0 0 5-5l-2 2-2-2 2-2Z"/>',
      knowledge: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Z"/>',
      condition: '<path d="M12 4v6M12 14v6M6 12h12M9 9l3-3 3 3M9 15l3 3 3-3"/>',
      parallel: '<rect x="3" y="4" width="7" height="7" rx="1.5"/><rect x="14" y="4" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
      approval: '<circle cx="12" cy="12" r="8"/><path d="M8 12l3 3 5-6"/>',
      delay: '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',
    };
    return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
      stroke-linejoin="round">${map[type] || map.llm}</svg>`;
  }

  function flowTargets(node) {
    const out = [];
    const b = node.branches || {};
    const conditional = node.type === "condition" && (b.true || b.false);
    if (!conditional) {
      const nxt = node.next;
      if (typeof nxt === "string" && nxt) out.push({ id: nxt, label: "" });
      else if (Array.isArray(nxt)) nxt.forEach((x) => x && out.push({ id: x, label: "" }));
    }
    if (b.true) out.push({ id: b.true, label: "是" });
    if (b.false) out.push({ id: b.false, label: "否" });
    if (node.type === "parallel") {
      arr((node.config || {}).members).forEach((x) => out.push({ id: x, label: "并行" }));
    }
    const seen = new Set();
    return out.filter((t) => {
      if (seen.has(t.id)) return false;
      seen.add(t.id);
      return true;
    });
  }

  function renderFlowGraph(nodes, activeId) {
    nodes = arr(nodes);
    if (!nodes.length) return '<div class="kp-meta">暂无节点</div>';
    const byId = {};
    nodes.forEach((n) => { byId[n.id] = n; });
    const start = (nodes.find((n) => n.type === "start") || nodes[0]).id;
    const layer = { [start]: 0 };
    const queue = [start];
    while (queue.length) {
      const id = queue.shift();
      const node = byId[id];
      if (!node) continue;
      flowTargets(node).forEach((t) => {
        if (!byId[t.id]) return;
        const nextLayer = (layer[id] || 0) + 1;
        if (layer[t.id] === undefined || layer[t.id] < nextLayer) {
          layer[t.id] = nextLayer;
          queue.push(t.id);
        }
      });
    }
    let maxLayer = 0;
    Object.values(layer).forEach((v) => { maxLayer = Math.max(maxLayer, v); });
    nodes.forEach((n) => {
      if (layer[n.id] === undefined) {
        maxLayer += 1;
        layer[n.id] = maxLayer;
      }
    });
    const groups = {};
    nodes.forEach((n) => {
      const l = layer[n.id];
      (groups[l] = groups[l] || []).push(n);
    });
    const nodeW = 196, nodeH = 54, gapX = 28, gapY = 44, pad = 20;
    const positions = {};
    const maxInLayer = {};
    Object.keys(groups).forEach((l) => {
      maxInLayer[l] = groups[l].length;
    });
    Object.keys(groups).forEach((l) => {
      groups[l].forEach((n, i) => {
        positions[n.id] = {
          x: pad + i * (nodeW + gapX),
          y: pad + Number(l) * (nodeH + gapY),
        };
      });
    });
    const widest = Math.max(1, ...Object.values(maxInLayer));
    const height = (maxLayer + 1) * (nodeH + gapY) - gapY + pad * 2;
    const width = widest * (nodeW + gapX) - gapX + pad * 2;
    let edges = "";
    nodes.forEach((n) => {
      flowTargets(n).forEach((t) => {
        if (!positions[t.id]) return;
        const a = positions[n.id], b = positions[t.id];
        const x1 = a.x + nodeW / 2, y1 = a.y + nodeH;
        const x2 = b.x + nodeW / 2, y2 = b.y;
        const my = (y1 + y2) / 2;
        edges += `<path d="M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}"
          fill="none" stroke="var(--g300)" stroke-width="1.4"
          marker-end="url(#kp-arrow)"/>`;
        if (t.label) {
          edges += `<text x="${(x1 + x2) / 2 + 4}" y="${my - 4}"
            text-anchor="middle" fill="var(--text-3)" font-size="9">${E(t.label)}</text>`;
        }
      });
    });
    return `<div class="kp-flow-shell"><div class="kp-flow-canvas"
      style="width:${width}px;height:${height}px">
      <svg width="${width}" height="${height}">
        <defs><marker id="kp-arrow" markerWidth="7" markerHeight="7" refX="6"
          refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z"
          fill="var(--g300)"/></marker></defs>${edges}</svg>
      ${nodes.map((n) => {
        const p = positions[n.id];
        return `<div class="kp-flow-node ${activeId === n.id ? "active" : ""}"
          style="left:${p.x}px;top:${p.y}px" title="${E(nodeSummary(n))}">
          <span class="fi">${flowIcon(n.type)}</span>
          <div><b>${E(n.label || n.id)}</b><small>${E(n.type)}</small></div>
        </div>`;
      }).join("")}
    </div></div>`;
  }

  function renderTeamGraph(t) {
    const members = arr(t.members);
    if (!members.length) return '<div class="kp-meta">暂无成员</div>';
    return `<div class="kp-team-graph">
      ${members.map((m, i) => `<div class="kp-team-node">
        <span class="idx">${i + 1}</span>
        <b>${E((m.agent || {}).name || m.agent_name || m.agent_id)}</b>
        <small>${E(m.role || "成员")}</small></div>`).join('<span class="kp-team-arrow">→</span>')}
      <span class="kp-team-arrow">→</span>
      <div class="kp-team-node synth"><span class="idx">✓</span>
        <b>综合结论</b><small>${t.strategy === "supervisor" ? "主管汇总" : t.strategy === "handoff" ? "末位输出" : "综合智能体"}</small></div>
    </div>`;
  }

  function explainWorkflow(id) {
    const w = (S.workflows || []).find((x) => x.id === id);
    if (!w) return;
    const nodes = arr(w.nodes);
    const byId = {};
    nodes.forEach((n) => { byId[n.id] = n; });
    const steps = [];
    let cur = (nodes.find((n) => n.type === "start") || {}).id;
    const seen = new Set();
    while (cur && byId[cur] && !seen.has(cur) && steps.length < 30) {
      seen.add(cur);
      const n = byId[cur];
      steps.push(n);
      if (n.type === "condition") {
        const b = n.branches || {};
        if (b.true) steps.push({ id: b.true + ":true", type: "branch",
          label: "条件成立 → " + ((byId[b.true] || {}).label || b.true) });
        if (b.false) steps.push({ id: b.false + ":false", type: "branch",
          label: "条件不成立 → " + ((byId[b.false] || {}).label || b.false) });
        cur = b.true || "";
        continue;
      }
      if (n.type === "end") break;
      cur = Array.isArray(n.next) ? n.next[0] : n.next;
    }
    const inputs = arr(w.input_schema);
    openDrawerLike("流程说明 · " + w.name, `
      <div class="kp-meta">${E(w.description || "没有描述")}</div>
      <div class="kp-drawer-sec"><h5>运行输入</h5>
        ${inputs.length ? inputs.map((f) => `<span class="kp-tag">${E(f.label || f.key)}${f.required ? " *" : ""}</span>`).join(" ")
          : '<span class="kp-meta">单输入框</span>'}</div>
      <div class="kp-drawer-sec"><h5>步骤</h5>
        <ol class="kp-flow-steps">${steps.map((n) => `<li>
          <b>${E(n.label || n.id)}</b>
          <span class="kp-tag">${E(n.type)}</span>
          <small>${E(nodeSummary(n))}</small></li>`).join("")}</ol></div>
      <div class="kp-drawer-sec"><h5>流程图</h5>${renderFlowGraph(nodes)}</div>
      <div class="kp-drawer-sec"><h5>怎么运行</h5>
        <div class="kp-meta">点击“运行”填写上方输入；遇到审批节点会暂停，可在右侧轨迹通过；结果会生成产物。</div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.useTarget('workflow','${id}')">去运行</button>`);
  }

  function explainTeam(id) {
    const t = (S.teams || []).find((x) => x.id === id);
    if (!t) return;
    const members = arr(t.members);
    const strategy = t.strategy === "supervisor" ? "主管调度"
      : t.strategy === "handoff" ? "流水线交接" : "圆桌讨论";
    const desc = t.strategy === "supervisor"
      ? "由模型担任主管，每一轮决定下一位成员和任务，最后汇总。"
      : t.strategy === "handoff"
        ? "按成员顺序依次处理，上一位的输出作为下一位的输入。"
        : "每位成员先独立发言，再由综合智能体合并成最终结论。";
    openDrawerLike("团队说明 · " + t.name, `
      <div class="kp-meta">${E(t.description || "")}</div>
      <div class="kp-drawer-sec"><h5>协作方式</h5>
        <div><b>${E(strategy)}</b></div><div class="kp-meta">${E(desc)}</div>
        <div class="kp-meta">最多 ${t.max_turns || 4} 轮</div></div>
      <div class="kp-drawer-sec"><h5>协作图</h5>${renderTeamGraph(t)}</div>
      <div class="kp-drawer-sec"><h5>成员</h5>
        ${members.map((m, i) => `<div class="kp-row"><b>${i + 1}</b>
          <div><b>${E((m.agent || {}).name || m.agent_id)}</b>
          <small>角色：${E(m.role || "成员")}</small></div></div>`).join("")}</div>
      <div class="kp-drawer-sec"><h5>运行后会发生什么</h5>
        <div class="kp-meta">${E(desc)} 综合结果会出现在对话里，成员发言与交接在右侧轨迹中。</div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.useTarget('team','${id}')">去运行</button>`);
  }

  async function workflowForm(id, preset) {
    const w = id ? (await api(`/api/platform/workflows/${id}`)) : (preset || {
      name: "", description: "", nodes: [
        { id: "start", type: "start", label: "开始", next: "llm" },
        { id: "llm", type: "llm", label: "处理",
          config: { prompt: "请处理：{input}" }, next: "end" },
        { id: "end", type: "end", label: "结束" }],
    });
    openDrawerLike(id ? "编辑工作流" : "新建工作流", `
      <div class="kp-form">
        <div class="row2"><label>名称<input id="kw-name" value="${E(w.name)}"></label>
          <label>描述<input id="kw-desc" value="${E(w.description || "")}"></label></div>
        <div class="kp-meta">日常修改建议用「AI 修改」；下面是可视化预览。JSON 仅在高级模式中编辑。</div>
        <div id="kw-dag">${renderFlowGraph(w.nodes)}</div>
        <details class="kp-advanced"><summary>高级：JSON 编辑</summary>
          <label>节点 JSON（start / llm / agent / skill / tool / knowledge / condition / parallel / approval / delay / end）
            <textarea id="kw-nodes" style="min-height:240px;font-family:var(--mono,monospace);font-size:12px">${E(JSON.stringify(w.nodes, null, 2))}</textarea></label>
          <label>运行表单字段 input_schema（JSON 数组；留空则单输入框）
            <textarea id="kw-schema" style="min-height:110px;font-family:var(--mono,monospace);font-size:12px">${E(JSON.stringify(w.input_schema || [], null, 2))}</textarea></label>
        </details>
        <div id="kw-validate" class="kp-meta"></div>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn" onclick="KP.exportWorkflow()">导出 JSON</button>
       <label class="btn" style="cursor:pointer">导入 JSON<input type="file" accept=".json"
         style="display:none" onchange="KP.importWorkflow(event)"></label>
       <button class="btn" onclick="KP.validateWorkflow()">校验</button>
       <button class="btn primary" onclick="KP.saveWorkflow('${id || ""}')">保存</button>`);
    el("kw-nodes").oninput = debounce(() => {
      try { el("kw-dag").innerHTML = renderFlowGraph(JSON.parse(val("kw-nodes"))); }
      catch (e) { el("kw-dag").innerHTML = '<div class="kp-meta">JSON 解析失败</div>'; }
    }, 400);
  }

  function exportWorkflow() {
    try {
      const nodes = JSON.parse(val("kw-nodes"));
      const blob = new Blob([JSON.stringify({ name: val("kw-name"),
        description: val("kw-desc"), nodes }, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (val("kw-name") || "workflow") + ".json";
      a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { toast("节点 JSON 格式错误", "err"); }
  }

  function importWorkflow(ev) {
    const file = ev.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        if (data.name) el("kw-name").value = data.name;
        if (data.description) el("kw-desc").value = data.description;
        el("kw-nodes").value = JSON.stringify(data.nodes || [], null, 2);
        el("kw-dag").innerHTML = renderDag(data.nodes || []);
        toast("已导入", "ok");
      } catch (e) { toast("文件不是有效 JSON", "err"); }
    };
    reader.readAsText(file);
  }

  async function validateWorkflow() {
    let nodes;
    try { nodes = JSON.parse(val("kw-nodes")); }
    catch (e) { return toast("JSON 格式错误", "err"); }
    try {
      const r = await api("/api/platform/workflows/validate", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nodes }) });
      el("kw-validate").innerHTML = r.ok ? '<span class="badge ok">校验通过</span>'
        : r.errors.map((x) => `<div class="badge err" style="margin:2px">${E(x)}</div>`).join("");
    } catch (e) { toast(e.message, "err"); }
  }

  async function saveWorkflow(id) {
    let nodes, inputSchema;
    try { nodes = JSON.parse(val("kw-nodes")); }
    catch (e) { return toast("节点 JSON 格式错误", "err"); }
    try { inputSchema = val("kw-schema") ? JSON.parse(val("kw-schema")) : []; }
    catch (e) { return toast("表单字段 JSON 格式错误", "err"); }
    const payload = { name: val("kw-name"), description: val("kw-desc"),
      nodes, input_schema: inputSchema };
    if (!payload.name) return toast("名称必填", "err");
    try {
      if (id) await api(`/api/platform/workflows/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      else await api("/api/platform/workflows", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      closeModal(); toast("已保存", "ok"); renderWorkflows();
    } catch (e) { toast(e.message, "err"); }
  }

  async function workflowTemplates() {
    const r = await api("/api/platform/workflows/templates");
    openDrawerLike("工作流模板", `<div class="kp-list">
      ${r.items.map((t) => `<div class="kp-row"><div><b>${E(t.name)}</b>
        <small>${E(t.description)}</small></div><span class="spacer"></span>
        <button class="btn sm primary" onclick="KP.useTemplate('${t.id}')">使用</button></div>`).join("")}
      </div>`, `<button class="btn" onclick="closeModal()">关闭</button>`);
    S.templates = r.items;
  }

  function useTemplate(id) {
    const t = (S.templates || []).find((x) => x.id === id);
    if (!t) return;
    closeModal();
    workflowForm("", { name: t.name, description: t.description,
      nodes: t.nodes, input_schema: t.input_schema || [] });
  }

  async function workflowDelete(id) {
    if (!confirm("确认删除该工作流？")) return;
    try {
      await api(`/api/platform/workflows/${id}`, { method: "DELETE" });
      toast("已删除", "ok"); renderWorkflows();
    } catch (e) { toast(e.message, "err"); }
  }

  function workflowRun(id) {
    openDrawerLike("运行工作流", `
      <div class="kp-form"><label>输入<textarea id="kp-wf-q" style="min-height:100px"></textarea></label>
        <div id="kp-wf-trace"></div></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" onclick="KP.doWorkflowRun('${id}')">开始</button>`);
  }

  async function doWorkflowRun(id) {
    const q = val("kp-wf-q");
    if (!q) return toast("请输入输入内容", "err");
    const trace = makeTrace(el("kp-wf-trace"), {
      onApproval: (ev) => {
        const row = document.createElement("div");
        row.innerHTML = `<button class="btn sm primary">通过并继续</button>
          <button class="btn sm">驳回</button>`;
        row.querySelector(".primary").onclick = async () => {
          closeModal();
          try {
            const resp = await fetch(`/api/platform/runs/${ev.run_id}/approve`, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ approved: true }) });
            const reader = resp.body.getReader(); const dec = new TextDecoder();
            let buf = "";
            for (;;) {
              const { done, value } = await reader.read();
              if (done) break;
              buf += dec.decode(value, { stream: true });
              let at;
              while ((at = buf.indexOf("\n\n")) >= 0) {
                const chunk = buf.slice(0, at); buf = buf.slice(at + 2);
                chunk.split("\n").filter((l) => l.startsWith("data: "))
                  .forEach((l) => { try { trace.event(JSON.parse(l.slice(6))); } catch (e) {} });
              }
            }
          } catch (e) { toast(e.message, "err"); }
          openDrawerLike("工作流运行结果", `<div id="kp-wf-done"></div>`,
            `<button class="btn" onclick="closeModal()">关闭</button>`);
          el("kp-wf-done").appendChild(traceNode(trace));
        };
        row.querySelectorAll(".btn")[1].onclick = async () => {
          await fetch(`/api/platform/runs/${ev.run_id}/approve`, { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ approved: false }) });
          toast("已驳回", "ok"); closeModal();
        };
        el("kp-wf-trace").appendChild(row);
      },
    });
    try {
      await stream(`/api/platform/workflows/${id}/run`, { input: q }, (ev) => trace.event(ev));
    } catch (e) { trace.event({ type: "error", message: e.message }); }
  }

  function traceNode(trace) {
    const box = document.createElement("div");
    box.className = "kp-pre";
    box.textContent = trace.text() || "（无输出）";
    return box;
  }

  /* ══════════════════════════ automation ══════════════════════════ */

  async function renderAutomation() {
    const v = el("view");
    v.innerHTML = `${head("自动化", `
      <button class="btn sm ${S.autoTab === "schedules" ? "primary" : ""}" onclick="KP.autoTab('schedules')">定时任务</button>
      <button class="btn sm ${S.autoTab === "artifacts" ? "primary" : ""}" onclick="KP.autoTab('artifacts')">运行产物</button>
      <button class="btn primary sm" onclick="KP.scheduleForm()">＋ 新建定时任务</button>`)}
      <div id="kp-auto-body">${empty("加载中…", "")}</div>`;
    if (S.autoTab === "artifacts") await paintArtifacts();
    else await paintSchedules();
  }

  function autoTab(tab) { S.autoTab = tab; renderAutomation(); }

  async function paintSchedules() {
    const host = el("kp-auto-body");
    try { S.schedules = (await api("/api/platform/schedules")).items; }
    catch (e) { host.innerHTML = empty("加载失败", e.message); return; }
    host.innerHTML = S.schedules.length ? `<div class="kp-list">${S.schedules.map((s) => `
      <div class="kp-row"><div><b>${E(s.name)}</b>
        <small>${E(s.kind)} · ${E(s.cron)} · 下次 ${fmt(s.next_run)}</small></div>
        <span class="spacer"></span>${badge(s.enabled ? "active" : "disabled")}
        <button class="btn sm" onclick="KP.runScheduleNow('${s.id}')">立即运行</button>
        <button class="btn sm ghost" onclick="KP.scheduleRuns('${s.id}')">记录</button>
        <button class="btn sm ghost" onclick="KP.scheduleToggle('${s.id}',${s.enabled ? 0 : 1})">${s.enabled ? "停用" : "启用"}</button>
      </div>`).join("")}</div>` : empty("还没有定时任务", "让智能体或工作流按 cron 自动运行");
  }

  async function scheduleForm() {
    const [agents, workflows] = await Promise.all([
      api("/api/platform/agents").then((r) => r.items),
      api("/api/platform/workflows").then((r) => r.items),
    ]);
    openDrawerLike("新建定时任务", `
      <div class="kp-form">
        <label>名称<input id="ksc-name"></label>
        <div class="row2"><label>类型<select id="ksc-kind">
          <option value="agent">智能体</option><option value="workflow">工作流</option></select></label>
          <label>Cron（分 时 日 月 周）<input id="ksc-cron" value="0 9 * * *"></label></div>
        <label>目标<select id="ksc-ref">
          ${agents.map((a) => `<option value="${a.id}" data-kind="agent">智能体 · ${E(a.name)}</option>`).join("")}
          ${workflows.map((w) => `<option value="${w.id}" data-kind="workflow">工作流 · ${E(w.name)}</option>`).join("")}
        </select></label>
        <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="ksc-enabled" checked> 启用</label>
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.saveSchedule()">保存</button>`);
  }

  async function saveSchedule() {
    const opt = el("ksc-ref").selectedOptions[0];
    const payload = { name: val("ksc-name"), cron: val("ksc-cron"),
      kind: opt ? opt.dataset.kind : "agent", ref_id: opt ? opt.value : "",
      enabled: el("ksc-enabled").checked };
    if (!payload.name) return toast("名称必填", "err");
    try {
      await api("/api/platform/schedules", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      closeModal(); toast("已创建", "ok"); renderAutomation();
    } catch (e) { toast(e.message, "err"); }
  }

  async function runScheduleNow(id) {
    try {
      const r = await api(`/api/platform/schedules/${id}/run`, { method: "POST" });
      toast(r.ok ? "运行完成" : "运行失败：" + (r.error || ""), r.ok ? "ok" : "err");
      paintSchedules();
    } catch (e) { toast(e.message, "err"); }
  }

  async function scheduleToggle(id, enabled) {
    try {
      await api(`/api/platform/schedules/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !!enabled }) });
      paintSchedules();
    } catch (e) { toast(e.message, "err"); }
  }

  async function scheduleRuns(id) {
    const r = await api(`/api/platform/schedules/${id}/runs`);
    openDrawerLike("运行记录", `<div class="kp-list">${arr(r.items).map((x) => `
      <div class="kp-row"><b>${E(x.status)}</b><small>${fmt(x.started_at)}</small>
        <span class="spacer"></span><small>${E(x.run_id || "")}</small></div>`).join("") || '<div class="kp-meta">暂无记录</div>'}</div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>`);
  }

  async function paintArtifacts() {
    const host = el("kp-auto-body");
    try { S.artifacts = (await api("/api/platform/artifacts")).items; }
    catch (e) { host.innerHTML = empty("加载失败", e.message); return; }
    host.innerHTML = S.artifacts.length ? `<div class="kp-list">${S.artifacts.map((a) => `
      <div class="kp-row"><div><b>${E(a.name)}</b><small>${fmt(a.created_at)} · ${num(a.bytes)} B</small></div>
        <span class="spacer"></span>
        <a class="btn sm" href="/api/platform/artifacts/${a.id}/download">下载</a></div>`).join("")}</div>`
      : empty("还没有产物", "运行智能体或工作流后会自动生成 Markdown 产物");
  }

  /* ══════════════════════════ ops ══════════════════════════ */

  async function renderOps() {
    const tabs = [["overview", "概览"], ["reviews", "审核队列"],
      ["notifications", "通知"], ["shares", "分享"], ["audit", "审计日志"],
      ["users", "用户与权限"], ["artifacts", "产物"]];
    const v = el("view");
    v.innerHTML = `${head("运营后台", `<a class="btn sm" href="/api/platform/export/qa">导出问答 CSV</a>
      <button class="btn sm" onclick="KP.retention()">清理 90 天前运行</button>
      <button class="btn sm" onclick="KP.exportData()">导出平台数据</button>`)}
      <div class="kp-tabs">${tabs.map(([k, label]) => `<button class="kp-tab
        ${S.opsTab === k ? "on" : ""}" onclick="KP.opsTab('${k}')">${label}</button>`).join("")}</div>
      <div id="kp-ops-body">${empty("加载中…", "")}</div>`;
    if (S.opsTab === "reviews") await paintReviews();
    else if (S.opsTab === "notifications") await paintNotifications();
    else if (S.opsTab === "shares") await paintShares();
    else if (S.opsTab === "audit") await paintAudit();
    else if (S.opsTab === "users") await paintUsers();
    else if (S.opsTab === "artifacts") await paintArtifacts();
    else await paintOverview();
  }

  function opsTab(tab) { S.opsTab = tab; renderOps(); }

  async function retention() {
    if (!confirm("将删除 90 天前的运行记录、步骤与产物文件，确认？")) return;
    try {
      const r = await api("/api/platform/maintenance/retention", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: 90 }) });
      toast(`已清理 ${r.runs} 条运行、${r.files} 个文件`, "ok");
      if (S.opsTab === "overview") paintOverview();
    } catch (e) { toast(e.message, "err"); }
  }

  async function exportData() {
    try {
      const data = await api("/api/platform/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "knowlens-platform-export.json";
      a.click(); URL.revokeObjectURL(a.href);
      toast("已导出", "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  async function paintOverview() {
    const host = el("kp-ops-body");
    try { S.overview = await api("/api/platform/overview"); }
    catch (e) { host.innerHTML = empty("加载失败", e.message); return; }
    const o = S.overview;
    const c = o.counts;
    host.innerHTML = `
      <div class="kp-kpis">
        <div class="kp-kpi"><b>${num(c.agents)}</b><small>智能体（${c.agents_published} 已发布）</small></div>
        <div class="kp-kpi"><b>${num(c.skills)}</b><small>已安装技能</small></div>
        <div class="kp-kpi"><b>${num(c.runs)}</b><small>累计运行（今日 ${c.runs_today}）</small></div>
        <div class="kp-kpi"><b>${(o.usage.success_rate * 100).toFixed(0)}%</b><small>成功率</small></div>
        <div class="kp-kpi"><b>${num(o.usage.tokens_in + o.usage.tokens_out)}</b><small>Token 用量</small></div>
        <div class="kp-kpi"><b>${num(c.pending_memories)}</b><small>待审核记忆</small></div>
        <div class="kp-kpi"><b>${num(c.reviews)}</b><small>待审智能体</small></div>
        <div class="kp-kpi"><b>${num(c.schedules)}</b><small>启用定时任务</small></div>
      </div>
      <div class="card" style="padding:14px;margin-bottom:14px">
        <b style="font-size:13px">近 14 天运行</b>
        <div id="kp-trend" style="height:240px;margin-top:8px"></div>
      </div>
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:14px">
        <div class="card" style="padding:14px"><b style="font-size:13px">活跃智能体</b>
          ${arr(o.top_agents).map((a) => `<div class="kp-row" style="margin-top:6px">
            <b>${E(a.name)}</b><span class="spacer"></span><small>${num(a.runs)} 次</small></div>`).join("") || '<div class="kp-meta">暂无</div>'}</div>
        <div class="card" style="padding:14px"><b style="font-size:13px">常用技能</b>
          ${arr(o.top_skills).map((s) => `<div class="kp-row" style="margin-top:6px">
            <b>${E(s.name)}</b><span class="spacer"></span><small>${num(s.installs)} 次</small></div>`).join("") || '<div class="kp-meta">暂无</div>'}</div>
      </div>
      <div class="card" style="padding:14px;margin-top:14px"><b style="font-size:13px">最近运行</b>
        <div class="kp-list" style="margin-top:8px">${arr(o.recent_runs).map((r) => `
          <div class="kp-row"><b>${E(r.kind)}</b><small>${E(r.title || r.ref_id)}</small>
            <span class="spacer"></span>${badge(r.status)}<small>${fmt(r.created_at)}</small>
            <button class="btn sm ghost" onclick="KP.runDetail('${r.id}')">查看</button></div>`).join("")}</div></div>`;
    if (window.echarts) {
      initChart("kp-trend", {
        color: ["#2f7df6", "#e25563"],
        tooltip: { trigger: "axis" },
        legend: { data: ["运行", "失败"], right: 0, top: 0,
          textStyle: { color: cssVar("--text-3"), fontSize: 10 } },
        grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
        xAxis: { type: "category", data: o.trend.map((x) => x.day.slice(5)),
          axisLine: { show: false }, axisTick: { show: false } },
        yAxis: { type: "value", minInterval: 1,
          splitLine: { lineStyle: { color: cssVar("--border"), type: "dashed" } } },
        series: [
          { name: "运行", type: "line", smooth: true, areaStyle: { opacity: 0.14 },
            data: o.trend.map((x) => x.runs) },
          { name: "失败", type: "bar", barWidth: 8,
            itemStyle: { color: "rgba(226,85,99,.55)", borderRadius: [4, 4, 0, 0] },
            data: o.trend.map((x) => x.failed) },
        ],
      });
    }
  }


  async function paintNotifications() {
    const host = el("kp-ops-body");
    let data = { items: [], unread: 0 };
    try { data = await api("/api/platform/notifications?limit=100"); }
    catch (e) { host.innerHTML = empty(e.message, ""); return; }
    host.innerHTML = `<div class="kp-head"><span class="kp-meta">${data.unread} 条未读</span>
      <span class="spacer"></span>
      <button class="btn sm" onclick="KP.readAllNotifications()">全部标记已读</button></div>
      <div class="kp-list">${arr(data.items).map((n) => `
        <div class="kp-row" style="${n.read ? "opacity:.62" : ""}">
          <span class="dot" style="width:8px;height:8px;border-radius:50%;background:${n.read ? "var(--text-3)" : "var(--brand)"}"></span>
          <div><b>${E(n.title)}</b><small>${E(n.body || "")}</small></div>
          <span class="spacer"></span><small>${fmt(n.created_at)}</small>
          ${n.read ? "" : `<button class="btn sm ghost" onclick="KP.readNotification('${n.id}')">已读</button>`}
        </div>`).join("") || empty("没有通知", "运行失败时会在这里提醒")}</div>`;
  }

  async function readNotification(id) {
    try {
      await api(`/api/platform/notifications/${id}/read`, { method: "POST" });
      paintNotifications();
    } catch (e) { toast(e.message, "err"); }
  }

  async function readAllNotifications() {
    try {
      await api("/api/platform/notifications/read-all", { method: "POST" });
      paintNotifications();
    } catch (e) { toast(e.message, "err"); }
  }


  async function paintShares() {
    const host = el("kp-ops-body");
    let data = { items: [] };
    try { data = await api("/api/platform/shares"); }
    catch (e) { host.innerHTML = empty(e.message, ""); return; }
    host.innerHTML = `<div class="card" style="padding:14px"><div class="kp-list">
      ${(data.items || []).map((x) => `<div class="kp-row">
        <b>${E(x.title || "分享")}</b>
        <small>${fmt(x.created_at)} · ${x.views || 0} 次查看${x.password_hash ? " · 有密码" : ""}${x.expires_at ? " · 至 " + E(x.expires_at) : ""}</small>
        <span class="spacer"></span>
        <a class="btn sm" href="/share/${E(x.token)}" target="_blank" rel="noopener">打开</a>
        <button class="btn sm ghost" onclick="KP.deleteShare('${x.id}')">删除</button>
      </div>`).join("") || '<div class="kp-meta">还没有分享</div>'}
    </div></div>`;
  }

  async function deleteShare(id) {
    if (!confirm("删除后链接立即失效，确认？")) return;
    try {
      await api(`/api/platform/shares/${id}`, { method: "DELETE" });
      toast("已删除", "ok"); paintShares();
    } catch (e) { toast(e.message, "err"); }
  }

  async function paintReviews() {
    const host = el("kp-ops-body");
    try { S.reviews = await api("/api/platform/reviews"); }
    catch (e) { host.innerHTML = empty(e.message, "需要管理员权限"); return; }
    host.innerHTML = `<div class="card" style="padding:14px;margin-bottom:14px">
      <b style="font-size:13px">待审智能体</b>
      ${arr(S.reviews.agents).map((a) => `<div class="kp-row" style="margin-top:6px">
        <b>${E(a.name)}</b><small>${E(a.title || "")}</small><span class="spacer"></span>
        <button class="btn sm primary" onclick="KP.review('agent','${a.id}',1)">通过</button>
        <button class="btn sm ghost" onclick="KP.review('agent','${a.id}',0)">驳回</button></div>`).join("") || '<div class="kp-meta">没有待审智能体</div>'}</div>
      <div class="card" style="padding:14px"><b style="font-size:13px">待审记忆</b>
      ${arr(S.reviews.memories).map((m) => `<div class="kp-row" style="margin-top:6px">
        <b>${E(m.scope)}</b><small>${E(m.content.slice(0, 120))}</small><span class="spacer"></span>
        <button class="btn sm primary" onclick="KP.review('memory','${m.id}',1)">通过</button>
        <button class="btn sm ghost" onclick="KP.review('memory','${m.id}',0)">驳回</button></div>`).join("") || '<div class="kp-meta">没有待审记忆</div>'}</div>`;
  }

  async function review(kind, id, approved) {
    try {
      await api(`/api/platform/reviews/${kind}/${id}`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: !!approved }) });
      toast("已处理", "ok"); paintReviews();
    } catch (e) { toast(e.message, "err"); }
  }

  async function paintAudit() {
    const host = el("kp-ops-body");
    try { S.audits = (await api("/api/platform/audit?limit=200")).items; }
    catch (e) { host.innerHTML = empty(e.message, "需要管理员权限"); return; }
    host.innerHTML = `<div class="card" style="padding:14px"><div class="kp-list">
      ${S.audits.map((a) => `<div class="kp-row"><small>${fmt(a.created_at)}</small>
        <b>${E(a.user_id)}</b><span class="kp-tag">${E(a.action)}</span>
        <small>${E(a.target)}</small><span class="spacer"></span>
        <small>${E((a.detail || "").slice(0, 80))}</small></div>`).join("") || '<div class="kp-meta">暂无日志</div>'}
      </div></div>`;
  }

  async function paintUsers() {
    const host = el("kp-ops-body");
    try { S.users = (await api("/api/platform/users")).items; }
    catch (e) { host.innerHTML = empty(e.message, "需要管理员权限"); return; }
    host.innerHTML = `<div class="kp-head"><span class="kp-meta">当前身份由 X-User-Id 决定，可在设置中切换</span>
      <span class="spacer"></span><button class="btn primary sm" onclick="KP.userForm()">＋ 新增用户</button></div>
      <div class="card" style="padding:14px"><div class="kp-list">
      ${S.users.map((u) => `<div class="kp-row"><b>${E(u.name)}</b><small>${E(u.id)}</small>
        <span class="spacer"></span><span class="kp-tag">${E(u.role)}</span>${badge(u.active ? "active" : "disabled")}
        <button class="btn sm ghost" onclick="KP.roleForm('${u.id}','${E(u.role)}')">改角色</button>
        <button class="btn sm ghost" onclick="KP.disableUser('${u.id}')">停用</button></div>`).join("")}
      </div></div>`;
  }

  function userForm() {
    openDrawerLike("新增用户", `<div class="kp-form">
      <label>姓名<input id="ku-name"></label>
      <label>角色<select id="ku-role"><option value="viewer">只读</option>
        <option value="editor">编辑</option><option value="admin">管理员</option></select></label>
      </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.saveUser()">保存</button>`);
  }

  async function saveUser() {
    try {
      await api("/api/platform/users", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: val("ku-name"), role: val("ku-role") }) });
      closeModal(); toast("已添加", "ok"); paintUsers();
    } catch (e) { toast(e.message, "err"); }
  }

  function roleForm(id, role) {
    openDrawerLike("修改角色", `<div class="kp-form"><label>角色<select id="ku-role2">
      ${["viewer", "editor", "admin"].map((r) => `<option value="${r}"
        ${r === role ? "selected" : ""}>${r}</option>`).join("")}</select></label></div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="KP.saveRole('${id}')">保存</button>`);
  }

  async function saveRole(id) {
    try {
      await api(`/api/platform/users/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: val("ku-role2") }) });
      closeModal(); toast("已更新", "ok"); paintUsers();
    } catch (e) { toast(e.message, "err"); }
  }

  async function disableUser(id) {
    try {
      await api(`/api/platform/users/${id}`, { method: "DELETE" });
      toast("已停用", "ok"); paintUsers();
    } catch (e) { toast(e.message, "err"); }
  }

  async function runDetail(runId) {
    const d = await api(`/api/platform/runs/${runId}`);
    openDrawerLike("运行详情", `
      <div class="kp-kpis"><div class="kp-kpi"><b>${E(d.run.status)}</b><small>状态</small></div>
        <div class="kp-kpi"><b>${num(d.run.tokens_in + d.run.tokens_out)}</b><small>Token</small></div>
        <div class="kp-kpi"><b>${num(d.run.ms)}</b><small>毫秒</small></div></div>
      <div class="kp-drawer-sec"><h5>步骤</h5>
        ${arr(d.steps).map((s) => `<div class="kp-row"><span class="kp-tag">${E(s.type)}</span>
          <b>${E(s.name)}</b><span class="spacer"></span><small>${num(s.ms)} ms</small></div>
          <div class="kp-pre">${E(String(s.output || "").slice(0, 800))}</div>`).join("") || '<div class="kp-meta">无步骤</div>'}</div>
      ${arr(d.artifacts).length ? `<div class="kp-drawer-sec"><h5>产物</h5>${d.artifacts.map((a) =>
        `<a class="btn sm" href="/api/platform/artifacts/${a.id}/download">${E(a.name)}</a>`).join(" ")}</div>` : ""}`,
      `<button class="btn" onclick="closeModal()">关闭</button>`);
  }


  function guideGo(view) {
    closeModal();
    if (typeof switchView === "function") switchView(view);
  }

  function openGuide() {
    openModal("使用指南 · KnowLens", `
      <div style="max-height:62vh;overflow:auto;font-size:13px;line-height:1.65">
        <p><b>一句话：</b>把部门文档、网页和录音变成“有出处的回答”，再把知识包装成智能体、专家、团队和自动化流程。</p>

        <div class="kp-drawer-sec"><h5>10 分钟上手</h5>
          <ol style="padding-left:18px;margin:0">
            <li>在「知识空间」新建空间与库，拖入资料，或点「🔗 链接入库」。</li>
            <li>等文档状态变为「就绪」，在「智能问答」直接提问；点引用可回到原文。</li>
            <li>在「智能体」用模板创建一个助手，绑定知识、勾选技能，测试后发布。</li>
            <li>在「技能市场」安装一个开源技能，回到智能体勾上再试一次。</li>
            <li>在「工作流」用模板创建「知识简报」，运行并走到审批节点。</li>
            <li>按 <b>Ctrl/Cmd+K</b>，搜索刚才的文档、智能体或技能。</li>
          </ol></div>

        <div class="kp-drawer-sec"><h5>六条主线</h5>
          <div class="kp-row"><b>知识空间</b><small>空间是隔离边界；库是检索单位；支持文档、图片、录音、网页。</small>
            <span class="spacer"></span><button class="btn sm" onclick="KP.guideGo('kb')">前往</button></div>
          <div class="kp-row"><b>智能问答</b><small>五阶段问答，回答带页码；选中文字可问“这个数怎么来的”。</small>
            <span class="spacer"></span><button class="btn sm" onclick="KP.guideGo('chat')">前往</button></div>
          <div class="kp-row"><b>智能体</b><small>角色 + 知识 + 技能 + 工具 + 记忆；可测试、发布、回滚、评测。</small>
            <span class="spacer"></span><button class="btn sm" onclick="KP.guideGo('agents')">前往</button></div>
          <div class="kp-row"><b>技能 / MCP</b><small>1,137 个开源技能；可打包、可自定义、可接入 MCP 远程服务。</small>
            <span class="spacer"></span><button class="btn sm" onclick="KP.guideGo('skills')">前往</button></div>
          <div class="kp-row"><b>专家 / 团队 / 工作流</b><small>把智能体上架、组合或编排成带审批的流程。</small>
            <span class="spacer"></span><button class="btn sm" onclick="KP.guideGo('experts')">前往</button></div>
          <div class="kp-row"><b>自动化 / 运营</b><small>cron 定时运行；审核、审计、通知、权限、导出与清理。</small>
            <span class="spacer"></span><button class="btn sm" onclick="KP.guideGo('ops')">前往</button></div>
        </div>

        <div class="kp-drawer-sec"><h5>常用技巧</h5>
          <ul style="padding-left:18px;margin:0">
            <li>提问前勾选库/标签，范围越准，答案越稳。</li>
            <li>同一对话框可多轮追问；Enter 发送，Shift+Enter 换行。</li>
            <li><b>Ctrl/Cmd+K</b> 全局搜索；右上角 ◐ 切换深浅色；设置里切换中英文。</li>
            <li>知识库文档可多选后批量加/删标签。</li>
            <li>工作流的审批节点会暂停，通过后继续。</li>
          </ul></div>

        <div class="kp-drawer-sec"><h5>边界</h5>
          <p class="kp-meta" style="margin:0">不执行任意代码；网页入库只支持公开页面；MCP 只支持远程 HTTP/SSE；当前为轻量身份（X-User-Id），未接 SSO/JWT；超大库建议再上向量召回。</p></div>

        <div class="kp-drawer-sec"><h5>开发者</h5>
          <p class="kp-meta" style="margin:0">接口文档：<a href="/docs" target="_blank" rel="noopener">/docs</a>；平台 API 前缀 <code>/api/platform</code>；运维手册见 <code>docs/platform-operations.md</code>。</p></div>
      </div>`,
      `<button class="btn" onclick="closeModal()">开始使用</button>
       <button class="btn primary" onclick="KP.guideGo('kb')">去建知识库</button>`);
  }

  /* ══════════════════════════ command palette ══════════════════════════ */

  let paletteHits = [];
  let paletteIndex = 0;

  function initPalette() {
    const wrap = document.createElement("div");
    wrap.className = "kp-palette-mask";
    wrap.id = "kp-palette-mask";
    wrap.innerHTML = `<div class="kp-palette"><input id="kp-palette-q"
      placeholder="搜索文档、智能体、技能、专家、团队、工作流、会话…">
      <div class="hits" id="kp-palette-hits"></div>
      <div class="kp-hit-hint">↑↓ 选择 · Enter 打开 · Esc 关闭 · Ctrl/Cmd+K 唤起</div></div>`;
    document.body.appendChild(wrap);
    const input = el("kp-palette-q");
    input.addEventListener("input", debounce(async () => {
      const q = input.value.trim();
      if (!q) { el("kp-palette-hits").innerHTML = ""; paletteHits = []; return; }
      try {
        const r = await api("/api/platform/search?q=" + encodeURIComponent(q));
        paletteHits = r.items;
        paletteIndex = 0;
        paintPalette();
      } catch (e) { el("kp-palette-hits").innerHTML = `<div class="kp-hit-hint">${E(e.message)}</div>`; }
    }, 250));
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") closePalette();
      else if (ev.key === "ArrowDown") { ev.preventDefault(); paletteIndex = Math.min(paletteIndex + 1, paletteHits.length - 1); paintPalette(); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); paletteIndex = Math.max(0, paletteIndex - 1); paintPalette(); }
      else if (ev.key === "Enter") { ev.preventDefault(); openHit(paletteHits[paletteIndex]); }
    });
    wrap.addEventListener("click", (ev) => { if (ev.target === wrap) closePalette(); });
    document.addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        wrap.classList.add("on");
        setTimeout(() => input.focus(), 30);
      }
    });
  }

  const KIND_LABEL = { agent: "智能体", skill: "技能", expert: "专家",
    team: "团队", workflow: "工作流", document: "文档", session: "会话",
    memory: "记忆", tool: "工具" };

  function paintPalette() {
    const host = el("kp-palette-hits");
    if (!paletteHits.length) { host.innerHTML = '<div class="kp-hit-hint">没有结果</div>'; return; }
    host.innerHTML = paletteHits.map((h, i) => `<div class="kp-hit ${i === paletteIndex ? "on" : ""}"
      onclick="KP.openHit(${i})"><span class="k">${E(KIND_LABEL[h.kind] || h.kind)}</span>
      <div class="t"><b>${E(h.title)}</b><small>${E(h.subtitle || "")}</small></div></div>`).join("");
  }

  function openHit(hit) {
    if (!hit) return;
    closePalette();
    const view = { agent: "agents", skill: "skills", expert: "experts",
      team: "teams", workflow: "workflows", document: "kb",
      session: "agents", memory: "evolve", tool: "agents" }[hit.kind];
    if (view && typeof switchView === "function") switchView(view);
    setTimeout(() => {
      if (hit.kind === "agent") useTarget("agent", hit.id);
      else if (hit.kind === "skill") skillDetail(hit.id);
      else if (hit.kind === "expert") useTarget("expert", hit.id);
      else if (hit.kind === "team") useTarget("team", hit.id);
      else if (hit.kind === "workflow") useTarget("workflow", hit.id);
    }, 120);
  }

  function closePalette() {
    const mask = el("kp-palette-mask");
    if (mask) mask.classList.remove("on");
  }


  /* ══════════════════════════ unified chat workspace ══════════════════════════ */

  const STAGE_LABEL = {
    profile: "解析表格", code_generation: "生成分析代码",
    code_execution: "执行代码", report_generation: "生成结论与依据",
    chart_generation: "判断图表", knowledge: "检索知识库",
    knowledge_merge: "合并表格与知识库",
  };

  const CHAT_KINDS = {
    kb: { label: "知识", icon: "📚", hint: "对话型 · 引用出处" },
    agent: { label: "智能体", icon: "🤖", hint: "对话型 · 支持多轮与附件" },
    expert: { label: "专家", icon: "🎓", hint: "对话型 · 由专家智能体回答" },
    team: { label: "团队", icon: "👥", hint: "对话型 · 过程见右侧轨迹" },
    workflow: { label: "工作流", icon: "🔀", hint: "运行型 · 一次性表单" },
    excel: { label: "问数", icon: "📊", hint: "Excel 问数 · 上传表格后直接问" },
    external: { label: "外部", icon: "🔗", hint: "外部运行时 · zdata 多智能体" },
  };

  function chatState() {
    if (!S.chat) {
      S.chat = { kind: "kb", id: "", name: "", avatar: "📚", status: "",
        sessionId: "", messages: [], files: [], running: false,
        workflow: null, runId: "", steps: [], kbIds: [] };
    }
    return S.chat;
  }


  function kindIcon(kind) {
    const svg = {
      kb: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Z"/><path d="M4 5.5v15"/>',
      agent: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M9 3v4M15 3v4M9 13h.01M15 13h.01"/>',
      expert: '<circle cx="12" cy="8" r="3.2"/><path d="M5 20a7 7 0 0 1 14 0"/>',
      team: '<circle cx="8" cy="9" r="2.6"/><circle cx="16" cy="9" r="2.6"/><path d="M3 20a5 5 0 0 1 10 0M11 20a5 5 0 0 1 10 0"/>',
      workflow: '<rect x="3" y="3" width="6" height="5" rx="1.5"/><rect x="15" y="16" width="6" height="5" rx="1.5"/><path d="M9 5.5h4a2 2 0 0 1 2 2v8.5M6 8v8a2 2 0 0 0 2 2h7"/>',
      excel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 4v16M15 4v16"/>',
      external: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.2 1.2"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.2-1.2"/>',
    };
    return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
      stroke-linejoin="round">${svg[kind] || svg.agent}</svg>`;
  }

  function inlineTargetHTML() {
    const c = chatState();
    const label = c.kind === "kb" ? "知识问答" : (c.name || "选择目标");
    const meta = c.kind === "kb" ? "知识" : CHAT_KINDS[c.kind].label;
    const icon = kindIcon(c.kind);
    return `<span class="av">${icon}</span><span class="nm">${E(label)}</span>
      <span class="ty">${E(meta)}</span><span class="cr">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
          <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round"/></svg></span>`;
  }

  function syncInlineTarget() {
    const node = el("kp-inline-target");
    if (node) node.innerHTML = inlineTargetHTML();
  }

  function targetKey(c) {
    return c.kind + ":" + (c.id || "");
  }

  function saveDraft() {
    const c = chatState();
    S.drafts = S.drafts || {};
    const node = c.kind === "kb" ? el("q-input") : el("kp-ws-input");
    if (node) S.drafts[targetKey(c)] = node.value;
  }

  function restoreDraft() {
    const c = chatState();
    S.drafts = S.drafts || {};
    const node = c.kind === "kb" ? el("q-input") : el("kp-ws-input");
    if (node) {
      node.value = S.drafts[targetKey(c)] || "";
      setTimeout(() => { try { node.focus(); } catch (e) {} }, 30);
    }
  }

  function mountChatTargets() {
    const bar = el("kp-target-bar");
    if (bar) { bar.innerHTML = ""; bar.style.display = "none"; }
    const c = chatState();
    const msgs = el("msgs"), comp = el("composer");
    let ws = el("kp-ws");
    const platformMode = c.kind !== "kb";
    if (platformMode) {
      if (msgs) msgs.style.display = "none";
      if (comp) comp.style.display = "none";
      if (!ws) {
        ws = document.createElement("div");
        ws.id = "kp-ws";
        ws.className = "kp-ws";
        el("chat-main").appendChild(ws);
      }
      ws.style.display = "grid";
      renderWorkspace();
    } else {
      if (msgs) msgs.style.display = "";
      if (comp) comp.style.display = "";
      if (ws) ws.style.display = "none";
      syncInlineTarget();
      const kbSubs = el("kp-kb-subs");
      if (kbSubs) kbSubs.innerHTML = subControlsHTML();
    }
    restoreDraft();
  }


  async function openExcelPicker() {
    let items = [];
    try { items = (await api("/api/platform/excel/files")).items || []; }
    catch (e) { toast(e.message, "err"); return; }
    S._excelItems = items;
    openDrawerLike("选择 Excel 文件", `
      <div class="kp-head"><span class="kp-meta">问数只读取表格，不会修改原文件</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn primary sm" onclick="KP.excelUploadDialog()">＋ 上传 Excel</button></div>
      <div class="kp-list">
        ${items.map((f) => `<div class="kp-row"><span style="font-size:18px">📊</span>
          <div><b>${E(f.name)}</b><small>${E(f.kind)} · ${fmt(f.created_at)}</small></div>
          <span class="spacer"></span>
          <button class="btn sm primary" onclick="KP.excelUse('${f.id}')">问这张表</button></div>`).join("")
          || empty("还没有 Excel 文件", "上传 xlsx / xls / csv 开始")}
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>`);
  }

  function excelUse(id) {
    const f = (S._excelItems || []).find((x) => x.id === id);
    if (!f) return;
    closeModal();
    applyTarget("excel", { id: f.id, name: f.name, avatar: "📊",
      status: "Excel 问数" });
  }

  function excelUploadDialog() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".xlsx,.xls,.csv";
    input.onchange = async () => {
      if (!input.files.length) return;
      const fd = new FormData();
      fd.append("files", input.files[0]);
      try {
        const r = await api("/api/platform/excel/upload", { method: "POST", body: fd });
        const item = (r.items || [])[0];
        if (!item || item.error) return toast(item ? item.error : "上传失败", "err");
        closeModal();
        applyTarget("excel", { id: item.id, name: item.name, avatar: "📊",
          status: "Excel 问数" });
      } catch (e) { toast(e.message, "err"); }
    };
    input.click();
  }

  async function loadExcelProfile() {
    const c = chatState();
    const box = el("kp-ws-profile");
    if (!box || !c.id) return;
    box.innerHTML = '<span class="kp-meta">解析工作表…</span>';
    try {
      const [prof, sug] = await Promise.all([
        api("/api/platform/excel/profile?file_id=" + encodeURIComponent(c.id)),
        api("/api/platform/excel/suggest", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_id: c.id }) }),
      ]);
      c.profile = prof;
      box.innerHTML = `<div class="kp-excel-head">
        <b>📊 ${E(c.name)}</b>
        <span class="kp-meta">${prof.sheets.length} 个工作表${prof.truncated ? " · 已截断" : ""}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn sm ghost" onclick="KP.openExcelPicker()">换文件</button></div>
        <div class="kp-excel-sheets">${prof.sheets.map((s) =>
          `<span class="kp-tag" title="${E(s.columns.join("、"))}">${E(s.name)} · ${s.rows} 行 · ${s.columns.length} 列</span>`).join("")}</div>
        <div class="kp-excel-sug">${arr(sug.items).map((q) =>
          `<button class="kp-sug" onclick="KP.excelAskSuggestion('${E(q).replace(/'/g, "\\'")}')">${E(q)}</button>`).join("")}</div>`;
    } catch (e) {
      box.innerHTML = `<span class="kp-meta">解析失败：${E(e.message)}</span>`;
    }
  }

  function excelAskSuggestion(q) {
    const input = el("kp-ws-input");
    if (!input) return;
    input.value = q;
    wsSend();
  }

  function renderExcelResult(m, idx) {
    let html = md2html(m.text || "").html;
    if (m.result !== undefined && m.result !== null) {
      let rows = Array.isArray(m.result) ? m.result
        : (typeof m.result === "object" ? [m.result] : []);
      if (rows.length === 1 && rows[0] && typeof rows[0] === "object") {
        const nested = Object.values(rows[0]).find((v) =>
          Array.isArray(v) && v.length && typeof v[0] === "object");
        if (nested) rows = nested;
      }
      if (rows.length && typeof rows[0] === "object" && !Array.isArray(rows[0])) {
        const cols = Object.keys(rows[0]).slice(0, 12);
        html += `<div class="kp-excel-table"><table><thead><tr>${
          cols.map((c) => `<th>${E(c)}</th>`).join("")}</tr></thead><tbody>${
          rows.slice(0, 40).map((r) => `<tr>${cols.map((c) =>
            `<td>${E(String(r[c] === undefined ? "" : r[c]))}</td>`).join("")}</tr>`).join("")}</tbody></table>
          ${rows.length > 40 ? `<div class="kp-meta">仅显示前 40 行，共 ${rows.length} 行</div>` : ""}</div>`;
      } else if (!rows.length) {
        html += `<div class="kp-pre">${E(JSON.stringify(m.result, null, 2))}</div>`;
      }
    }
    if (m.chart) {
      html += `<div class="kp-excel-chart" id="kp-chart-${idx}" style="height:280px;margin-top:8px"></div>`;
    }
    if (arr(m.citations).length) {
      html += `<div class="kp-cites"><div class="kp-meta">知识库依据</div>${
        m.citations.slice(0, 5).map((c) => `<div class="kp-cite">
          <b>${E(c.doc || "来源")}</b>
          <small>${E([c.heading, c.page ? "第" + c.page + "页" : "",
            c.base].filter(Boolean).join(" · "))}</small>
          <div class="kp-meta">${E(c.excerpt || "")}</div></div>`).join("")}</div>`;
    }
    return html;
  }

  function mountExcelCharts() {
    const c = chatState();
    if (!window.echarts) return;
    c.messages.forEach((m, i) => {
      if (!m.chart) return;
      const dom = el(`kp-chart-${i}`);
      if (!dom) return;
      try {
        const old = echarts.getInstanceByDom(dom);
        if (old) old.dispose();
        echarts.init(dom).setOption(m.chart);
      } catch (e) {}
    });
  }


  async function loadQuickTargets() {
    const box = el("kp-quick");
    if (!box) return;
    try {
      const [agents, teams, workflows, excel] = await Promise.all([
        api("/api/platform/agents").then((r) => r.items || []),
        api("/api/platform/teams").then((r) => r.items || []),
        api("/api/platform/workflows").then((r) => r.items || []),
        api("/api/platform/excel/files").then((r) => r.items || []),
      ]);
      const items = [
        ...agents.map((x) => ({ kind: "agent", id: x.id, name: x.name,
          icon: x.avatar || "🤖", at: x.updated_at })),
        ...teams.map((x) => ({ kind: "team", id: x.id, name: x.name,
          icon: "👥", at: x.updated_at })),
        ...workflows.map((x) => ({ kind: "workflow", id: x.id, name: x.name,
          icon: "🔀", at: x.updated_at })),
        ...excel.map((x) => ({ kind: "excel", id: x.id, name: x.name,
          icon: "📊", at: x.created_at })),
      ].sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
      const favs = JSON.parse(localStorage.getItem("knowlens-favs") || "[]");
      items.sort((a, b) => {
        const fa = favs.includes(a.kind + ":" + a.id) ? 1 : 0;
        const fb = favs.includes(b.kind + ":" + b.id) ? 1 : 0;
        return fb - fa;
      });
      const shown = items.slice(0, 6);
      S.quick = shown;
      box.innerHTML = shown.map((x) => {
        const key = x.kind + ":" + x.id;
        const fav = favs.includes(key);
        return `<div class="kp-quick-item" onclick="KP.quickOpen('${x.kind}','${x.id}')">
          <span class="ic">${kindIcon(x.kind)}</span><span class="nm">${E(x.name)}</span>
          <span class="ty">${E(CHAT_KINDS[x.kind] ? CHAT_KINDS[x.kind].label : "")}</span>
          <button class="fav ${fav ? "on" : ""}" title="${fav ? "取消收藏" : "收藏"}"
            onclick="event.stopPropagation();KP.toggleFav('${x.kind}','${x.id}')">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="${fav ? "currentColor" : "none"}"
              stroke="currentColor" stroke-width="1.8"><path d="M12 3l2.7 5.5 6 .9-4.3 4.2 1 6-5.4-2.8-5.4 2.8 1-6L3.3 9.4l6-.9L12 3Z"/></svg>
          </button></div>`;
      }).join("") || '<div class="kp-meta" style="padding:4px 8px">暂无</div>';
    } catch (e) { box.innerHTML = ""; }
  }

  function toggleFav(kind, id) {
    const key = kind + ":" + id;
    const favs = JSON.parse(localStorage.getItem("knowlens-favs") || "[]");
    const idx = favs.indexOf(key);
    if (idx >= 0) favs.splice(idx, 1); else favs.push(key);
    localStorage.setItem("knowlens-favs", JSON.stringify(favs));
    loadQuickTargets();
  }

  function quickOpen(kind, id) {
    if (kind === "excel") {
      const f = (S.quick || []).find((x) => x.id === id);
      return applyTarget("excel", { id, name: f ? f.name : "Excel",
        avatar: "📊", status: "Excel 问数" });
    }
    useTarget(kind, id);
  }

  function copyMessage(index) {
    const m = (chatState().messages || [])[index];
    if (!m) return;
    const text = m.text || "";
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(
      () => toast("已复制", "ok"), () => toast("复制失败", "err"));
    else toast("复制失败", "err");
  }


  function uiIcon(name) {
    const paths = {
      upload: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
      file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z"/><path d="M14 3v6h6"/>',
      link: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.2 1.2"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.2-1.2"/>',
      book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Z"/><path d="M4 5.5v15"/>',
      swap: '<path d="M7 7h11l-3-3M17 17H6l3 3"/>',
      check: '<path d="M5 13l4 4L19 7"/>',
      close: '<path d="M6 6l12 12M18 6L6 18"/>',
    };
    return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
      stroke-linejoin="round">${paths[name] || paths.file}</svg>`;
  }

  function positionPopover(pop, rect) {
    pop.style.visibility = "hidden";
    document.body.appendChild(pop);
    const width = pop.offsetWidth;
    const height = pop.offsetHeight;
    const left = Math.max(8, Math.min(rect.left, innerWidth - width - 8));
    const below = rect.bottom + 8;
    const top = below + height <= innerHeight - 8
      ? below : Math.max(8, rect.top - height - 8);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
    pop.style.visibility = "";
  }

  function closeTargetHub() {
    const pop = el("kp-target-pop");
    if (pop) pop.remove();
  }

// ─────────────────────── 统一知识库 ───────────────────────
  function clearKbPoll() {
    if (S._kbPoll) { clearTimeout(S._kbPoll); S._kbPoll = null; }
  }

  async function renderKbs() {
    clearKbPoll();
    const v = el("view");
    v.innerHTML = `${head("知识库", `<button class="btn primary sm"
      onclick="KP.kbCreateForm()">＋ 新建知识库</button>`)}
      <div id="kb-list">${skeleton()}</div>`;
    try {
      const rows = (await api("/api/kbs")).items || [];
      el("kb-list").innerHTML = rows.length ? `<div class="kp-grid">${rows.map((b) => `
        <div class="kp-card" onclick="KP.openKb('${b.id}')" style="cursor:pointer">
          <div class="kp-card-top"><div class="kp-avatar">📚</div>
            <div style="flex:1;min-width:0"><h4>${E(b.name)}</h4>
              <div class="kp-meta">${E(b.description || "知识库")}</div></div></div>
          <div class="kp-card-foot"><span class="kp-tag">${b.documents} 文档</span>
            <span class="kp-tag">${num(b.chunks)} 分块</span>
            ${b.processing ? `<span class="kp-tag warn">处理中 ${b.processing}</span>` : ""}
            <span class="spacer" style="flex:1"></span>
            <span class="kp-meta">${E((b.created_at || "").slice(0, 10))}</span></div>
        </div>`).join("")}</div>`
        : empty("还没有知识库", "新建后上传 PDF/Word/Excel/图片，两种解析方式共用一套存储");
    } catch (e) { el("kb-list").innerHTML = empty("加载失败", e.message); }
  }

  function kbCreateForm() {
    openDrawerLike("新建知识库", `<div class="kp-form">
      <label>名称<input id="kb-name" placeholder="例如：制度与流程"></label>
      <label>描述<input id="kb-desc"></label>
      <div class="kp-meta">创建后可配置解析、分块、索引与治理项。</div>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.saveKb()">创建</button>`);
  }

  async function saveKb() {
    const name = val("kb-name");
    if (!name) return toast("名称必填", "err");
    try {
      const row = await api("/api/kbs", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description: val("kb-desc") }) });
      closeModal(); toast("已创建", "ok"); openKb(row.id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function openKb(id) {
    clearKbPoll();
    const v = el("view");
    let stat, docs;
    try {
      [stat, docs] = await Promise.all([
        api(`/api/kbs/${id}`),
        api(`/api/kbs/${id}/documents`),
      ]);
    } catch (e) { return toast(e.message, "err"); }
    const b = stat.base || {};
    const tab = (S._kb && S._kb.id === id && S._kb.tab) || "docs";
    S._kb = { id, base: b, stat, docs: (docs && docs.items) || [], tab };
    v.className = "view kp-kb-page";
    v.innerHTML = `${head(b.name, `
      <button class="kp-icon-btn" onclick="KP.renderKbs()" title="返回">←</button>
      <button class="kp-icon-btn" onclick="KP.kbSettings('${id}')" title="设置">⚙</button>
      <details class="kp-menu-wrap"><summary class="btn sm">添加文档 ▾</summary>
        <div class="kp-menu">
          <button onclick="KP.kbManualForm('${id}')">手工录入</button>
          <button onclick="KP.kbUrlForm('${id}')">导入网页</button>
          <button onclick="KP.kbUploadFolder('${id}')">上传文件夹</button>
        </div></details>
      <button class="btn primary sm" onclick="KP.kbUpload('${id}')">＋ 上传文件</button>`)}
      <div class="kp-stat-strip">
        <span><b>${stat.documents || 0}</b> 文档</span>
        <span><b>${stat.ready || 0}</b> 已就绪</span>
        <span><b>${num(stat.chunks || 0)}</b> 分块</span>
        <span><b>${stat.processing || 0}</b> 处理中</span>
      </div>
      <div class="kp-tabs" id="kb-tabs">
        ${["docs:文档", "faq:FAQ", "search:检索测试", "versions:版本", "recycle:回收站"].map((pair) => {
          const [key, label] = pair.split(":");
          if (key === "faq" && (S._kb.base || {}).type !== "faq") return "";
          return `<button data-t="${key}" class="kp-tab ${tab === key ? "on" : ""}"
            onclick="KP.kbTab('${key}')">${label}</button>`;
        }).join("")}
      </div>
      <div id="kb-body"></div>`;
    renderKbBody();
    if ((stat.processing || 0) > 0) {
      S._kbPoll = setTimeout(() => openKb(id), 2500);
    }
  }

  function kbTab(tab) {
    if (!S._kb) return;
    S._kb.tab = tab;
    document.querySelectorAll("#kb-tabs button").forEach((btn) =>
      btn.classList.toggle("on", btn.dataset.t === tab));
    renderKbBody();
  }

  function renderKbBody() {
    const box = el("kb-body");
    if (!box || !S._kb) return;
    if (S._kb.tab === "search") return renderKbSearch(box);
    if (S._kb.tab === "versions") return renderKbVersions(box);
    if (S._kb.tab === "recycle") return renderKbRecycle(box);
    if (S._kb.tab === "faq") return renderKbFaq(box);
    renderKbDocs(box);
  }

  function renderKbDocs(box) {
    const docs = S._kb.docs || [];
    box.innerHTML = `<div class="kp-toolbar">
        <input id="kb-doc-q" class="kp-hub-search" placeholder="搜索文档名…">
        <span class="spacer" style="flex:1"></span>
        <span class="kp-meta">${docs.length} 篇文档</span>
      </div>
      ${docs.length ? `<div class="kp-table">${docs.map((d) => `
        <div class="kp-table-row" data-name="${E((d.name || "").toLowerCase())}">
          <div class="kp-table-main">
            <div class="kp-doc-name"><b>${E(d.name)}</b>${badge(d.status)}</div>
            <div class="kp-meta">${d.parse_mode === "tree" ? "文档树" : "分块"} ·
              ${d.chunks_count || 0} 块 · ${num(d.chars)} 字
              ${d.version_no ? " · v" + d.version_no : ""}
              ${d.expires_at ? " · 有效至 " + E(d.expires_at) : ""}
              ${d.enabled === false ? " · 已停用" : ""}</div>
            ${(d.tags || []).length ? `<div class="kp-tags">${(d.tags || [])
              .map((t) => `<span class="kp-tag">${E(t)}</span>`).join("")}</div>` : ""}
          </div>
          <div class="kp-table-actions">
            <button class="kp-icon-btn" title="查看分块" onclick="KP.kbChunks('${d.id}')">▤</button>
            <details class="kp-menu-wrap">
              <summary class="kp-icon-btn" title="更多操作">⋯</summary>
              <div class="kp-menu">
                <button onclick="KP.kbDocSettings('${d.id}')">文档设置</button>
                <button onclick="KP.kbEnrich('${d.id}')">重新富化</button>
                <button onclick="KP.kbReparse('${d.id}')">重新解析</button>
                <button class="danger" onclick="KP.kbDeleteDoc('${d.id}')">删除</button>
              </div>
            </details>
          </div>
        </div>`).join("")}</div>`
        : empty("还没有文档", "上传 PDF / Word / Excel / 图片，或从 URL、手工录入导入")}`;
    const input = el("kb-doc-q");
    if (input) {
      input.oninput = () => {
        const q = input.value.trim().toLowerCase();
        document.querySelectorAll("#kb-body .kp-table-row").forEach((row) => {
          row.style.display = !q || row.dataset.name.includes(q) ? "" : "none";
        });
      };
    }
  }

  function renderKbSearch(box) {
    box.innerHTML = `<div class="kp-kb2-bar">
        <input id="kb-q" placeholder="检索当前知识库，例如：住宿标准">
        <button class="btn sm primary" onclick="KP.kbSearch()">检索</button></div>
      <div id="kb-results"></div>`;
    const input = el("kb-q");
    if (input) input.onkeydown = (ev) => { if (ev.key === "Enter") kbSearch(); };
  }

  async function kbSearch() {
    const q = val("kb-q");
    if (!q || !S._kb) return;
    const box = el("kb-results");
    box.innerHTML = '<div class="kp-meta">检索中…</div>';
    try {
      const r = await api(`/api/kbs/${S._kb.id}/search`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, top_k: 8 }) });
      box.innerHTML = (r.items || []).map((x) => `<div class="kp-row">
        <div style="min-width:0"><b>${E(x.doc)}</b>
          <small>${E(x.context || "")}${x.page ? " · 第" + x.page + "页" : ""}
            · ${E(x.match_type || "")}</small>
          <div class="kp-meta">${E(String(x.content || "").slice(0, 240))}</div></div>
        <span class="spacer"></span><span class="kp-tag">${Number(x.score || 0).toFixed(3)}</span>
      </div>`).join("") || '<div class="kp-meta">没有命中</div>';
    } catch (e) { box.innerHTML = `<div class="kp-meta">${E(e.message)}</div>`; }
  }

  function renderKbVersions(box) {
    const docs = S._kb.docs || [];
    box.innerHTML = `<div class="kp-meta">选择文档查看其解析快照（每次解析生成新版本）</div>
      <div class="kp-list" style="margin-top:8px">${docs.map((d) => `
        <div class="kp-row"><b>${E(d.name)}</b><small>v${d.version_no || 0}</small>
          <span class="spacer"></span>
          <button class="btn sm ghost" onclick="KP.kbDocVersions('${d.id}')">版本</button>
        </div>`).join("") || '<div class="kp-meta">还没有文档</div>'}</div>`;
  }

  function kbUpload(id, folder) {
    const input = document.createElement("input");
    input.type = "file"; input.multiple = true;
    if (folder) input.webkitdirectory = true;
    input.onchange = () => {
      if (!input.files.length) return;
      uploadFiles(id, Array.from(input.files), folder);
    };
    input.click();
  }

  function uploadFiles(id, files, folder) {
    S._upload = {
      id, running: false, tab: "tags", form: {},
      files: Array.from(files).slice(0, 10).map((file) => ({
        file, name: file.name,
        rel: folder ? (file.webkitRelativePath || file.name) : file.name,
        status: "待上传", id: "", error: "",
      })),
    };
    renderUploadConfirm();
  }

  function kbUploadFolder(id) { kbUpload(id, true); }

  function renderUploadConfirm() {
    const u = S._upload;
    if (!u) return;
    const tab = (key, label) => `<button class="kp-tab ${u.tab === key ? "on" : ""}"
      onclick="KP.uploadTab('${key}')">${label}</button>`;
    openDrawerLike("上传文档", `
      <div class="kp-upload-files">${u.files.map((f) => `
        <div class="kp-upload-file"><span class="kp-doc-name">${E(f.rel)}</span>
          <span class="kp-tag" data-status>${E(f.status)}</span></div>`).join("")}</div>
      <div class="kp-tabs" style="margin:12px 0 10px">
        ${tab("tags", "标签")}${tab("parse", "解析")}${tab("chunk", "分块")}${tab("adv", "高级")}
      </div>
      <div id="upload-tab-body">${uploadTabBody()}</div>
      <div id="upload-progress"></div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn" onclick="KP.kbUploadFolder('${u.id}')">选择文件夹</button>
       <button class="btn primary" onclick="KP.uploadStart()">上传并解析</button>`);
  }

  function collectUploadForm() {
    const u = S._upload;
    if (!u) return;
    const f = u.form || {};
    const read = (id, key) => { const node = el(id); if (node) f[key] = node.value; };
    const check = (id, key) => { const node = el(id); if (node) f[key] = node.checked; };
    read("kb-up-tags", "tags");
    read("kb-up-from", "from");
    read("kb-up-to", "to");
    read("kb-up-mode", "mode");
    read("kb-up-engine", "engine");
    check("kb-up-scanned", "scanned");
    read("kb-up-strategy", "strategy");
    read("kb-up-size", "size");
    read("kb-up-over", "over");
    read("kb-up-seps", "seps");
    check("kb-up-parent", "parent");
    read("kb-up-psize", "psize");
    read("kb-up-csize", "csize");
    read("kb-up-token", "token");
    read("kb-up-langs", "langs");
    read("kb-up-dup", "dup");
    u.form = f;
  }

  function uploadTabBody() {
    const f = (S._upload && S._upload.form) || {};
    const tabs = {
      tags: `<div class="kp-form">
        <label>标签（逗号分隔，仅本批）<input id="kb-up-tags"
          value="${E(f.tags || "")}" placeholder="制度,财务"></label>
        <div class="row2">
          <label>生效日期<input id="kb-up-from" type="date" value="${E(f.from || "")}"></label>
          <label>失效日期<input id="kb-up-to" type="date" value="${E(f.to || "")}"></label>
        </div>
        <div class="kp-meta">留空表示跟随知识库默认有效期。</div>
      </div>`,
      parse: `<div class="kp-form">
        <label>解析方式<select id="kb-up-mode">
          <option value="chunk" ${(f.mode || "chunk") === "chunk" ? "selected" : ""}>分块（自适应）</option>
          <option value="tree" ${f.mode === "tree" ? "selected" : ""}>文档树（逐页+章节）</option>
        </select></label>
        <label>解析引擎<select id="kb-up-engine">
          <option value="">跟随知识库配置</option>
          <option value="builtin" ${f.engine === "builtin" ? "selected" : ""}>内置</option>
          <option value="markitdown" ${f.engine === "markitdown" ? "selected" : ""}>MarkItDown</option>
        </select></label>
        <label style="display:flex;align-items:center;gap:8px">
          <input id="kb-up-scanned" type="checkbox" style="width:auto"
            ${f.scanned ? "checked" : ""}> PDF 按扫描件解析（强制 OCR）</label>
      </div>`,
      chunk: `<div class="kp-form">
        <label>分块策略<select id="kb-up-strategy">
          ${["auto", "heading", "heuristic", "legacy"].map((s) =>
            `<option value="${s}" ${(f.strategy || "auto") === s ? "selected" : ""}>${s}</option>`).join("")}
        </select></label>
        <div class="row2">
          <label>分块大小<input id="kb-up-size" type="number" value="${f.size || 512}"></label>
          <label>分块重叠<input id="kb-up-over" type="number" value="${f.over || 80}"></label>
        </div>
        <label>分隔符（逗号分隔）<input id="kb-up-seps"
          value="${E(f.seps || "\\n\\n,\\n,。")}"></label>
        <label style="display:flex;align-items:center;gap:8px">
          <input id="kb-up-parent" type="checkbox" style="width:auto"
            ${f.parent ? "checked" : ""}> 父子分块</label>
        <div class="row2">
          <label>父块大小<input id="kb-up-psize" type="number" value="${f.psize || 4096}"></label>
          <label>子块大小<input id="kb-up-csize" type="number" value="${f.csize || 384}"></label>
        </div>
        <div class="row2">
          <label>Token 上限<input id="kb-up-token" type="number" value="${f.token || 0}"></label>
          <label>语言提示<input id="kb-up-langs" value="${E(f.langs || "")}" placeholder="zh,en"></label>
        </div>
      </div>`,
      adv: `<div class="kp-form">
        <label>重复文件处理<select id="kb-up-dup">
          <option value="skip" ${(f.dup || "skip") === "skip" ? "selected" : ""}>跳过已存在</option>
          <option value="allow" ${f.dup === "allow" ? "selected" : ""}>仍然上传</option>
          <option value="ask" ${f.dup === "ask" ? "selected" : ""}>上传前询问</option>
        </select></label>
        <div class="kp-meta">本批设置只对本次上传生效，不改变知识库默认配置。</div>
      </div>`,
    };
    return tabs[S._upload.tab] || tabs.tags;
  }

  function uploadTab(tab) {
    collectUploadForm();
    S._upload.tab = tab;
    renderUploadConfirm();
  }

  function uploadProgressHTML(done, total, failed) {
    const percent = total ? Math.round(done * 100 / total) : 0;
    return `<div class="kp-upload-progress">
      <div class="kp-upload-bar"><i style="width:${percent}%"></i></div>
      <div class="kp-meta">${done}/${total} 完成${failed ? ` · ${failed} 失败` : ""}</div>
    </div>`;
  }

  function setUploadStatus(index, text) {
    const u = S._upload;
    if (!u || !u.files[index]) return;
    u.files[index].status = text;
    const rows = document.querySelectorAll(".kp-upload-file [data-status]");
    if (rows[index]) rows[index].textContent = text;
  }

  async function fileSha(file) {
    try {
      const buffer = await file.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", buffer);
      return Array.from(new Uint8Array(digest)).map((b) =>
        b.toString(16).padStart(2, "0")).join("");
    } catch (e) { return ""; }
  }

  async function uploadStart() {
    const u = S._upload;
    if (!u || u.running || !u.files.length) return;
    collectUploadForm();
    u.running = true;
    const f = u.form || {};
    const box = el("upload-progress");
    if (box) box.innerHTML = uploadProgressHTML(0, u.files.length, 0);

    // duplicate pre-check
    const items = [];
    for (const entry of u.files) items.push({ name: entry.name, sha: await fileSha(entry.file) });
    let duplicates = {};
    try {
      const check = await api("/api/kbs/documents/precheck", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kb_id: u.id, items }) });
      (check.items || []).forEach((row, i) => {
        if (row.exists) duplicates[i] = row.existing;
      });
    } catch (e) { /* precheck is best effort */ }

    const policy = f.dup || "skip";
    let allow = policy === "allow";
    if (Object.keys(duplicates).length && policy === "ask") {
      allow = confirm(`有 ${Object.keys(duplicates).length} 个文件已存在，仍要上传吗？`);
    }
    const queue = [];
    u.files.forEach((entry, index) => {
      if (duplicates[index] && !allow) {
        setUploadStatus(index, "已存在，跳过");
        return;
      }
      queue.push(index);
    });

    let done = u.files.length - queue.length;
    let failed = 0;
    const refresh = () => {
      const bar = el("upload-progress");
      if (bar) bar.innerHTML = uploadProgressHTML(done, u.files.length, failed);
    };
    refresh();

    const chunking = {
      strategy: f.strategy || "auto",
      chunk_size: Number(f.size || 512),
      chunk_overlap: Number(f.over || 80),
      token_limit: Number(f.token || 0),
      languages: (f.langs || "").split(",").map((x) => x.trim()).filter(Boolean),
      separators: (f.seps || "").split(",").map((x) =>
        x.trim().replace(/\\n/g, "\n")).filter(Boolean),
    };
    if (f.parent) chunking.parent_child = { enabled: true,
      parent_size: Number(f.psize || 4096), child_size: Number(f.csize || 384) };
    const parseConfig = { chunking };
    if (f.engine) parseConfig.parser_engine = f.engine;
    if (f.scanned) parseConfig.parser_overrides = { pdf_force_scanned: true };

    const worker = async (index) => {
      const entry = u.files[index];
      setUploadStatus(index, "上传中…");
      const fd = new FormData();
      fd.append("files", entry.file);
      fd.append("relative_paths", JSON.stringify([entry.rel]));
      fd.append("parse_mode", f.mode || "chunk");
      fd.append("parse_config", JSON.stringify(parseConfig));
      fd.append("tags", JSON.stringify((f.tags || "")
        .split(/[,，]/).map((x) => x.trim()).filter(Boolean)));
      if (f.from) fd.append("valid_from", f.from);
      if (f.to) fd.append("expires_at", f.to);
      if (duplicates[index] && allow) fd.append("duplicate_policy", "allow");
      try {
        const r = await api(`/api/kbs/${u.id}/documents`, { method: "POST", body: fd });
        const item = (r.items || [])[0] || {};
        if (item.error) {
          entry.error = item.error; failed += 1;
          setUploadStatus(index, "失败：" + item.error.slice(0, 30));
        } else {
          entry.id = item.id;
          setUploadStatus(index, "排队解析");
        }
      } catch (e) {
        entry.error = e.message; failed += 1;
        setUploadStatus(index, "失败：" + e.message.slice(0, 30));
      } finally {
        done += 1; refresh();
      }
    };

    const next = async () => {
      while (queue.length) {
        const index = queue.shift();
        await worker(index);
      }
    };
    await Promise.all([next(), next()]);

    if (done < u.files.length) refresh();
    const ids = u.files.filter((x) => x.id).map((x) => x.id);
    if (ids.length) {
      setUploadStatus(-1, "");
      const bar = el("upload-progress");
      if (bar) bar.innerHTML = uploadProgressHTML(done, u.files.length, failed)
        + '<div class="kp-meta">解析中，完成后自动刷新…</div>';
      await pollUploadDocs(u.id, ids);
    }
    u.running = false;
    toast(`上传完成：${u.files.filter((x) => x.id).length} 个已提交`
      + (failed ? `，${failed} 个失败` : ""), failed ? "err" : "ok");
  }

  async function pollUploadDocs(kbId, ids) {
    for (let round = 0; round < 120; round++) {
      await new Promise((r) => setTimeout(r, 2000));
      let pending = 0, ready = 0, failedDocs = 0;
      for (const id of ids) {
        try {
          const doc = await api(`/api/kbs/documents/${id}`);
          if (doc.status === "ready") ready += 1;
          else if (doc.status === "failed") failedDocs += 1;
          else pending += 1;
        } catch (e) { pending += 1; }
      }
      const bar = el("upload-progress");
      if (bar) {
        bar.innerHTML = `<div class="kp-meta">解析进度：就绪 ${ready} · 处理中 ${pending}
          · 失败 ${failedDocs}</div>`;
      }
      if (!pending) break;
    }
  }

  async function kbChunks(docId) {
    try {
      const rows = (await api(`/api/kbs/documents/${docId}/chunks`)).items || [];
      S._kbChunks = rows;
      openDrawerLike("文档分块", `<div class="kp-list">${rows.map((c) => `
        <div class="kp-row"><b>#${Number(c.chunk_index) + 1}</b>
          <span class="kp-tag">${E(c.chunk_type)}</span>
          <small>${E(c.context_header || "")}${c.page ? " · 第" + c.page + "页" : ""}</small>
          <span class="spacer"></span>
          <span class="kp-tag">${c.enabled ? "生效" : "停用"}</span>
          <span class="kp-tag">${String(c.content || "").length} 字</span>
          <button class="btn sm ghost" onclick="KP.kbChunkHistory('${c.id}')">历史</button>
          <button class="btn sm ghost" onclick="KP.kbToggleChunk('${c.id}',${c.enabled ? "false" : "true"})">
            ${c.enabled ? "停用" : "启用"}</button>
          <button class="btn sm ghost" onclick="KP.kbEditChunk('${c.id}')">编辑</button></div>
        <div class="kp-pre">${E(String(c.content || "").slice(0, 800))}</div>`).join("")
        || '<div class="kp-meta">没有分块</div>'}</div>`,
        `<button class="btn" onclick="closeModal()">关闭</button>`);
    } catch (e) { toast(e.message, "err"); }
  }

  function kbEditChunk(chunkId) {
    const chunk = (S._kbChunks || []).find((c) => c.id === chunkId);
    if (!chunk) return toast("分块不存在", "err");
    openDrawerLike("编辑分块", `<div class="kp-form">
      <div class="kp-meta">#${Number(chunk.chunk_index) + 1} · 当前版本 v${chunk.content_revision || 0}</div>
      <textarea id="kb-chunk-text" rows="10">${E(chunk.content || "")}</textarea>
      <div class="kp-meta">保存会生成新的修订版本，并立即重建检索索引。</div>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.kbSaveChunk('${chunkId}',${chunk.content_revision || 0})">保存</button>`);
  }

  async function kbSaveChunk(chunkId, revision) {
    const content = (el("kb-chunk-text") || {}).value || "";
    try {
      await api(`/api/kbs/chunks/${chunkId}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, expected_revision: revision }) });
      closeModal(); toast("已保存并重建索引", "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbToggleChunk(chunkId, enabled) {
    const chunk = (S._kbChunks || []).find((c) => c.id === chunkId);
    try {
      await api(`/api/kbs/chunks/${chunkId}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: enabled === true || enabled === "true",
          expected_revision: chunk ? chunk.content_revision || 0 : 0 }) });
      toast("已更新", "ok"); closeModal();
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbChunkHistory(chunkId) {
    try {
      const rows = (await api(`/api/kbs/chunks/${chunkId}/revisions`)).items || [];
      openDrawerLike("分块历史", `<div class="kp-list">${rows.map((v) => `
        <div class="kp-row"><b>v${v.revision}</b><small>${String(v.content || "").length} 字</small>
          <span class="spacer"></span><span class="kp-meta">${E(v.edited_at || "")}</span>
          <button class="btn sm ghost" onclick="KP.kbRevertChunk('${chunkId}',${v.revision})">回滚</button>
        </div>
        <div class="kp-pre">${E(String(v.content || "").slice(0, 400))}</div>`).join("")
        || '<div class="kp-meta">还没有历史修订</div>'}</div>`,
        `<button class="btn" onclick="closeModal()">关闭</button>`);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbRevertChunk(chunkId, revision) {
    try {
      await api(`/api/kbs/chunks/${chunkId}/revert/${revision}`,
        { method: "POST" });
      closeModal(); toast("已回滚", "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  async function renderKbRecycle(box) {
    box.innerHTML = '<div class="kp-meta">加载中…</div>';
    try {
      const rows = (await api(`/api/kbs/${S._kb.id}/recycle`)).items || [];
      box.innerHTML = `<div class="kp-meta">回收站保留 90 天；彻底删除不可恢复。</div>
        <div class="kp-list" style="margin-top:8px">${rows.map((r) => `
          <div class="kp-row"><b>${E(r.name || r.ref_id)}</b>
            <small>${E((r.created_at || "").slice(0, 16))}</small>
            <span class="spacer"></span>
            <span class="kp-meta">${E((r.expires_at || "").slice(0, 10))} 到期</span>
            <button class="btn sm ghost" onclick="KP.kbRestoreEntry('${r.id}')">恢复</button>
            <button class="btn sm ghost" onclick="KP.kbPurgeEntry('${r.id}')">彻底删除</button>
          </div>`).join("") || '<div class="kp-meta">回收站为空</div>'}</div>`;
    } catch (e) { box.innerHTML = `<div class="kp-meta">${E(e.message)}</div>`; }
  }

  async function kbRestoreEntry(entryId) {
    try {
      await api(`/api/kbs/recycle/${entryId}/restore`, { method: "POST" });
      toast("已恢复", "ok"); openKb(S._kb.id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbPurgeEntry(entryId) {
    if (!confirm("彻底删除后不可恢复，继续？")) return;
    try {
      await api(`/api/kbs/recycle/${entryId}`, { method: "DELETE" });
      toast("已彻底删除", "ok"); openKb(S._kb.id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbDocVersions(docId) {
    try {
      const rows = (await api(`/api/kbs/documents/${docId}/versions`)).items || [];
      openDrawerLike("文档版本", `<div class="kp-list">${rows.map((v) => `
        <div class="kp-row"><b>v${v.version_no}</b><small>${num(v.chars)} 字</small>
          <span class="spacer"></span><span class="kp-tag">${E(v.status)}</span>
          <span class="kp-meta">${E((v.created_at || "").slice(0, 16))}</span></div>`).join("")
        || '<div class="kp-meta">还没有版本</div>'}</div>`,
        `<button class="btn" onclick="closeModal()">关闭</button>`);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbDocSettings(docId) {
    let doc = null;
    try { doc = await api(`/api/kbs/documents/${docId}`); }
    catch (e) { return toast(e.message, "err"); }
    openDrawerLike("文档设置", `<div class="kp-form">
      <label>标签（逗号分隔）<input id="kb-doc-tags"
        value="${E((doc.tags || []).join(","))}"></label>
      <div class="row2">
        <label>生效日期<input id="kb-doc-from" type="date"
          value="${E(doc.valid_from || "")}"></label>
        <label>失效日期<input id="kb-doc-to" type="date"
          value="${E(doc.expires_at || "")}"></label>
      </div>
      <label style="display:flex;align-items:center;gap:8px">
        <input id="kb-doc-enabled" type="checkbox" style="width:auto"
          ${doc.enabled === false ? "" : "checked"}> 参与检索</label>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.kbSaveDoc('${docId}')">保存</button>`);
  }

  async function kbSaveDoc(docId) {
    try {
      await api(`/api/kbs/documents/${docId}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tags: (val("kb-doc-tags") || "").split(/[,，]/)
            .map((x) => x.trim()).filter(Boolean),
          valid_from: val("kb-doc-from"), expires_at: val("kb-doc-to"),
          enabled: el("kb-doc-enabled").checked }) });
      closeModal(); toast("已保存", "ok");
      if (S._kb) openKb(S._kb.id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbReparse(docId) {
    try {
      await api(`/api/kbs/documents/${docId}/reparse`, { method: "POST" });
      toast("已开始重新解析", "ok");
      if (S._kb) setTimeout(() => openKb(S._kb.id), 1200);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbEnrich(docId) {
    try {
      toast("富化执行中…", "ok");
      const r = await api(`/api/kbs/documents/${docId}/enrich`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force: true }) });
      toast(`完成：${Object.keys(r || {}).length} 项`, "ok");
      if (S._kb) setTimeout(() => openKb(S._kb.id), 800);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbDeleteDoc(docId) {
    if (!confirm("删除该文档及其分块？")) return;
    try {
      await api(`/api/kbs/documents/${docId}`, { method: "DELETE" });
      toast("已删除", "ok");
      if (S._kb) openKb(S._kb.id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbSettings(id) {
    let stat = null, modelsData = { items: [] }, parsersData = { items: [] };
    try {
      [stat, modelsData, parsersData] = await Promise.all([
        api(`/api/kbs/${id}`),
        api("/api/platform/models"),
        api("/api/platform/parsers"),
      ]);
    } catch (e) { return toast(e.message, "err"); }
    S._kbSettings = { id, base: stat.base || {}, models: modelsData.items || [],
      parsers: parsersData.items || [] };
    renderKbSettings();
  }

  const KB_SETTING_GROUPS = [
    ["basic", "基本信息"], ["models", "模型配置"], ["parse", "解析与分块"],
    ["parsers", "解析引擎"], ["index", "索引策略"], ["gov", "治理"],
    ["enrich", "富化"], ["adv", "高级"], ["danger", "危险区"],
  ];

  const PARSER_TYPE_GROUPS = [
    ["pdf", "PDF"], ["docx", "Word"], ["pptx", "演示文稿"],
    ["xlsx", "Excel"], ["epub", "电子书"], ["html", "网页归档"],
    ["csv", "CSV"], ["md", "纯文本"], ["json", "JSON"],
    ["image", "图片"], ["audio", "音频"],
  ];

  function renderKbSettings() {
    const st = S._kbSettings;
    if (!st) return;
    const v = el("view");
    const b = st.base;
    let cfg = b.config || {};
    if (typeof cfg === "string") {
      try { cfg = JSON.parse(cfg); } catch (e) { cfg = {}; }
    }
    const chunking = cfg.chunking || {};
    const models = cfg.models || {};
    const index = cfg.indexing || {};
    const gov = cfg.governance || {};
    const enrichment = cfg.enrichment || {};
    v.innerHTML = `${head(`${b.name} · 设置`, `<button class="btn sm"
      onclick="KP.openKb('${st.id}')">← 返回</button>
      <button class="btn primary sm" onclick="KP.kbSaveSettings('${st.id}')">保存</button>`)}
      <div class="split">
        <div class="setnav">${KB_SETTING_GROUPS.map(([key, label]) =>
          `<div onclick="document.getElementById('set-${key}').scrollIntoView({behavior:'smooth',block:'start'})">${label}</div>`).join("")}</div>
        <div>
          <div class="form" id="set-basic">
            <h3>基本信息</h3>
            <div class="field"><label>名称</label><input id="kb-s-name" value="${E(b.name || "")}"></div>
            <div class="field"><label>描述</label><input id="kb-s-desc" value="${E(b.description || "")}"></div>
            <div class="field"><label>类型</label><input value="${E(b.type || "document")}" disabled><div class="desc">创建后不可修改</div></div>
          </div>

          <div class="form" id="set-models" style="margin-top:14px">
            <h3>模型配置</h3>
            <div class="hint">留空表示跟随全局默认；显示「未配置」的能力相关功能会被禁用。</div>
            ${[["chat", "问答模型"], ["embedding", "嵌入模型"], ["summary", "摘要/问题/标签模型"],
               ["rerank", "重排模型"], ["vllm", "视觉模型"], ["asr", "语音模型"]].map(([key, label]) => {
              const options = (st.models || []).filter((m) => m.kind === (key === "summary" ? "chat" : key));
              return `<div class="field"><label>${label}</label>
                <select id="kb-model-${key}">
                  <option value="">跟随全局默认</option>
                  ${options.map((m) => `<option value="${m.id}" ${models[key] === m.id ? "selected" : ""}>${E(m.name)} (${E(m.model_name || m.provider)})</option>`).join("")}
                </select></div>`;
            }).join("")}
          </div>

          <div class="form" id="set-parse" style="margin-top:14px">
            <h3>解析与分块</h3>
            <div class="field"><label>默认解析方式</label>
              <select id="kb-parse-mode">
                <option value="chunk" ${(b.parse_mode || chunking.parse_mode) === "chunk" ? "selected" : ""}>分块（自适应切分）</option>
                <option value="tree" ${(chunking.parse_mode) === "tree" ? "selected" : ""}>文档树（逐页+章节）</option>
              </select></div>
            <div class="field"><label>分块策略</label>
              <select id="kb-s-strategy">${["auto", "heading", "heuristic", "legacy"].map((s) =>
                `<option value="${s}" ${chunking.strategy === s ? "selected" : ""}>${s}</option>`).join("")}</select>
              <div class="desc">自动：按文档结构选择标题 / 结构感知 / 递归</div></div>
            <div class="row2">
              <div class="field"><label>分块大小</label><input id="kb-s-size" type="number" value="${chunking.chunk_size || 512}"><div class="desc">100–4000 字符</div></div>
              <div class="field"><label>分块重叠</label><input id="kb-s-over" type="number" value="${chunking.chunk_overlap || 80}"><div class="desc">0–500 字符</div></div>
            </div>
            <div class="field"><label>分隔符（逗号分隔）</label><input id="kb-s-seps" value="${E((chunking.separators || ["\\n\\n", "\\n", "。"]).join(","))}"></div>
            <div class="field"><div class="switch ${((chunking.parent_child || {}).enabled) ? "on" : ""}" id="kb-s-parent-sw" onclick="this.classList.toggle('on')"><i></i>父子分块</div>
              <div class="desc">父块回填上下文，子块参与匹配</div></div>
            <div class="row2">
              <div class="field"><label>父块大小</label><input id="kb-s-psize" type="number" value="${(chunking.parent_child || {}).parent_size || 4096}"></div>
              <div class="field"><label>子块大小</label><input id="kb-s-csize" type="number" value="${(chunking.parent_child || {}).child_size || 384}"></div>
            </div>
            <div class="row2">
              <div class="field"><label>Token 上限</label><input id="kb-s-token" type="number" value="${chunking.token_limit || 0}"><div class="desc">0 表示按字符</div></div>
              <div class="field"><label>语言提示</label><input id="kb-s-langs" value="${E((chunking.languages || []).join(","))}" placeholder="zh,en"></div>
            </div>
            <button class="btn" onclick="KP.kbPreviewChunking()">测试分块效果</button>
          </div>

          <div class="form" id="set-parsers" style="margin-top:14px">
            <h3>解析引擎</h3>
            <div class="hint">按文件类型选择引擎；未配置使用默认（PPT→markitdown，其余→builtin）。</div>
            ${PARSER_TYPE_GROUPS.map(([type, label]) => {
              const current = (chunking.parser_engines || []).find((rule) =>
                (rule.file_types || []).includes(type));
              const options = (st.parsers || []).filter((p) => p.available &&
                (p.file_types || []).includes(type));
              return `<div class="field"><label>${label}</label>
                <select id="kb-parser-${type}">
                  <option value="">默认</option>
                  ${options.map((p) => `<option value="${p.name}" ${current && current.engine === p.name ? "selected" : ""}>${p.name}</option>`).join("")}
                </select></div>`;
            }).join("")}
            <label style="display:flex;align-items:center;gap:8px">
              <input id="kb-parser-xlsx-header" type="checkbox" style="width:auto"
                ${(chunking.xlsx_first_row_as_header !== false) ? "checked" : ""}> Excel 首行作为列标题</label>
          </div>

          <div class="form" id="set-index" style="margin-top:14px">
            <h3>索引策略</h3>
            <div class="field"><div class="switch on"><i></i>关键词检索（FTS5，始终开启）</div></div>
            <div class="field"><div class="switch ${index.vector ? "on" : ""}" id="kb-index-vector" onclick="this.classList.toggle('on')"><i></i>向量检索</div><div class="desc">需配置嵌入模型</div></div>
            <div class="field"><div class="switch ${index.tree !== false ? "on" : ""}" id="kb-index-tree" onclick="this.classList.toggle('on')"><i></i>结构检索（父块 / 章节回填）</div></div>
          </div>

          <div class="form" id="set-gov" style="margin-top:14px">
            <h3>治理</h3>
            <div class="field"><label>文档默认有效期（天，0=永久）</label><input id="kb-gov-days" type="number" value="${gov.default_validity_days || 0}"></div>
            <div class="field"><label>重复检测范围</label>
              <select id="kb-gov-scope"><option value="current_kb" ${gov.duplicate_scope !== "workspace_kbs" ? "selected" : ""}>当前知识库</option>
                <option value="workspace_kbs" ${gov.duplicate_scope === "workspace_kbs" ? "selected" : ""}>空间内全部知识库</option></select></div>
            <div class="field"><div class="switch ${gov.sensitive_enabled ? "on" : ""}" id="kb-gov-sens" onclick="this.classList.toggle('on')"><i></i>敏感内容检测</div></div>
            <div class="field"><div class="switch ${gov.auto_redact !== false ? "on" : ""}" id="kb-gov-redact" onclick="this.classList.toggle('on')"><i></i>自动脱敏（关闭则命中后需人工复核）</div></div>
            <div class="field"><label>自定义敏感词（逗号分隔）</label><input id="kb-gov-terms" value="${E((gov.custom_terms || []).join(","))}"></div>
          </div>

          <div class="form" id="set-enrich" style="margin-top:14px">
            <h3>富化</h3>
            <div class="field"><div class="switch ${enrichment.summary ? "on" : ""}" id="kb-en-summary" onclick="this.classList.toggle('on')"><i></i>生成文档摘要</div></div>
            <div class="field"><div class="switch ${(enrichment.questions || {}).enabled ? "on" : ""}" id="kb-en-questions" onclick="this.classList.toggle('on')"><i></i>生成检索问题</div></div>
            <div class="field"><label>每块问题数</label><input id="kb-en-qcount" type="number" value="${(enrichment.questions || {}).count || 3}"></div>
            <div class="field"><div class="switch ${(enrichment.auto_tag || {}).enabled ? "on" : ""}" id="kb-en-autotag" onclick="this.classList.toggle('on')"><i></i>自动关联标签</div></div>
            <div class="field"><label>最多标签数</label><input id="kb-en-maxtags" type="number" value="${(enrichment.auto_tag || {}).max_tags || 3}"></div>
          </div>

          <div class="form" id="set-adv" style="margin-top:14px">
            <h3>高级</h3>
            <div class="field"><label>表格元数据生成要求</label><textarea id="kb-adv-table">${E(chunking.table_metadata_instructions || "")}</textarea><div class="desc">用于 CSV/Excel 表摘要的业务背景</div></div>
            <div class="field"><label>问题生成要求</label><textarea id="kb-adv-questions">${E((enrichment.questions || {}).instructions || "")}</textarea></div>
          </div>

          <div class="form" id="set-danger" style="margin-top:14px">
            <h3>危险区</h3>
            <div class="hint">删除知识库会同时删除文档、分块与索引，不可恢复。</div>
            <button class="btn" style="color:var(--err);border-color:var(--err)"
              onclick="KP.kbDelete('${st.id}')">删除知识库</button>
          </div>
        </div>
      </div>`;
  }

  async function kbSaveSettings(id) {
    const st = S._kbSettings;
    if (!st) return;
    let cfg = st.base.config || {};
    if (typeof cfg === "string") {
      try { cfg = JSON.parse(cfg); } catch (e) { cfg = {}; }
    }
    cfg = Object.assign({}, cfg);
    const text = (v) => (v || "").trim();
    cfg.models = {};
    ["chat", "embedding", "summary", "rerank", "vllm", "asr"].forEach((key) => {
      const value = val(`kb-model-${key}`);
      if (value) cfg.models[key] = value;
    });
    const seps = text(val("kb-s-seps")).split(",").map((x) => x.trim())
      .filter(Boolean);
    const parserEngines = [];
    PARSER_TYPE_GROUPS.forEach(([type]) => {
      const engine = val(`kb-parser-${type}`);
      if (engine) parserEngines.push({ file_types: [type], engine });
    });
    cfg.chunking = Object.assign({}, cfg.chunking, {
      parse_mode: val("kb-parse-mode") || "chunk",
      strategy: val("kb-s-strategy") || "auto",
      chunk_size: Number(val("kb-s-size") || 512),
      chunk_overlap: Number(val("kb-s-over") || 80),
      separators: seps.length ? seps : undefined,
      parent_child: {
        enabled: (el("kb-s-parent-sw") || {}).classList?.contains("on") || false,
        parent_size: Number(val("kb-s-psize") || 4096),
        child_size: Number(val("kb-s-csize") || 384),
      },
      token_limit: Number(val("kb-s-token") || 0),
      languages: text(val("kb-s-langs")).split(",").map((x) => x.trim()).filter(Boolean),
      parser_engines: parserEngines,
      xlsx_first_row_as_header: (el("kb-parser-xlsx-header") || {}).checked,
      table_metadata_instructions: text((el("kb-adv-table") || {}).value),
    });
    cfg.indexing = {
      keyword: true,
      vector: (el("kb-index-vector") || {}).classList?.contains("on") || false,
      tree: (el("kb-index-tree") || {}).classList?.contains("on") || false,
    };
    const terms = text(val("kb-gov-terms")).split(/[,，]/).map((x) => x.trim()).filter(Boolean);
    cfg.governance = {
      default_validity_days: Number(val("kb-gov-days") || 0),
      duplicate_scope: val("kb-gov-scope") || "current_kb",
      sensitive_enabled: (el("kb-gov-sens") || {}).classList?.contains("on") || false,
      auto_redact: (el("kb-gov-redact") || {}).classList?.contains("on") || false,
      custom_terms: terms,
    };
    cfg.enrichment = {
      summary: (el("kb-en-summary") || {}).classList?.contains("on") || false,
      questions: { enabled: (el("kb-en-questions") || {}).classList?.contains("on") || false,
        count: Number(val("kb-en-qcount") || 3),
        instructions: text((el("kb-adv-questions") || {}).value) },
      auto_tag: { enabled: (el("kb-en-autotag") || {}).classList?.contains("on") || false,
        max_tags: Number(val("kb-en-maxtags") || 3), skip_if_tagged: true },
    };
    cfg.sensitive = {
      enabled: cfg.governance.sensitive_enabled,
      auto_redact: cfg.governance.auto_redact,
      custom_terms: terms,
    };
    cfg.dedup = { enabled: true };
    try {
      await api(`/api/kbs/${id}`, { method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: val("kb-s-name"),
          description: val("kb-s-desc"), config: cfg }) });
      toast("已保存", "ok");
      openKb(id);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbPreviewChunking() {
    const text = prompt("粘贴一段样例文本，按当前配置预览分块：", "");
    if (!text) return;
    try {
      const r = await api("/api/kbs/preview-chunking", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, config: { chunking: {
          strategy: val("kb-s-strategy"), chunk_size: Number(val("kb-s-size") || 512),
          chunk_overlap: Number(val("kb-s-over") || 80),
          token_limit: Number(val("kb-s-token") || 0),
        } } }) });
      openDrawerLike("分块预览", `<div class="kp-meta">选中策略：${E(r.selected_tier || "legacy")}
        · 块数 ${r.stats?.chunks || 0} · 平均 ${r.stats?.avg_chars || 0} 字</div>
        <div class="kp-list" style="margin-top:8px">${(r.chunks || []).map((c) => `
          <div class="kp-row"><b>#${Number(c.seq) + 1}</b><small>${c.chars} 字</small>
            <span class="spacer"></span><span class="kp-tag">${c.tokens} tok</span></div>
          <div class="kp-pre">${E(c.preview || "")}</div>`).join("")}</div>`,
        `<button class="btn" onclick="closeModal()">关闭</button>`);
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbDelete(id) {
    const name = prompt("输入知识库名称确认删除：");
    if (name === null) return;
    if (name.trim() !== (S._kbSettings.base.name || "")) return toast("名称不匹配", "err");
    try {
      await api(`/api/kbs/${id}`, { method: "DELETE" });
      toast("已删除", "ok"); renderKbs();
    } catch (e) { toast(e.message, "err"); }
  }

  // ─────────────────────── 知识库：FAQ / 导入 / 模型配置 ───────────────────────
  function kbManualForm(id) {
    openDrawerLike("手工录入文档", `<div class="kp-form">
      <label>标题<input id="kb-m-title" placeholder="例如：差旅制度补充说明"></label>
      <label>正文（Markdown）<textarea id="kb-m-body" rows="10"
        placeholder="# 标题&#10;&#10;正文内容"></textarea></label>
      <label>标签（逗号分隔）<input id="kb-m-tags"></label>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.kbManualSave('${id}')">保存并解析</button>`);
  }

  async function kbManualSave(id) {
    const title = val("kb-m-title");
    const content = (el("kb-m-body") || {}).value || "";
    if (!title || !content.trim()) return toast("标题与正文必填", "err");
    try {
      await api(`/api/kbs/${id}/documents/manual`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, content,
          tags: (val("kb-m-tags") || "").split(/[,，]/)
            .map((x) => x.trim()).filter(Boolean) }) });
      closeModal(); toast("已保存，开始解析", "ok");
      setTimeout(() => openKb(id), 1200);
    } catch (e) { toast(e.message, "err"); }
  }

  function kbUrlForm(id) {
    openDrawerLike("导入网页", `<div class="kp-form">
      <label>URL<input id="kb-u-url" placeholder="https://example.com/page"></label>
      <label>标题（可选）<input id="kb-u-title"></label>
      <div class="kp-meta">抓取正文后按当前解析与分块配置入库。</div>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.kbUrlSave('${id}')">抓取并解析</button>`);
  }

  async function kbUrlSave(id) {
    const url = val("kb-u-url");
    if (!url) return toast("URL 必填", "err");
    try {
      await api(`/api/kbs/${id}/documents/url`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, title: val("kb-u-title") }) });
      closeModal(); toast("已开始抓取", "ok");
      setTimeout(() => openKb(id), 1500);
    } catch (e) { toast(e.message, "err"); }
  }

  async function renderKbFaq(box) {
    box.innerHTML = `<div class="toolbar">
        <button class="btn primary sm" onclick="KP.kbFaqForm()">＋ 新建条目</button>
        <button class="btn sm" onclick="KP.kbFaqImport()">导入 JSON/CSV</button>
        <span class="spacer" style="flex:1"></span>
        <button class="btn sm" onclick="KP.kbFaqExport()">导出 CSV</button></div>
      <div id="kb-faq-list"><div class="kp-meta">加载中…</div></div>`;
    try {
      const items = (await api(`/api/kbs/${S._kb.id}/faq/entries`)).items || [];
      S._faqItems = items;
      el("kb-faq-list").innerHTML = items.length ? `<div class="kp-list">${items.map((e) => `
        <div class="kp-row"><b>${E(e.standard_question)}</b>
          ${e.enabled ? "" : '<span class="badge gray">已停用</span>'}
          <small>${arr(e.similar_questions).length} 个相似问 · ${arr(e.answers).length} 个答案</small>
          <span class="spacer"></span>
          <button class="btn sm ghost" onclick="KP.kbFaqForm('${e.id}')">编辑</button>
          <button class="btn sm ghost" onclick="KP.kbFaqDelete('${e.id}')">删除</button></div>`).join("")}</div>`
        : empty("还没有 FAQ 条目", "手工新建，或在下方导入 JSON/CSV");
    } catch (e) { el("kb-faq-list").innerHTML = empty("加载失败", e.message); }
  }

  function kbFaqForm(entryId) {
    const entry = (S._faqItems || []).find((e) => e.id === entryId) || {};
    openDrawerLike(entryId ? "编辑 FAQ 条目" : "新建 FAQ 条目", `<div class="kp-form">
      <label>标准问<input id="kb-faq-q" value="${E(entry.standard_question || "")}"></label>
      <label>相似问（逗号分隔）<input id="kb-faq-sim"
        value="${E(arr(entry.similar_questions).join(","))}"></label>
      <label>反例问（逗号分隔）<input id="kb-faq-neg"
        value="${E(arr(entry.negative_questions).join(","))}"></label>
      <label>答案（每行一个）<textarea id="kb-faq-ans" rows="5">${E(arr(entry.answers).join("\n"))}</textarea></label>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.kbFaqSave('${S._kb.id}','${entryId || ""}')">保存</button>`);
  }

  async function kbFaqSave(kbId, entryId) {
    const payload = {
      standard_question: val("kb-faq-q"),
      similar_questions: (val("kb-faq-sim") || "").split(/[,，]/)
        .map((x) => x.trim()).filter(Boolean),
      negative_questions: (val("kb-faq-neg") || "").split(/[,，]/)
        .map((x) => x.trim()).filter(Boolean),
      answers: ((el("kb-faq-ans") || {}).value || "").split("\n")
        .map((x) => x.trim()).filter(Boolean),
    };
    try {
      if (entryId) {
        await api(`/api/kbs/faq/entries/${entryId}`, { method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload) });
      } else {
        await api(`/api/kbs/${kbId}/faq/entries`, { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload) });
      }
      closeModal(); toast("已保存", "ok"); renderKbBody();
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbFaqDelete(entryId) {
    if (!confirm("删除该 FAQ 条目？")) return;
    try {
      await api(`/api/kbs/faq/entries/${entryId}`, { method: "DELETE" });
      toast("已删除", "ok"); renderKbBody();
    } catch (e) { toast(e.message, "err"); }
  }

  function kbFaqImport() {
    openDrawerLike("导入 FAQ", `<div class="kp-form">
      <label>JSON 数组<textarea id="kb-faq-json" rows="10"
        placeholder='[{"standard_question":"...","answers":["..."]}]'></textarea></label>
      <label>模式
        <select id="kb-faq-mode">
          <option value="append">追加（同标准问合并）</option>
          <option value="replace">替换（删除未出现的条目）</option>
        </select></label>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.kbFaqDoImport()">导入</button>`);
  }

  async function kbFaqDoImport() {
    let entries = [];
    try { entries = JSON.parse((el("kb-faq-json") || {}).value || "[]"); }
    catch (e) { return toast("JSON 格式错误", "err"); }
    if (!Array.isArray(entries)) return toast("必须是数组", "err");
    try {
      const r = await api(`/api/kbs/${S._kb.id}/faq/import`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries, mode: val("kb-faq-mode") || "append" }) });
      closeModal();
      toast(`新增 ${r.created}，合并 ${r.merged}，跳过 ${r.skipped}`, "ok");
      renderKbBody();
    } catch (e) { toast(e.message, "err"); }
  }

  async function kbFaqExport() {
    try {
      const r = await api(`/api/kbs/${S._kb.id}/faq/export?fmt=csv`);
      const blob = new Blob([r.content], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "faq.csv"; a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { toast(e.message, "err"); }
  }

  // ── 模型配置页 ──
  const MODEL_KIND_LABEL = { chat: "对话模型", embedding: "嵌入模型",
    rerank: "重排模型", vllm: "视觉模型", asr: "语音模型" };

  async function renderModels() {
    const v = el("view");
    v.innerHTML = `${head("模型配置", `<button class="btn primary sm"
      onclick="KP.modelForm()">＋ 新建模型</button>`)}
      <div class="kp-tabs" id="model-tabs"></div>
      <div id="model-list">${skeleton()}</div>`;
    try {
      const data = await api("/api/platform/models");
      const items = data.items || [];
      S._models = items;
      const kinds = ["all"].concat(data.kinds || Object.keys(MODEL_KIND_LABEL));
      const active = S._modelTab || "all";
      el("model-tabs").innerHTML = kinds.map((kind) => {
        const count = kind === "all" ? items.length
          : items.filter((m) => m.kind === kind).length;
        const label = kind === "all" ? "全部" : (MODEL_KIND_LABEL[kind] || kind);
        return `<button data-k="${kind}" class="kp-tab ${active === kind ? "on" : ""}"
          onclick="KP.modelTab('${kind}')">${label} (${count})</button>`;
      }).join("");
      renderModelList();
    } catch (e) { el("model-list").innerHTML = empty("加载失败", e.message); }
  }

  function modelTab(kind) {
    S._modelTab = kind;
    document.querySelectorAll("#model-tabs button").forEach((btn) =>
      btn.classList.toggle("on", btn.dataset.k === kind));
    renderModelList();
  }

  function renderModelList() {
    const box = el("model-list");
    if (!box) return;
    const active = S._modelTab || "all";
    const items = (S._models || []).filter((m) =>
      active === "all" || m.kind === active);
    if (!items.length) {
      box.innerHTML = empty("还没有模型配置",
        "新建对话 / 嵌入 / 重排 / 视觉 / 语音模型");
      return;
    }
    box.innerHTML = `<div class="kp-grid">${items.map((m) => `
      <div class="kp-card" style="cursor:pointer" onclick="KP.modelForm('${m.id}')">
        <div class="kp-card-top">
          <div class="kp-avatar">${m.kind === "embedding" ? "🧬"
            : m.kind === "rerank" ? "📊" : m.kind === "vllm" ? "🖼"
            : m.kind === "asr" ? "🎙" : "🤖"}</div>
          <div style="flex:1;min-width:0"><h4>${E(m.name)}</h4>
            <div class="kp-meta">${E(m.provider)} · ${E(m.model_name || "未填模型")}</div></div>
          ${m.is_default ? '<span class="badge ok">默认</span>' : ""}
        </div>
        <div class="kp-card-foot">
          <span class="kp-tag">${MODEL_KIND_LABEL[m.kind] || m.kind}</span>
          ${m.dimension ? `<span class="kp-tag">${m.dimension} 维</span>` : ""}
          ${m.context_window ? `<span class="kp-tag">${m.context_window} ctx</span>` : ""}
          ${m.supports_vision ? '<span class="kp-tag">视觉</span>' : ""}
          <span class="kp-tag">${m.has_key ? "密钥已配" : "无密钥"}</span>
          ${m.enabled ? "" : '<span class="badge gray">停用</span>'}
          <span class="spacer" style="flex:1"></span>
          <button class="btn sm ghost" onclick="event.stopPropagation();KP.modelTest('${m.id}')">测试</button>
          <button class="btn sm ghost" onclick="event.stopPropagation();KP.modelDelete('${m.id}')">删除</button>
        </div>
      </div>`).join("")}</div>`;
  }

  function modelForm(modelId) {
    const m = (S._models || []).find((x) => x.id === modelId) || {};
    openDrawerLike(modelId ? "编辑模型" : "新建模型", `<div class="kp-form">
      <label>类型<select id="md-kind">
        ${Object.keys(MODEL_KIND_LABEL).map((k) =>
          `<option value="${k}" ${m.kind === k ? "selected" : ""}>${MODEL_KIND_LABEL[k]}</option>`).join("")}
      </select></label>
      <label>名称<input id="md-name" value="${E(m.name || "")}"></label>
      <label>来源<select id="md-source">
        ${["custom", "builtin", "ollama"].map((s) =>
          `<option value="${s}" ${(m.source || "custom") === s ? "selected" : ""}>${s}</option>`).join("")}
      </select></label>
      <label>Provider<select id="md-provider">
        ${["openai", "anthropic", "azure", "ollama", "custom"].map((p) =>
          `<option value="${p}" ${m.provider === p ? "selected" : ""}>${p}</option>`).join("")}
      </select></label>
      <label>Base URL<input id="md-url" value="${E(m.base_url || "")}"
        placeholder="https://api.example.com/v1"></label>
      <label>模型名<input id="md-model" value="${E(m.model_name || "")}"></label>
      <div class="row2">
        <label>向量维度<input id="md-dim" type="number"
          value="${m.dimension || 0}"></label>
        <label>上下文窗口<input id="md-ctx" type="number"
          value="${m.context_window || 0}"></label>
      </div>
      <label>API Key<input id="md-key" type="password"
        placeholder="${m.has_key ? "已配置，留空则不修改" : "sk-..."}"></label>
      <label style="display:flex;align-items:center;gap:8px">
        <input id="md-vision" type="checkbox" style="width:auto"
          ${m.supports_vision ? "checked" : ""}> 支持视觉输入</label>
      <label style="display:flex;align-items:center;gap:8px">
        <input id="md-default" type="checkbox" style="width:auto"
          ${m.is_default ? "checked" : ""}> 设为该类型默认</label>
      <label style="display:flex;align-items:center;gap:8px">
        <input id="md-enabled" type="checkbox" style="width:auto"
          ${m.enabled === false ? "" : "checked"}> 启用</label>
      <label>自定义 Header（JSON，可选）<textarea id="md-headers"
        rows="3" placeholder='{"X-Org":"team"}'></textarea></label>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.modelSave('${modelId || ""}')">保存</button>`);
  }

  async function modelSave(modelId) {
    let extra = {};
    try {
      if (val("md-headers")) extra = JSON.parse(val("md-headers"));
    } catch (e) { return toast("Header 必须是 JSON", "err"); }
    const payload = {
      kind: val("md-kind"), name: val("md-name"), provider: val("md-provider"),
      source: val("md-source"), base_url: val("md-url"),
      model_name: val("md-model"),
      dimension: Number(val("md-dim") || 0),
      context_window: Number(val("md-ctx") || 0),
      supports_vision: el("md-vision").checked,
      extra: Object.assign({}, extra ? { headers: extra } : {}),
      is_default: el("md-default").checked, enabled: el("md-enabled").checked,
    };
    if (val("md-key")) payload.api_key = val("md-key");
    try {
      if (modelId) {
        await api(`/api/platform/models/${modelId}`, { method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload) });
      } else {
        await api("/api/platform/models", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload) });
      }
      closeModal(); toast("已保存", "ok"); renderModels();
    } catch (e) { toast(e.message, "err"); }
  }

  function modelTest(modelId) {
    const m = (S._models || []).find((x) => x.id === modelId) || {};
    const kind = m.kind || "chat";
    const hint = kind === "embedding" ? "输入要向量化的示例文本"
      : kind === "rerank" ? "输入查询语句，候选文档在下方按行填写"
      : kind === "vllm" ? "输入给视觉模型的提示词（未上传图片时按文本测试）"
      : "输入测试问题";
    const params = kind === "chat" || kind === "vllm" ? `
      <div class="row2">
        <label>Temperature<input id="md-temp" type="number" step="0.1"
          value="0.2" min="0" max="2"></label>
        <label>最大 Token<input id="md-max" type="number" value="256"
          min="1" max="8192"></label>
      </div>` : "";
    const docs = kind === "rerank" ? `
      <label>候选文档（每行一条）<textarea id="md-docs" rows="4">知识库是企业的文档检索系统。
报销制度规定了差旅住宿标准。</textarea></label>` : "";
    openDrawerLike(`调试 · ${m.name || "模型"}`, `
      <div class="kp-form">
        <div class="kp-meta">${E(m.provider || "")} · ${E(m.model_name || "未填模型")}
          ${m.base_url ? " · " + E(m.base_url) : ""}</div>
        <label>${hint}<textarea id="md-input" rows="4">${kind === "chat" ? "请用一句话介绍你自己。" : "人工智能正在改变企业知识管理。"}</textarea></label>
        ${docs}
        ${params}
      </div>
      <div id="md-result"></div>`,
      `<button class="btn" onclick="closeModal()">关闭</button>
       <button class="btn primary" id="md-run"
         onclick="KP.modelRun('${modelId}')">运行测试</button>`);
  }

  async function modelRun(modelId) {
    const box = el("md-result");
    const btn = el("md-run");
    if (box) box.innerHTML = '<div class="kp-meta">测试中…</div>';
    if (btn) { btn.disabled = true; btn.textContent = "测试中…"; }
    const payload = {
      input: val("md-input"),
      documents: val("md-docs") ? val("md-docs").split("\n").filter(Boolean) : undefined,
      temperature: el("md-temp") ? Number(val("md-temp")) : undefined,
      max_tokens: el("md-max") ? Number(val("md-max")) : undefined,
    };
    try {
      const r = await api(`/api/platform/models/${modelId}/test`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload) });
      if (!box) return;
      S._debugPreview = r.preview || "";
      const metrics = Object.entries(r.metrics || {}).filter(([, v]) =>
        v !== "" && v !== 0 && v !== null && v !== undefined);
      box.innerHTML = `<div class="kp-debug">
        <div class="kp-debug-banner ${r.ok ? "ok" : "err"}">
          <b>${r.ok ? "连通正常" : "连接失败"}</b>
          <span>${r.elapsed_ms || 0} ms</span>
        </div>
        ${metrics.length ? `<div class="kp-tags" style="margin-top:8px">${metrics
          .map(([k, v]) => `<span class="kp-tag">${E(k)} ${E(String(v))}</span>`).join("")}</div>` : ""}
        ${r.preview ? `<div class="kp-side-sec">返回内容</div>
          <div class="kp-pre">${E(r.preview)}</div>` : ""}
        ${r.error ? `<div class="kp-side-sec">错误详情</div>
          <div class="kp-pre" style="color:var(--err)">${E(r.error)}</div>` : ""}
        ${r.preview ? `<div style="margin-top:8px"><button class="btn sm"
          onclick="KP.copyDebug()">复制结果</button></div>` : ""}
      </div>`;
    } catch (e) {
      if (box) box.innerHTML = `<div class="kp-debug-banner err"><b>请求失败</b><span>${E(e.message)}</span></div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "运行测试"; }
    }
  }

  function copyDebug() {
    const text = S._debugPreview || "";
    if (!text) return;
    navigator.clipboard.writeText(text).then(
      () => toast("已复制", "ok"), () => toast("复制失败", "err"));
  }

  async function modelDelete(modelId) {
    if (!confirm("删除该模型配置？")) return;
    try {
      await api(`/api/platform/models/${modelId}`, { method: "DELETE" });
      toast("已删除", "ok"); renderModels();
    } catch (e) { toast(e.message, "err"); }
  }

  function openTargetHub() {
    closeTargetHub();
    const anchors = Array.from(document.querySelectorAll(".kp-inline-target"));
    const anchor = anchors.find((n) => n.offsetParent !== null) || anchors[0];
    const rect = anchor ? anchor.getBoundingClientRect()
      : { left: 120, bottom: 240, top: 200 };
    const width = Math.min(470, innerWidth - 24);
    const left = Math.max(12, Math.min(rect.left, innerWidth - width - 12));
    const kinds = Object.keys(CHAT_KINDS);
    const pop = document.createElement("div");
    pop.id = "kp-target-pop";
    pop.className = "kp-target-pop";
    pop.style.width = width + "px";
    pop.innerHTML = `<div class="kp-menu-head"><b>切换目标</b>
        <span class="kp-meta">Esc 关闭</span></div>
      <div class="kp-menu-grid">
        <div class="kp-menu-cats">${kinds.map((k) =>
          `<button data-k="${k}" class="${chatState().kind === k ? "on" : ""}">
            <span class="ci">${kindIcon(k)}</span><span>${CHAT_KINDS[k].label}</span>
            <span class="ca">›</span></button>`).join("")}</div>
        <div class="kp-menu-pane">
          <input id="kp-hub-search" class="kp-hub-search"
            placeholder="搜索…">
          <div id="kp-hub-body" class="kp-hub-body"></div>
        </div>
      </div>`;
    positionPopover(pop, rect);
    pop.querySelectorAll(".kp-menu-cats button").forEach((b) => {
      b.onclick = () => {
        pop.querySelectorAll(".kp-menu-cats button").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        const search = el("kp-hub-search");
        if (search) search.value = "";
        loadHub(b.dataset.k);
      };
    });
    const search = el("kp-hub-search");
    if (search) {
      search.oninput = () => {
        const q = search.value.trim().toLowerCase();
        document.querySelectorAll("#kp-hub-body .kp-row").forEach((row) => {
          row.style.display = !q || row.innerText.toLowerCase().includes(q) ? "" : "none";
        });
      };
      setTimeout(() => search.focus(), 40);
    }
    loadHub(chatState().kind);
    setTimeout(() => {
      document.addEventListener("click", targetHubOutside, true);
      document.addEventListener("keydown", targetHubEsc);
    }, 0);
  }

  function targetHubOutside(ev) {
    const pop = el("kp-target-pop");
    if (!pop) return;
    if (pop.contains(ev.target) || ev.target.closest(".kp-inline-target")) return;
    closeTargetHub();
    document.removeEventListener("click", targetHubOutside, true);
    document.removeEventListener("keydown", targetHubEsc);
  }

  function targetHubEsc(ev) {
    if (ev.key !== "Escape") return;
    closeTargetHub();
    document.removeEventListener("click", targetHubOutside, true);
    document.removeEventListener("keydown", targetHubEsc);
  }

  async function loadHub(kind) {
    const box = el("kp-hub-body");
    if (!box) return;
    if (kind === "kb") {
      box.innerHTML = `<div class="kp-row kp-menu-item" onclick="KP.hubUse('kb','')">
        <div class="kp-avatar" style="width:30px;height:30px;font-size:15px">📚</div>
        <div><b>知识问答</b><small>基于当前空间勾选的库与标签检索，逐句带出处</small></div>
        <span class="go">↵</span></div>`;
      return;
    }
    box.innerHTML = '<div class="kp-meta">加载中…</div>';
    try {
      if (kind === "external") {
        const rt = await api("/api/platform/runtimes");
        if (!rt.configured) {
          box.innerHTML = `<div class="kp-meta" style="line-height:1.8">
            未配置外部运行时。设置 <code>ZDATA_BASE_URL</code> 指向 zdata-agent-starter
            （默认 http://127.0.0.1:8080），重启后即可在这里看到它的智能体/团队/工作流。</div>`;
          return;
        }
        if (!rt.online) {
          box.innerHTML = `<div class="kp-meta">外部运行时离线：${E(rt.error || "")}</div>`;
          return;
        }
        S._externalItems = rt.items || [];
        box.innerHTML = `<div class="kp-list">${(rt.items || []).map((x) => `
          <div class="kp-row kp-menu-item" onclick="KP.hubUse('external','${E(x.id)}')">
            <div class="kp-avatar" style="width:30px;height:30px;font-size:14px">🔗</div>
            <div><b>${E(x.name)}</b><small>${E(x.kind)} · ${E(x.description || "zdata")}</small></div>
            <span class="go">↵</span></div>`).join("")
          || empty("外部运行时没有对象", "在 zdata 中注册 agent/team/workflow")}</div>`;
        return;
      }
      if (kind === "excel") {
        const items = (await api("/api/platform/excel/files")).items || [];
        box.innerHTML = `<div class="kp-head"><span class="kp-meta">已上传的 Excel</span>
          <span class="spacer" style="flex:1"></span>
          <button class="btn sm primary" onclick="KP.excelUploadDialog()">＋ 上传 Excel</button></div>
          <div class="kp-list">${items.map((f) => `<div class="kp-row kp-menu-item"
            onclick="KP.hubUse('excel','${f.id}')">
            <span style="font-size:17px">📊</span><div><b>${E(f.name)}</b>
            <small>${E(f.kind)} · ${fmt(f.created_at)}</small></div>
            <span class="go">↵</span></div>`).join("")
            || empty("还没有 Excel 文件", "上传 xlsx / xls / csv 开始")}</div>`;
        S._excelItems = items;
        return;
      }
      const endpoints = { agent: "/agents", expert: "/experts",
        team: "/teams", workflow: "/workflows" };
      const items = (await api("/api/platform" + endpoints[kind])).items || [];
      box.innerHTML = `<div class="kp-list">${items.map((x) => `<div class="kp-row kp-menu-item"
        onclick="KP.hubUse('${kind}','${E(x.id)}')">
        <div class="kp-avatar" style="width:30px;height:30px;font-size:14px">${E(x.avatar || CHAT_KINDS[kind].icon)}</div>
        <div><b>${E(x.name || x.title)}</b><small>${E(x.description || x.bio || (x.strategy ? "团队 · " + x.strategy : ""))}</small></div>
        <span class="go">↵</span></div>`).join("")
        || empty("还没有可用对象", "先在对应页面创建一个")}</div>`;
    } catch (e) { box.innerHTML = `<div class="kp-meta">${E(e.message)}</div>`; }
  }

  function hubUse(kind, id) {
    closeModal();
    closeTargetHub();
    if (kind === "kb") return setChatKind("kb");
    if (kind === "external") {
      const item = (S._externalItems || []).find((x) => x.id === id);
      if (!item) return;
      return applyTarget("external", { id: item.id, name: item.name,
        avatar: "🔗", status: "外部 · " + item.kind,
        remote_kind: item.kind, description: item.description });
    }
    if (kind === "excel") {
      const f = (S._excelItems || []).find((x) => x.id === id);
      return applyTarget("excel", { id, name: f ? f.name : "Excel",
        avatar: "📊", status: "Excel 问数" });
    }
    useTarget(kind, id);
  }



  function closeKbMenu() {
    const pop = el("kp-kb-pop");
    if (pop) pop.remove();
  }

  function openKbPlusMenu() {
    closeKbMenu();
    const anchor = el("kp-kb-plus");
    const rect = anchor ? anchor.getBoundingClientRect()
      : { left: 560, top: 700 };
    const pop = document.createElement("div");
    pop.id = "kp-kb-pop";
    pop.className = "kp-attach-pop";
    pop.style.width = "220px";
    const adhoc = state.chatMode === "adhoc";
    pop.innerHTML = `<button data-a="upload"><span>${uiIcon("upload")}</span>上传到当前库</button>
      <button data-a="adhoc"><span>${uiIcon("file")}</span>上传临时文件问答</button>
      <button data-a="url"><span>${uiIcon("link")}</span>从链接入库</button>
      <button data-a="scope"><span>${uiIcon("book")}</span>选择检索范围</button>
      <button data-a="mode"><span>${uiIcon("swap")}</span>${adhoc ? "切回库内问答" : "切换为临时问答"}</button>
      ${adhoc ? `<button data-a="ref"><span>${uiIcon(state.refLibs ? "check" : "file")}</span>同时参考已入库文档</button>` : ""}`;
    positionPopover(pop, rect);
    pop.querySelectorAll("button").forEach((b) => {
      b.onclick = () => {
        const act = b.dataset.a;
        closeKbMenu();
        const lib = state.lib || (state.libs && state.libs[0] && state.libs[0].id);
        if (act === "upload") {
          if (!lib) return toast("请先在知识空间创建库", "err");
          if (typeof uploadDialog === "function") uploadDialog(lib);
        } else if (act === "adhoc") {
          if (typeof setChatMode === "function") setChatMode("adhoc");
          setTimeout(() => el("adhoc-input")?.click(), 60);
        } else if (act === "url") {
          if (!lib) return toast("请先打开一个库再使用链接入库", "err");
          if (state.lib !== lib) {
            state.lib = lib;
          }
          if (typeof urlIngestDialog === "function") urlIngestDialog();
        } else if (act === "scope") {
          if (typeof toggleLibPop === "function") toggleLibPop();
        } else if (act === "mode") {
          if (typeof setChatMode === "function") {
            setChatMode(adhoc ? "kb" : "adhoc");
          }
        } else if (act === "ref") {
          state.refLibs = !state.refLibs;
          if (typeof syncCard === "function") syncCard();
          if (typeof welcomeScreen === "function") welcomeScreen();
        }
      };
    });
    setTimeout(() => document.addEventListener("click", kbMenuOutside, true), 0);
  }

  function kbMenuOutside(ev) {
    const pop = el("kp-kb-pop");
    if (!pop) return;
    if (pop.contains(ev.target) || ev.target.closest("#kp-kb-plus")) return;
    closeKbMenu();
    document.removeEventListener("click", kbMenuOutside, true);
  }

  function closeAttachMenu() {
    const pop = el("kp-attach-pop");
    if (pop) pop.remove();
  }

  function openAttachMenu() {
    closeAttachMenu();
    const anchor = document.querySelector(".kp-ws-foot .add");
    const rect = anchor ? anchor.getBoundingClientRect()
      : { left: 320, top: 700, bottom: 730 };
    const pop = document.createElement("div");
    pop.id = "kp-attach-pop";
    pop.className = "kp-attach-pop";
    pop.style.width = "200px";
    pop.innerHTML = `<button data-a="upload"><span>${uiIcon("upload")}</span>上传文件</button>
      <button data-a="paste"><span>${uiIcon("file")}</span>粘贴剪贴板文件</button>
      <button data-a="cancel"><span>${uiIcon("close")}</span>取消</button>`;
    positionPopover(pop, rect);
    pop.querySelectorAll("button").forEach((b) => {
      b.onclick = () => {
        const act = b.dataset.a;
        closeAttachMenu();
        if (act === "upload") el("kp-ws-file").click();
        else if (act === "paste") pasteAttachments();
      };
    });
    setTimeout(() => document.addEventListener("click", attachOutside, true), 0);
  }

  function attachOutside(ev) {
    const pop = el("kp-attach-pop");
    if (!pop) return;
    if (pop.contains(ev.target) || ev.target.closest("#kp-ws-add")) return;
    closeAttachMenu();
    document.removeEventListener("click", attachOutside, true);
  }

  async function pasteAttachments() {
    try {
      const items = await navigator.clipboard.read();
      const files = [];
      for (const item of items) {
        for (const type of item.types) {
          if (!type.startsWith("image/") && type !== "text/plain") continue;
          const blob = await item.getType(type);
          const ext = type === "text/plain" ? "txt" : type.split("/")[1];
          files.push(new File([blob], `paste-${Date.now()}.${ext}`, { type }));
        }
      }
      if (!files.length) return toast("剪贴板里没有可添加的文件", "err");
      wsUpload(files);
    } catch (e) { toast("无法读取剪贴板：" + e.message, "err"); }
  }


  function subControlsHTML() {
    const c = chatState();
    if (c.kind === "external") return "";
    const nSkill = (c.skillIds || []).length;
    const nTool = (c.toolIds || []).length;
    const nKb = (c.kbIds || []).length;
    const kbBtn = `<button class="kp-sub-btn ${nKb ? "on" : ""}"
      onclick="KP.openKBPicker(event)" title="给当前目标挂载知识库">知识库 ${nKb || ""}</button>`;
    if (c.kind === "kb") {
      return `${kbBtn}<button class="kp-sub-btn" onclick="KP.openSkillPicker(event)"
        title="本次对话附加技能">技能 ${nSkill || ""}</button>`;
    }
    if (c.kind === "excel" || c.kind === "workflow") {
      return `${kbBtn}<button class="kp-sub-btn" onclick="KP.openSkillPicker(event)"
        title="本次对话附加技能">技能 ${nSkill || ""}</button>`;
    }
    if (!["agent", "expert", "team"].includes(c.kind)) return "";
    return `${kbBtn}
      <button class="kp-sub-btn" onclick="KP.openSkillPicker(event)" title="本次对话附加技能">
        技能 ${nSkill || ""}</button>
      <button class="kp-sub-btn" onclick="KP.openToolPicker(event)" title="本次对话启用工具">工具 ${nTool || ""}</button>`;
  }

  async function openKBPicker(ev) {
    let items = [];
    try { items = (await api("/api/kb2/bases")).items || []; }
    catch (e) { return toast(e.message, "err"); }
    const c = chatState();
    const selected = new Set(c.kbIds || []);
    openLocalPicker(ev, "挂载到当前目标的知识库", `
      <div class="kp-picker-list">
        ${items.map((b) => `<label class="kp-skill-row">
          <input type="checkbox" class="kp-kb-check" value="${b.id}"
            ${selected.has(b.id) ? "checked" : ""}>
          <span><b>${E(b.name)}</b><small>${b.docs} 文档 · ${num(b.chunks)} 分块</small></span>
        </label>`).join("") || `<div class="kp-meta">还没有分块知识库，
          <a href="#" onclick="closeModal();KP.kb2CreateForm();return false">先新建一个</a></div>`}
      </div>
      <div class="kp-meta" style="margin-top:8px">挂载后，问答会自动检索；
        若同时上传了 Excel，会分别取数与取依据再合并回答。</div>`, saveKBPicker);
  }

  async function saveKBPicker() {
    const c = chatState();
    c.kbIds = Array.from(document.querySelectorAll(".kp-kb-check:checked"))
      .map((n) => n.value);
    closeLocalPicker();
    refreshSubControls();
    if (["agent", "team", "workflow"].includes(c.kind) && c.id) {
      const path = c.kind === "team" ? "/api/platform/teams/"
        : c.kind === "workflow" ? "/api/platform/workflows/"
        : "/api/platform/agents/";
      try {
        await api(path + c.id, { method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kb_ids: c.kbIds }) });
        toast(c.kbIds.length ? "知识库已挂载并保存" : "已取消挂载", "ok");
      } catch (e) { toast(e.message, "err"); }
    } else {
      toast(c.kbIds.length ? "本次问答已挂载知识库" : "已取消挂载", "ok");
    }
  }

  function currentSkillIds() {
    return chatState().skillIds || [];
  }

  function currentKBIds() {
    return chatState().kbIds || [];
  }

  function refreshSubControls() {
    const box = el("kp-ws-subs");
    if (box) box.innerHTML = subControlsHTML();
    const kb = el("kp-kb-subs");
    if (kb) kb.innerHTML = chatState().kind === "kb" ? subControlsHTML() : "";
  }

  function closeLocalPicker() {
    const pop = el("kp-picker-pop");
    if (pop) pop.remove();
    document.removeEventListener("click", localPickerOutside, true);
  }

  function localPickerOutside(ev) {
    const pop = el("kp-picker-pop");
    if (!pop) return;
    if (pop.contains(ev.target) || ev.target.closest(".kp-sub-btn")) return;
    closeLocalPicker();
  }

  function openLocalPicker(ev, title, bodyHTML, onApply) {
    closeLocalPicker();
    const anchor = (ev && ev.currentTarget) || document.querySelector(".kp-sub-btn");
    const rect = anchor ? anchor.getBoundingClientRect()
      : { left: 320, top: 600, bottom: 630 };
    const width = Math.min(340, innerWidth - 24);
    const left = Math.max(12, Math.min(rect.left, innerWidth - width - 12));
    const pop = document.createElement("div");
    pop.id = "kp-picker-pop";
    pop.className = "kp-picker-pop";
    pop.style.width = width + "px";
    pop.innerHTML = `<div class="kp-picker-head"><b>${E(title)}</b>
        <button class="kp-picker-x">✕</button></div>
      <div class="kp-picker-body">${bodyHTML}</div>
      <div class="kp-picker-foot">
        <button class="btn sm" data-a="cancel">取消</button>
        <button class="btn sm primary" data-a="apply">应用</button></div>`;
    positionPopover(pop, rect);
    pop.querySelector(".kp-picker-x").onclick = closeLocalPicker;
    pop.querySelector('[data-a="cancel"]').onclick = closeLocalPicker;
    pop.querySelector('[data-a="apply"]').onclick = onApply;
    setTimeout(() => document.addEventListener("click", localPickerOutside, true), 0);
  }

  async function openSkillPicker(ev) {
    let items = [];
    try { items = (await api("/api/platform/skills?enabled=1")).items || []; }
    catch (e) { return toast(e.message, "err"); }
    const c = chatState();
    const selected = new Set(c.skillIds || []);
    openLocalPicker(ev, "本次对话的技能", `
      <input id="kp-skill-q" class="kp-hub-search" placeholder="搜索技能…">
      <div class="kp-picker-list" id="kp-skill-list">
        ${items.map((x) => `<label class="kp-skill-row" data-name="${E((x.name + " " + x.category).toLowerCase())}">
          <input type="checkbox" class="kp-skill-check" value="${x.id}" ${selected.has(x.id) ? "checked" : ""}>
          <span><b>${E(x.name)}</b><small>${E(x.kind)} · ${E(x.category)}</small></span></label>`).join("")
          || '<div class="kp-meta">尚未安装技能</div>'}
      </div>`, saveSkillPicker);
    const search = el("kp-skill-q");
    search.oninput = () => {
      const q = search.value.trim().toLowerCase();
      document.querySelectorAll("#kp-skill-list .kp-skill-row").forEach((row) => {
        row.style.display = !q || row.dataset.name.includes(q) ? "" : "none";
      });
    };
    setTimeout(() => search.focus(), 40);
  }

  function saveSkillPicker() {
    const c = chatState();
    c.skillIds = Array.from(document.querySelectorAll(".kp-skill-check:checked"))
      .map((n) => n.value);
    closeLocalPicker();
    refreshSubControls();
    toast("技能已更新（本次对话）", "ok");
  }

  async function openToolPicker(ev) {
    let items = [];
    try { items = (await api("/api/platform/tools")).items || []; }
    catch (e) { return toast(e.message, "err"); }
    const c = chatState();
    const selected = new Set(c.toolIds || []);
    openLocalPicker(ev, "本次对话的工具", `
      <div class="kp-picker-list">
        ${items.map((x) => `<label class="kp-skill-row"><input type="checkbox"
          class="kp-tool-check" value="${x.id}" ${selected.has(x.id) ? "checked" : ""}>
          <span><b>${E(x.name)}</b><small>${E(x.risk)} · ${E(x.description).slice(0, 60)}</small></span></label>`).join("")}
      </div>`, saveToolPicker);
  }

  function saveToolPicker() {
    const c = chatState();
    c.toolIds = Array.from(document.querySelectorAll(".kp-tool-check:checked"))
      .map((n) => n.value);
    closeLocalPicker();
    refreshSubControls();
    toast("工具已更新（本次对话）", "ok");
  }


  function startNewChat() {
    const c = chatState();
    if (typeof state !== "undefined" && state.view === "chat" && c.kind !== "kb") {
      c.messages = [];
      c.files = [];
      c.steps = [];
      c.sessionId = "";
      c.runId = "";
      c.lastPrompt = "";
      renderWorkspace();
      renderWsTrace();
      toast("已开始新对话", "ok");
      return;
    }
    if (typeof switchView === "function") {
      state.sessionId = null;
      state.live = null;
      switchView("chat");
      toast("已开始新对话", "ok");
    }
  }

  async function exportConversation() {
    let lines = [];
    const c = chatState();
    if (typeof state !== "undefined" && state.view === "chat" && c.kind !== "kb") {
      lines.push(`# ${c.name || "对话"}\n`);
      (c.messages || []).forEach((m) => {
        lines.push(`**${m.role === "user" ? "用户" : "助手"}**\n\n${m.text || ""}\n`);
      });
    } else if (state.sessionId) {
      try {
        const data = await api(`/api/sessions/${state.sessionId}`);
        lines.push(`# ${data.title || "对话"}\n`);
        (data.messages || []).forEach((m) => {
          lines.push(`**${m.role === "user" ? "用户" : "助手"}**\n\n${m.content || ""}\n`);
        });
      } catch (e) { return toast(e.message, "err"); }
    }
    if (!lines.length) return toast("当前没有可导出的内容", "err");
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "knowlens-conversation.md";
    a.click(); URL.revokeObjectURL(a.href);
    toast("已导出 Markdown", "ok");
  }

  function slashCommands() {
    const c = chatState();
    const items = [
      { id: "new", label: "新对话", hint: "清空当前会话" },
      { id: "target", label: "切换目标", hint: "知识 / 智能体 / 团队 / 工作流 / 问数" },
    ];
    if (["kb", "agent", "expert", "team"].includes(c.kind)) {
      items.push({ id: "skill", label: "选择技能", hint: "本次对话叠加" });
    }
    if (["agent", "expert", "team"].includes(c.kind)) {
      items.push({ id: "tool", label: "选择工具", hint: "本次对话启用" });
    }
    if (c.kind === "kb") {
      items.push({ id: "attach", label: "添加资料", hint: "上传/链接/范围" });
    } else if (c.kind !== "excel" && c.kind !== "workflow") {
      items.push({ id: "attach", label: "添加附件", hint: "上传或粘贴文件" });
    }
    items.push({ id: "export", label: "导出对话", hint: "Markdown" });
    items.push({ id: "share", label: "分享对话", hint: "生成只读链接" });
    return items;
  }

  function closeSlashMenu() {
    el("kp-slash-pop")?.remove();
    document.removeEventListener("click", slashOutside, true);
  }

  function slashOutside(ev) {
    const pop = el("kp-slash-pop");
    if (!pop) return;
    if (pop.contains(ev.target) || ev.target.matches("#q-input, #kp-ws-input")) return;
    closeSlashMenu();
  }

  function openSlashMenu(input) {
    closeSlashMenu();
    const rect = input.getBoundingClientRect();
    const pop = document.createElement("div");
    pop.id = "kp-slash-pop";
    pop.className = "kp-slash-pop";
    pop.style.width = Math.min(340, innerWidth - 24) + "px";
    pop.innerHTML = `<div class="kp-slash-head">命令</div>` +
      slashCommands().map((x) => `<button data-c="${x.id}">
        <span class="sl">/${x.label}</span><span class="sh">${E(x.hint)}</span></button>`).join("");
    positionPopover(pop, rect);
    pop.querySelectorAll("button").forEach((b) => {
      b.onclick = () => runSlash(b.dataset.c, input);
    });
    pop.querySelector("button")?.classList.add("on");
    setTimeout(() => document.addEventListener("click", slashOutside, true), 0);
  }

  function runSlash(id, input) {
    closeSlashMenu();
    input.value = "";
    const anchor = { currentTarget: input };
    if (id === "new") startNewChat();
    else if (id === "target") openTargetHub();
    else if (id === "skill") openSkillPicker(anchor);
    else if (id === "tool") openToolPicker(anchor);
    else if (id === "attach") {
      if (chatState().kind === "kb") openKbPlusMenu();
      else openAttachMenu();
    } else if (id === "export") exportConversation();
    else if (id === "share") shareConversation();
  }

  function bindSlash() {
    document.addEventListener("input", (ev) => {
      const t = ev.target;
      if (!t || !t.matches("#q-input, #kp-ws-input")) return;
      if (t.value.trim() === "/") openSlashMenu(t);
      else if (t.value.trim() !== "") closeSlashMenu();
    });
    document.addEventListener("keydown", (ev) => {
      const pop = el("kp-slash-pop");
      if (!pop) return;
      if (ev.key === "Escape") { closeSlashMenu(); return; }
      const buttons = Array.from(pop.querySelectorAll("button"));
      if (!buttons.length) return;
      const idx = buttons.findIndex((b) => b.classList.contains("on"));
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault(); ev.stopPropagation();
        const next = ev.key === "ArrowDown"
          ? Math.min(buttons.length - 1, idx + 1)
          : Math.max(0, idx - 1);
        buttons.forEach((b) => b.classList.remove("on"));
        buttons[next].classList.add("on");
      } else if (ev.key === "Enter" && idx >= 0) {
        ev.preventDefault(); ev.stopPropagation();
        runSlash(buttons[idx].dataset.c, el("q-input") || el("kp-ws-input"));
      }
    }, true);
  }


  function shareConversation() {
    const c = chatState();
    const platform = state.view === "chat" && c.kind !== "kb";
    if (platform && !(c.messages || []).length) {
      return toast("当前没有可分享的消息", "err");
    }
    if (!platform && !state.sessionId) {
      return toast("先提一个问题再分享", "err");
    }
    openModal("分享对话", `<div class="kp-form">
      <label>访问密码（可选）<input id="kp-share-pw" type="password"
        placeholder="留空表示任何人可看"></label>
      <label>有效期（天，0 表示永久）<input id="kp-share-days"
        type="number" min="0" max="365" value="0"></label>
      <div class="kp-meta">分享的是当前时刻的快照，不包含账号或密钥。</div>
    </div>`, `<button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" onclick="KP.doCreateShare()">生成链接</button>`);
  }

  async function doCreateShare() {
    const c = chatState();
    const platform = state.view === "chat" && c.kind !== "kb";
    let body;
    if (platform) {
      body = { kind: "snapshot", title: (c.name || "对话") + " · 对话",
        payload: { messages: c.messages.map((m) => ({
          role: m.role, content: m.text || "",
        })) } };
    } else {
      body = { kind: "session", ref_id: state.sessionId };
    }
    body.password = el("kp-share-pw")?.value || "";
    body.days = Number(el("kp-share-days")?.value || 0);
    try {
      const r = await api("/api/platform/shares", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      const url = location.origin + r.url;
      openModal("分享已生成", `<div class="kp-form">
        <label>只读链接<input id="kp-share-url" value="${E(url)}" readonly></label>
        <div class="kp-meta">${r.protected ? "已设置访问密码。" : "未设置密码，拿到链接即可查看。"}
        ${r.expires_at ? "有效期至 " + E(r.expires_at) : "永久有效"}。</div>
      </div>`, `<button class="btn" onclick="closeModal()">关闭</button>
        <button class="btn primary" onclick="KP.copyShareLink()">复制链接</button>
        <a class="btn" href="${E(url)}" target="_blank" rel="noopener">打开</a>`);
    } catch (e) { toast(e.message, "err"); }
  }

  function copyShareLink() {
    const node = el("kp-share-url");
    if (!node) return;
    if (navigator.clipboard) navigator.clipboard.writeText(node.value).then(
      () => toast("链接已复制", "ok"), () => toast("复制失败", "err"));
    else { node.select(); document.execCommand("copy"); toast("链接已复制", "ok"); }
  }

  function diffLines(before, after) {
    const a = String(before || "").split("\n").slice(0, 800);
    const b = String(after || "").split("\n").slice(0, 800);
    const dp = Array.from({ length: a.length + 1 },
      () => new Array(b.length + 1).fill(0));
    for (let i = a.length - 1; i >= 0; i -= 1) {
      for (let j = b.length - 1; j >= 0; j -= 1) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    const out = [];
    let i = 0, j = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { out.push({ t: "=", s: a[i] }); i += 1; j += 1; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: "-", s: a[i] }); i += 1; }
      else { out.push({ t: "+", s: b[j] }); j += 1; }
    }
    while (i < a.length) { out.push({ t: "-", s: a[i] }); i += 1; }
    while (j < b.length) { out.push({ t: "+", s: b[j] }); j += 1; }
    return out;
  }

  async function compareVersion(agentId, version) {
    try {
      const [current, old] = await Promise.all([
        api(`/api/platform/agents/${agentId}`),
        api(`/api/platform/agents/${agentId}/versions/${version}`),
      ]);
      const snap = old.snapshot || {};
      const fields = [
        ["模型", current.model || "默认", snap.model || "默认"],
        ["温度", current.temperature, snap.temperature],
        ["最大步数", current.max_steps, snap.max_steps],
        ["技能数", arr(current.skill_ids).length, arr(snap.skill_ids).length],
        ["工具数", arr(current.tool_ids).length, arr(snap.tool_ids).length],
        ["状态", current.status, snap.status],
      ];
      const rows = fields.map(([k, nowV, oldV]) => `<tr><td>${E(k)}</td>
        <td>${E(oldV)}</td><td class="${String(nowV) === String(oldV) ? "" : "diff"}">${E(nowV)}</td></tr>`).join("");
      const diff = diffLines(snap.system_prompt || "", current.system_prompt || "");
      openModal(`版本对比 · v${version} → 当前`, `
        <div class="kp-drawer-sec"><h5>配置</h5>
          <table class="kp-diff-table"><thead><tr><th>字段</th><th>v${version}</th><th>当前</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <div class="kp-drawer-sec"><h5>角色设定差异</h5>
          <pre class="kp-diff">${diff.map((d) => `<span class="d${d.t === "=" ? "" : d.t === "+" ? "add" : "del"}">${d.t === "=" ? "  " : d.t + " "}${E(d.s)}</span>`).join("\n")}</pre></div>
        <div class="kp-meta">左侧为 v${version}，右侧为当前草稿/发布版。</div>`,
        `<button class="btn" onclick="closeModal()">关闭</button>
         <button class="btn primary" onclick="KP.agentRollback('${agentId}',${version})">回滚到 v${version}</button>`);
    } catch (e) { toast(e.message, "err"); }
  }

  function toggleTrace() {
    const ws = el("kp-ws");
    if (!ws) return;
    ws.classList.toggle("trace-open");
    S.traceClosed = !ws.classList.contains("trace-open");
  }

  function setChatKind(kind) {
    if (kind === "kb") {
      saveDraft();
      S.chat = { ...chatState(), kind: "kb", id: "", name: "知识问答",
        avatar: "📚", status: "", sessionId: "", messages: [], files: [],
        workflow: null };
      closeTargetHub();
      if (state.view !== "chat") switchView("chat");
      else mountChatTargets();
      return;
    }
    openTargetPicker(kind, true);
  }

  async function openTargetPicker(kind, switchTo) {
    if (kind === "kb") return setChatKind("kb");
    if (kind === "excel") return openExcelPicker();
    const endpoints = { agent: "/agents", expert: "/experts",
      team: "/teams", workflow: "/workflows" };
    let items = [];
    try { items = (await api("/api/platform" + endpoints[kind])).items || []; }
    catch (e) { toast(e.message, "err"); return; }
    const pick = (id) => { closeModal(); applyTarget(kind, items.find((x) => x.id === id)); };
    openDrawerLike("选择" + CHAT_KINDS[kind].label, `
      <div class="kp-form"><label>搜索<input id="kp-pick-q" placeholder="输入名称筛选"></label></div>
      <div class="kp-list" id="kp-pick-list">
        ${items.map((x) => `<div class="kp-row kp-pick" data-name="${E((x.name || x.title || "").toLowerCase())}">
          <div class="kp-avatar" style="width:32px;height:32px;font-size:15px">${E(x.avatar || CHAT_KINDS[kind].icon)}</div>
          <div><b>${E(x.name || x.title)}</b>
            <small>${E(x.description || x.bio || (x.strategy ? "团队 · " + x.strategy : ""))}</small></div>
          <span class="spacer"></span>${badge(x.status || "ready")}
          <button class="btn sm primary" data-id="${E(x.id)}">使用</button></div>`).join("")
          || empty("还没有可用对象", "先在对应页面创建一个")}
      </div>`,
      `<button class="btn" onclick="closeModal()">取消</button>`);
    el("kp-pick-q").oninput = (ev) => {
      const q = ev.target.value.trim().toLowerCase();
      document.querySelectorAll(".kp-pick").forEach((row) => {
        row.style.display = !q || row.dataset.name.includes(q) ? "" : "none";
      });
    };
    document.querySelectorAll("#kp-pick-list button[data-id]").forEach((b) => {
      b.onclick = () => pick(b.dataset.id);
    });
    S._pickerSwitch = switchTo;
  }

  async function useTarget(kind, id) {
    try {
      let item = null;
      if (kind === "expert") {
        item = (await api("/api/platform/experts")).items.find((x) => x.id === id);
      } else if (kind === "team") {
        item = (await api("/api/platform/teams")).items.find((x) => x.id === id);
      } else if (kind === "agent") {
        item = await api(`/api/platform/agents/${id}`);
      } else {
        item = await api(`/api/platform/workflows/${id}`);
      }
      if (!item) return toast("对象不存在", "err");
      applyTarget(kind, item);
    } catch (e) { toast(e.message, "err"); }
  }

  function applyTarget(kind, item) {
    if (!item) return;
    saveDraft();
    S.chat = { kind, id: item.id, name: item.name || item.title || "未命名",
      avatar: item.avatar || CHAT_KINDS[kind].icon,
      status: kind === "workflow" ? "工作流" : (item.status || ""),
      sessionId: "", messages: [], files: [], running: false,
      workflow: kind === "workflow" ? item : null, runId: "", steps: [],
      skillIds: arr(item.skill_ids), toolIds: arr(item.tool_ids),
      kbIds: arr(item.kb_ids),
      remoteKind: item.remote_kind || item.remoteKind || "" };
    if (state.view !== "chat") switchView("chat");
    else mountChatTargets();
  }

  function renderWorkspace() {
    const c = chatState();
    const ws = el("kp-ws");
    if (!ws) return;
    if (innerWidth >= 1360 && !S.traceClosed) ws.classList.add("trace-open");
    const isFlow = c.kind === "workflow";
    const isExcel = c.kind === "excel";
    const isExternal = c.kind === "external";
    const isTeam = c.kind === "team";
    ws.innerHTML = `<div class="kp-ws-main">
        ${isExcel ? '<div class="kp-excel-profile" id="kp-ws-profile"></div>' : ""}
        <div class="kp-ws-msgs" id="kp-ws-msgs"></div>
        <div id="kp-ws-formwrap"></div>
        <div class="kp-ws-composer">
          <div class="kp-composer-top">
            <button class="kp-inline-target" onclick="KP.openTargetHub()"
              title="切换目标">${inlineTargetHTML()}</button>
            <span class="kp-subs" id="kp-ws-subs">${subControlsHTML()}</span>
          </div>
          <div class="kp-file-chips" id="kp-ws-files"></div>
          <textarea id="kp-ws-input" placeholder="${isFlow ? "补充说明（可选）…" : isExcel ? "针对这张表提问，例如「哪个月销售额最高？」" : isExternal ? "向外部智能体提问…" : "输入消息，Enter 发送，Shift+Enter 换行"}"></textarea>
          <div class="kp-ws-foot">
            ${isExcel || isExternal ? "" : `<button class="add" id="kp-ws-add" title="添加附件">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2"
                  stroke-linecap="round"/></svg></button>
            <span class="kp-attach-txt"><svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M21 12.5l-8.4 8.4a5 5 0 0 1-7.1-7.1l8.5-8.5a3.4 3.4 0 0 1 4.8 4.8l-8.5 8.5a1.8 1.8 0 0 1-2.5-2.5l7.9-7.9"
                stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>附件</span>`}
            <span class="kp-meta" id="kp-ws-hint">${isFlow ? "填写上方表单后运行" : isExcel ? "问数基于当前文件，不会修改原文件" : isExternal ? "由外部 zdata 运行时执行" : "支持 PDF / Word / Excel / 文本附件"}</span>
            <button class="btn sm ghost kp-trace-btn" onclick="KP.toggleTrace()" title="运行轨迹">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" stroke="currentColor"
                  stroke-width="1.8" stroke-linecap="round"/></svg> 轨迹</button>
            <button class="send ${isFlow || isExcel ? "" : "icon-only"}" id="kp-ws-send"
              title="${isFlow ? "运行" : isExcel ? "问数" : "发送"}">${isFlow ? "运行" : isExcel ? "问数" : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`}</button>
          </div>
          <input type="file" id="kp-ws-file" multiple style="display:none">
        </div>
      </div>
      <aside class="kp-ws-side" id="kp-ws-side">
        <div style="display:flex;align-items:center;gap:8px"><b>上下文</b>
          <span class="spacer" style="flex:1"></span><span class="kp-meta" id="kp-ws-run"></span>
          <button class="kp-side-close" onclick="KP.toggleTrace()" title="关闭轨迹">✕</button></div>
        <div class="kp-side-sec">上下文</div>
        <div class="kp-side-ctx">
          <div><b>${E(c.kind === "kb" ? "知识问答" : (c.name || "目标"))}</b></div>
          <div class="kp-meta">${E(c.kind === "excel" ? "Excel 问数"
            : c.kind === "team" ? "团队 · " + arr(c.members).length + " 人"
            : c.kind === "workflow" ? "工作流 · " + arr((c.workflow || {}).nodes).length + " 节点"
            : "智能体")}</div>
          ${(c.files || []).length ? `<div class="kp-file-chips" style="margin-top:6px">
            ${c.files.map((f) => `<span class="chip">${E(f.name)}</span>`).join("")}</div>` : ""}
        </div>
        <div class="kp-side-sec">运行轨迹</div>
        ${isFlow ? '<div class="kp-side-sec">流程图</div><div id="kp-ws-flow"></div>' : ""}
        ${isTeam ? '<div class="kp-side-sec">团队发言</div><div id="kp-ws-team"></div>' : ""}
        <div id="kp-ws-steps" style="margin-top:8px"></div>
        <div id="kp-ws-artifacts"></div>
      </aside>`;
    if (isFlow) {
      const schema = arr(c.workflow.input_schema);
      el("kp-ws-formwrap").innerHTML = renderWorkflowForm(schema);
      el("kp-ws-formwrap").className = "kp-ws-form";
    }
    if (el("kp-ws-add")) el("kp-ws-add").onclick = openAttachMenu;
    if (el("kp-ws-file")) el("kp-ws-file").onchange = (ev) => wsUpload(ev.target.files);
    el("kp-ws-send").onclick = wsSend;
    el("kp-ws-input").onkeydown = (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); wsSend(); }
    };
    el("kp-ws-input").onpaste = (ev) => {
      if (ev.clipboardData && ev.clipboardData.files.length) {
        ev.preventDefault();
        wsUpload(ev.clipboardData.files);
      }
    };
    renderWsMessages();
    renderWsFiles();
    renderWsTrace();
    if (isFlow) renderWsFlow();
    if (isTeam) renderTeamTimeline();
    if (isExcel && c.id) loadExcelProfile();
  }

  function renderWorkflowForm(schema) {
    if (!schema.length) {
      return `<div class="kp-field"><label>输入</label>
        <textarea id="kp-f-input" placeholder="这次运行要处理什么？"></textarea></div>`;
    }
    return schema.map((f) => {
      const key = `kp-f-${f.key}`;
      const label = `${E(f.label || f.key)}${f.required ? ' <span class="req">*</span>' : ""}`;
      if (f.type === "textarea") {
        return `<div class="kp-field"><label>${label}</label>
          <textarea id="${key}" placeholder="${E(f.placeholder || "")}"></textarea></div>`;
      }
      if (f.type === "radio" || f.type === "select") {
        const opts = arr(f.options).map((o) => `<option value="${E(o.value)}">${E(o.label)}</option>`).join("");
        return `<div class="kp-field"><label>${label}</label>
          <select id="${key}">${opts}</select></div>`;
      }
      if (f.type === "file_list") {
        return `<div class="kp-field"><label>${label}</label>
          <div class="kp-meta">通过下方 📎 添加附件（最多 ${f.max || 5} 个）</div></div>`;
      }
      return `<div class="kp-field"><label>${label}</label>
        <input id="${key}" type="${f.type === "number" ? "number" : "text"}"
          placeholder="${E(f.placeholder || "")}"></div>`;
    }).join("");
  }

  function collectWorkflowInputs() {
    const c = chatState();
    const schema = arr((c.workflow || {}).input_schema);
    const inputs = {};
    if (!schema.length) {
      inputs.input = (el("kp-f-input")?.value || "").trim();
      return inputs;
    }
    schema.forEach((f) => {
      if (f.type === "file_list") return;
      const node = el(`kp-f-${f.key}`);
      if (node) inputs[f.key] = node.value.trim();
    });
    return inputs;
  }

  function renderWsMessages() {
    const box = el("kp-ws-msgs");
    if (!box) return;
    const c = chatState();
    if (!c.messages.length) {
      const emptyText = c.kind === "workflow"
        ? "填写运行输入，点击「运行」；结果与审批会出现在这里。"
        : c.kind === "excel"
          ? "直接问这张表；也可以先点上面的推荐问题。"
          : `与「${E(c.name)}」开始对话。可以添加文件或直接提问。`;
      box.innerHTML = `<div class="kp-ws-empty">${emptyText}</div>`;
      const main = document.querySelector(".kp-ws-main");
      if (main) main.classList.add("home");
      return;
    }
    box.innerHTML = c.messages.map((m, i) => {
      if (m.role === "user") {
        return `<div class="kp-msg user">${E(m.text)}</div>`;
      }
      if (chatState().kind === "excel" && (m.result !== undefined || m.chart)) {
        return `<div class="kp-msg assistant" id="kp-ws-last-${i}">${renderExcelResult(m, i)}
          <div class="kp-msg-actions"><button onclick="KP.copyMessage(${i})">复制</button>
          <button onclick="KP.wsRegenerate(${i})">重新生成</button></div></div>`;
      }
      const parsed = md2html(m.text || "");
      return `<div class="kp-msg assistant" id="kp-ws-last-${i}">${parsed.html}
        <div class="kp-msg-actions"><button onclick="KP.copyMessage(${i})">复制</button>
          <button onclick="KP.wsRegenerate(${i})">重新生成</button></div></div>`;
    }).join("");
    box.scrollTop = box.scrollHeight;
    const main = document.querySelector(".kp-ws-main");
    if (main) main.classList.toggle("home", !c.messages.length);
    if (chatState().kind === "excel") setTimeout(mountExcelCharts, 60);
  }

  function renderWsFiles() {
    const box = el("kp-ws-files");
    if (!box) return;
    box.innerHTML = (chatState().files || []).map((f) => `<span class="chip">
      ${E(f.kind === "xlsx" ? "📊" : "📄")} ${E(f.name)}
      <small>${f.chars ? num(f.chars) + "字" : ""}</small>
      <button onclick="KP.wsRemoveFile('${f.id}')">×</button></span>`).join("");
  }

  async function wsUpload(files) {
    if (!files || !files.length) return;
    const fd = new FormData();
    Array.from(files).slice(0, 10).forEach((f) => fd.append("files", f));
    try {
      const r = await api("/api/platform/files", { method: "POST", body: fd });
      for (const item of r.items || []) {
        if (item.error) toast(item.error, "err");
        else chatState().files.push(item);
      }
      renderWsFiles();
    } catch (e) { toast("上传失败：" + e.message, "err"); }
    const input = el("kp-ws-file");
    if (input) input.value = "";
  }

  async function wsRemoveFile(id) {
    chatState().files = (chatState().files || []).filter((f) => f.id !== id);
    renderWsFiles();
    try { await api(`/api/platform/files/${id}`, { method: "DELETE" }); } catch (e) {}
  }

  function wsTraceStep(name, status, detail) {
    const box = el("kp-ws-steps");
    if (!box) return;
    const row = document.createElement("div");
    row.className = "kp-ws-step " + (status || "");
    row.innerHTML = `<span class="dot">${status === "done" ? "✓" : status === "error" ? "!" : "●"}</span>
      <div><b>${E(name)}</b><small>${E(detail || "")}</small></div>`;
    box.appendChild(row);
  }

  function renderWsTrace() {
    const box = el("kp-ws-steps");
    if (!box) return;
    box.innerHTML = "";
    (chatState().steps || []).forEach((s) => wsTraceStep(s.name, s.status, s.detail));
    const run = el("kp-ws-run");
    if (run) run.textContent = chatState().runId || "";
  }


  function renderWsFlow() {
    const c = chatState();
    const box = el("kp-ws-flow");
    if (!box || !c.workflow) return;
    box.innerHTML = renderFlowGraph(c.workflow.nodes || [], c.activeNode || "");
  }

  function renderTeamTimeline() {
    const c = chatState();
    const box = el("kp-ws-team");
    if (!box) return;
    const turns = arr(c.turns);
    box.innerHTML = turns.length ? turns.map((t, i) => `<div class="kp-turn">
      <span class="idx">${i + 1}</span>
      <div><b>${E(t.name || "成员")}</b>
        <span class="kp-tag">${E(t.role || "")}</span>
        ${t.reason ? `<small>原因：${E(t.reason)}</small>` : ""}
        <small class="out">${E(String(t.output || "").slice(0, 90))}</small></div></div>`).join("")
      : '<div class="kp-meta">运行后会显示每位成员的发言顺序与交接原因。</div>';
  }

  async function wsSend() {
    const c = chatState();
    if (c.running) return;
    const text = (el("kp-ws-input")?.value || "").trim();
    const isFlow = c.kind === "workflow";
    const isTeam = c.kind === "team";
    let inputs = {};
    if (isFlow) {
      inputs = collectWorkflowInputs();
      const required = arr((c.workflow || {}).input_schema).filter((f) =>
        f.required && f.type !== "file_list" && !((inputs[f.key] || "").trim()));
      if (required.length) return toast("请填写：" + required.map((f) => f.label).join("、"), "err");
      if (!Object.values(inputs).some((v) => String(v || "").trim()) && !c.files.length) {
        return toast("请填写运行输入", "err");
      }
    } else if (!text) {
      return toast("请输入消息", "err");
    }
    const fileIds = (c.files || []).map((f) => f.id);
    c.messages.push({ role: "user",
      text: isFlow ? ("运行：" + JSON.stringify(inputs)) : text });
    c.messages.push({ role: "assistant", text: "" });
    c.steps = [];
    c.turns = [];
    c.activeNode = "";
    c.running = true;
    c.lastPrompt = text;
    el("kp-ws-input").value = "";
    const sendBtn = el("kp-ws-send");
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.textContent = "停止";
      sendBtn.onclick = wsStop;
      sendBtn.classList.add("stop");
    }
    renderWsMessages(); renderWsTrace();
    const last = c.messages.length - 1;
    let answer = "";
    const urls = {
      agent: `/api/platform/agents/${c.id}/run`,
      expert: `/api/platform/experts/${c.id}/run`,
      team: `/api/platform/teams/${c.id}/run`,
      workflow: `/api/platform/workflows/${c.id}/run`,
      excel: "/api/platform/excel/ask",
      external: "/api/platform/external/run",
    };
    const isExcel = c.kind === "excel";
    const history = isExcel ? c.messages.filter((m) => m.text)
      .slice(-6).map((m) => ({ q: m.role === "user" ? m.text : "",
                               a: m.role === "assistant" ? m.text : "" })) : [];
    const body = c.kind === "external"
      ? { remote_kind: c.remoteKind || "agent", name: c.name,
          message: text, session_id: c.sessionId || "" }
      : isExcel
        ? { file_id: c.id, question: text, history,
            kb_ids: c.kbIds || [] }
        : isFlow
          ? { input: text, inputs, file_ids: fileIds,
              kb_ids: c.kbIds || [] }
          : { message: text, file_ids: fileIds, session_id: c.sessionId || "",
              skill_ids: c.skillIds || [], tool_ids: c.toolIds || [],
              kb_ids: c.kbIds || [] };
    try {
      await stream(urls[c.kind], body, (ev) => {
        if (ev.type === "run_start") {
          if (ev.session_id) c.sessionId = ev.session_id;
          c.runId = ev.run_id || "";
          const run = el("kp-ws-run");
          if (run) run.textContent = c.runId;
          if (isFlow && c.workflow) {
            const start = arr(c.workflow.nodes).find((n) => n.type === "start");
            c.activeNode = start ? start.id : "";
            renderWsFlow();
          }
          if (isFlow || isTeam) {
            document.querySelector("#kp-ws")?.classList.add("trace-open");
          }
        } else if (ev.type === "delta") {
          answer += ev.text || "";
          const node = el(`kp-ws-last-${last}`);
          if (node) node.textContent = answer;
          const box = el("kp-ws-msgs");
          if (box) box.scrollTop = box.scrollHeight;
        } else if (ev.type === "stage") {
          c.steps.push({ name: STAGE_LABEL[ev.name] || ev.name,
            status: "done", detail: ev.detail || "" });
          renderWsTrace();
        } else if (ev.type === "result" && isExcel) {
          c.messages[last] = { role: "assistant", text: ev.summary || "",
            result: ev.result, chart: ev.chart, code: ev.code,
            citations: ev.citations || [] };
          renderWsMessages();
          const side = el("kp-ws-side");
          if (side && ev.code) {
            side.insertAdjacentHTML("beforeend",
              `<details class="kp-ws-artifact"><summary>生成的 Python 代码</summary>
               <div class="kp-pre">${E(ev.code)}</div></details>`);
          }
        } else if (ev.type === "result") {
          answer = ev.output || answer;
          c.messages[last].text = answer;
          renderWsMessages();
          loadWsArtifacts();
        } else if (ev.type === "attachment") {
          c.steps.push({ name: "附件 · " + arr(ev.names).join("、"),
            status: "done", detail: "已注入上下文" });
          renderWsTrace();
        } else if (ev.type === "node") {
          c.activeNode = ev.node || "";
          if (isFlow) renderWsFlow();
          c.steps.push({ name: ev.name || ev.node, status: "done",
            detail: (ev.status || "") });
          renderWsTrace();
        } else if (ev.type === "step") {
          c.steps.push({ name: ev.name || ev.node, status: "done",
            detail: (ev.status || "") });
          renderWsTrace();
        } else if (ev.type === "handoff") {
          c.turns.push({ name: (ev.agent || {}).name || "",
            role: ev.role || "", reason: ev.reason || "",
            output: ev.output || "" });
          if (isTeam) renderTeamTimeline();
          c.steps.push({ name: "交接 · " + ((ev.agent || {}).name || ""),
            status: "done", detail: String(ev.output || "").slice(0, 80) });
          renderWsTrace();
        } else if (ev.type === "tool_call") {
          c.steps.push({ name: "工具 · " + ev.name, status: "done",
            detail: JSON.stringify(ev.arguments || {}).slice(0, 100) });
          renderWsTrace();
        } else if (ev.type === "approval") {
          c.activeNode = ev.node || "";
          if (isFlow) renderWsFlow();
          c.steps.push({ name: "等待审批", status: "", detail: ev.message });
          renderWsTrace();
          wsApproval(ev, last);
        } else if (ev.type === "error") {
          c.messages[last].text = "错误：" + (ev.message || "");
          c.steps.push({ name: "运行失败", status: "error", detail: ev.message });
          renderWsMessages(); renderWsTrace();
        } else if (ev.type === "done") {
          c.running = false;
          restoreSend();
        }
      });
    } catch (e) {
      c.messages[last].text = "错误：" + e.message;
      renderWsMessages();
    } finally {
      c.running = false;
      restoreSend();
    }
  }

  function restoreSend() {
    const btn = el("kp-ws-send");
    if (!btn) return;
    const c = chatState();
    const isFlow = c.kind === "workflow";
    const isExcel = c.kind === "excel";
    const isExternal = c.kind === "external";
    const isTeam = c.kind === "team";
    btn.disabled = false;
    btn.classList.remove("stop");
    btn.classList.toggle("icon-only", !isFlow && !isExcel);
    btn.innerHTML = isFlow ? "运行" : isExcel ? "问数" :
      `<svg width="15" height="15" viewBox="0 0 24 24" fill="none">
        <path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" stroke-width="2.2"
          stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    btn.onclick = wsSend;
  }

  async function wsStop() {
    const c = chatState();
    if (!c.runId) return;
    try { await api(`/api/platform/runs/${c.runId}/cancel`, { method: "POST" }); }
    catch (e) {}
    c.running = false;
    restoreSend();
    toast("已请求停止", "ok");
  }

  function wsRegenerate(index) {
    const c = chatState();
    let text = "";
    for (let i = index - 1; i >= 0; i -= 1) {
      if (c.messages[i].role === "user") { text = c.messages[i].text; break; }
    }
    if (!text) return;
    if (c.kind === "workflow") { return toast("工作流请修改表单后重新运行", "err"); }
    if (c.kind === "excel") {
      const input = el("kp-ws-input");
      input.value = text;
      return wsSend();
    }
    const input = el("kp-ws-input");
    input.value = text;
    wsSend();
  }

  function wsApproval(ev, last) {
    const side = el("kp-ws-side");
    if (!side) return;
    const ws = el("kp-ws");
    if (ws) ws.classList.add("trace-open");
    const card = document.createElement("div");
    card.className = "approval";
    card.innerHTML = `<div>${E(ev.message || "是否继续？")}</div>
      <div class="btns"><button class="btn sm primary">通过并继续</button>
      <button class="btn sm">驳回</button></div>`;
    card.querySelector(".primary").onclick = async () => {
      card.remove();
      try {
        const resp = await fetch(`/api/platform/runs/${ev.run_id}/approve`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved: true }) });
        const c = chatState();
        c.messages.push({ role: "assistant", text: "" });
        const idx = c.messages.length - 1;
        let answer = "";
        await streamResponse(resp, (e2) => {
          if (e2.type === "delta") {
            answer += e2.text || "";
            const node = el(`kp-ws-last-${idx}`);
            if (node) node.textContent = answer;
          } else if (e2.type === "result") {
            answer = e2.output || answer;
            c.messages[idx].text = answer;
            renderWsMessages();
            loadWsArtifacts();
          } else if (e2.type === "error") {
            c.messages[idx].text = "错误：" + e2.message;
            renderWsMessages();
          }
        });
      } catch (e) { toast(e.message, "err"); }
    };
    card.querySelectorAll("button")[1].onclick = async () => {
      card.remove();
      await fetch(`/api/platform/runs/${ev.run_id}/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: false }) });
      toast("已驳回", "ok");
    };
    side.appendChild(card);
  }

  async function streamResponse(resp, onEvent) {
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        chunk.split("\n").filter((l) => l.startsWith("data: ")).forEach((l) => {
          try { onEvent(JSON.parse(l.slice(6))); } catch (e) {}
        });
      }
    }
  }

  async function previewArtifact(id, name) {
    try {
      const resp = await fetch(`/api/platform/artifacts/${id}/download`);
      const text = await resp.text();
      openModal("产物 · " + name, `<div class="kp-pre" style="max-height:58vh;overflow:auto">${E(text.slice(0, 20000))}</div>`,
        `<button class="btn" onclick="closeModal()">关闭</button>
         <a class="btn" href="/artifact/${id}" target="_blank" rel="noopener">新页面打开</a>
         <a class="btn primary" href="/api/platform/artifacts/${id}/download">下载</a>`);
    } catch (e) { toast(e.message, "err"); }
  }

  async function loadWsArtifacts() {
    const c = chatState();
    if (!c.runId) return;
    try {
      const d = await api(`/api/platform/runs/${c.runId}`);
      const box = el("kp-ws-artifacts");
      if (!box) return;
      box.innerHTML = arr(d.artifacts).map((a) =>
        `<div class="kp-ws-artifact">📄 <b>${E(a.name)}</b><br>
          <button class="btn sm ghost" onclick="KP.previewArtifact('${a.id}','${E(a.name)}')">预览</button>
          <a class="btn sm ghost" href="/api/platform/artifacts/${a.id}/download">下载</a></div>`).join("");
    } catch (e) {}
  }

  /* ══════════════════════════ dispatch ══════════════════════════ */

  function render(view) {
    if (view === "agents") return renderAgents();
    if (view === "skills") return renderSkills();
    if (view === "experts") return renderExperts();
    if (view === "teams") return renderTeams();
    if (view === "workflows") return renderWorkflows();
    if (view === "automation") return renderAutomation();
    if (view === "ops") return renderOps();
    if (view === "designer") return renderDesigner();
    if (view === "kb2") return renderKbs();
    if (view === "models") return renderModels();
  }

  function rerender() {
    if (typeof switchView === "function" && typeof state !== "undefined" && state.view) {
      switchView(state.view);
    }
  }

  function closeMobileSidebar() {
    document.body.classList.remove("side-open");
    el("kp-side-mask")?.remove();
  }

  function toggleSidebar() {
    const side = document.querySelector("#sidebar");
    if (!side) return;
    if (innerWidth <= 800) {
      const open = !document.body.classList.contains("side-open");
      document.body.classList.toggle("side-open", open);
      let mask = el("kp-side-mask");
      if (open && !mask) {
        mask = document.createElement("div");
        mask.id = "kp-side-mask";
        mask.className = "kp-side-mask";
        mask.onclick = closeMobileSidebar;
        document.body.appendChild(mask);
      } else if (!open) closeMobileSidebar();
      return;
    }
    side.classList.toggle("collapsed");
    localStorage.setItem("knowlens-side",
      side.classList.contains("collapsed") ? "1" : "0");
  }

  function init() {
    initPalette();
    bindSlash();
    const side = document.querySelector("#sidebar");
    if (side && localStorage.getItem("knowlens-side") === "1") {
      side.classList.add("collapsed");
    }
    const sideBtn = el("btn-side");
    if (sideBtn) sideBtn.onclick = toggleSidebar;
    document.addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "b") {
        ev.preventDefault();
        toggleSidebar();
      }
      const tag = (ev.target && ev.target.tagName || "").toLowerCase();
      if (ev.key === "/" && !["input", "textarea"].includes(tag)
          && !(ev.target && ev.target.isContentEditable)) {
        const box = el("kp-ws-input") || el("q-input");
        if (box) { ev.preventDefault(); box.focus(); }
      }
    });
    document.querySelectorAll("#nav .nav-item").forEach((b) => {
      b.addEventListener("click", () => {
        if (innerWidth <= 800) closeMobileSidebar();
      });
    });
    const sidebar = document.querySelector("#sidebar");
    if (sidebar && !el("kp-quick")) {
      const sec = document.createElement("div");
      sec.className = "side-sec kp-sec-toggle";
      sec.innerHTML = `<span>最近使用</span><span class="kp-sec-caret">⌄</span>`;
      const box = document.createElement("div");
      box.id = "kp-quick";
      const collapsed = localStorage.getItem("knowlens-recent") === "1";
      if (collapsed) {
        box.classList.add("collapsed");
        sec.classList.add("collapsed");
      }
      sec.onclick = () => {
        box.classList.toggle("collapsed");
        sec.classList.toggle("collapsed", box.classList.contains("collapsed"));
        localStorage.setItem("knowlens-recent",
          box.classList.contains("collapsed") ? "1" : "0");
      };
      const nav = sidebar.querySelector("#nav");
      const spaceBtn = sidebar.querySelector("#btn-new-space");
      if (spaceBtn) {
        spaceBtn.after(sec);
        sec.after(box);
      } else if (nav) {
        nav.after(sec);
        sec.after(box);
      }
    }
    loadQuickTargets();
    const guideButton = el("btn-guide");
    if (guideButton) guideButton.onclick = openGuide;
  }

  return {
    VIEWS, render, init, rerender, openHit, openGuide, guideGo,
    agentForm, agentTemplates, useAgentTemplate, agentDetail, agentTest, runAgentTest, openAgentChat, sendAgentChat,
    agentAction, agentRollback, agentDelete, openAgentEvals, createEval, runEval,
    skillTab, skillCustom, skillImport, installSkill, catalogDetail, catMore,
    skillDetail, viewSkillFile, skillToggle, skillRun, doSkillRun, saveSkill, doImport,
    refreshCatalog, packForm, savePack, deletePack, applyPack, doApplyPack,
    mcpMore, mcpInstall, mcpTools, doMcpInstall, mcpManual,
    mcpToolsManual, doMcpManualInstall,
    expertForm, saveExpert, expertDetail, expertChat, sendExpert, rateExpert,
    teamForm, teamTemplates, useTeamTemplate, saveTeam, teamRun, doTeamRun, teamDelete,
    renderKbs, openKb, kbCreateForm, saveKb, kbUpload,
    kbSearch, kbChunks, kbDocSettings, kbSaveDoc, kbDocVersions,
    kbUploadFolder, uploadFiles, uploadTab, uploadStart,
    kbReparse, kbDeleteDoc, kbEnrich, kbSettings, kbSaveSettings, kbTab,
    kbPreviewChunking, kbDelete,
    kbEditChunk, kbSaveChunk, kbToggleChunk, kbChunkHistory, kbRevertChunk,
    kbRestoreEntry, kbPurgeEntry, kbManualForm, kbManualSave, kbUrlForm,
    kbUrlSave, kbFaqForm, kbFaqSave, kbFaqDelete, kbFaqImport, kbFaqDoImport,
    kbFaqExport, renderModels, modelTab, modelForm, modelSave, modelTest,
    modelRun, copyDebug,
    modelDelete,
    explainWorkflow, explainTeam, startDesigner, renderDesigner,
    sendDesigner, saveDesignerDraft, aiEditWorkflow, aiEditAgent, aiEditTeam,
    aiDesign, runAiDesign, saveAiDraft,
    workflowForm, saveWorkflow, exportWorkflow, importWorkflow, workflowRun, doWorkflowRun, workflowTemplates,
    useTemplate, workflowDelete, validateWorkflow,
    autoTab, scheduleForm, saveSchedule, runScheduleNow, scheduleToggle,
    scheduleRuns, opsTab, review,
    userForm, saveUser, roleForm, saveRole, disableUser, runDetail,
    exportData, retention, mountChatTargets, setChatKind, openTargetPicker,
    useTarget, wsUpload, wsRemoveFile, wsSend, excelUse, excelUploadDialog,
    loadQuickTargets, toggleSidebar, toggleFav, quickOpen, copyMessage,
    openSkillPicker, saveSkillPicker, openToolPicker, saveToolPicker,
    currentSkillIds, currentKBIds, openKBPicker, saveKBPicker,
    startNewChat, exportConversation,
    shareConversation,
    copyShareLink, doCreateShare, compareVersion, deleteShare, paintShares,
    previewArtifact, wsStop, wsRegenerate,
    excelAskSuggestion, openExcelPicker, openTargetHub, closeTargetHub,
    openAttachMenu, openKbPlusMenu,
    hubUse, toggleTrace, readNotification, readAllNotifications,
  };
})();

// app.js boots before this file is parsed, so initialise the palette here.
window.addEventListener("load", () => {
  if (window.KP) window.KP.init();
});
