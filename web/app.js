/* dept-kb-agent 前端 SPA（原生 JS）
   v2 布局对齐豆包 / knowledge-hub：输入卡整合控件、文档详情右侧抽屉、
   库详情右下 FAB 迷你问答窗。 */
const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => [...(el || document).querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  spaces: [], space: null, view: "dashboard", lib: null,
  libs: [], docs: [], tags: [],
  chatMode: "kb", sessionId: null, running: false,
  scopeLibs: [], scopeTags: [], adhocFiles: [], refLibs: false,
  histCursor: 0,
  drawer: { did: null, doc: null, tree: null, page: 1, mode: "page", timer: null },
  mini: {
    open: false, libId: null, sessions: {}, running: false,
    scope: { type: "library", docId: null, dbId: null, docName: "",
             page: 0, selection: "", before: "", after: "" },
  },
};

/* ── 基础工具 ── */
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.status + " " + r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}
const jpost = (p, b) => api(p, { method: "POST",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) });

function toast(msg, cls = "") {
  const t = document.createElement("div");
  t.className = "toast " + cls; t.textContent = msg;
  $("#toast-box").appendChild(t);
  setTimeout(() => t.remove(), 4200);
}

let _modalTrigger = null;
function openModal(title, bodyHTML, footHTML) {
  _modalTrigger = document.activeElement;
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = bodyHTML;
  $("#modal-foot").innerHTML = footHTML || "";
  $("#modal-back").classList.add("show");
}
function closeModal() {
  $("#modal-back").classList.remove("show");
  if (_modalTrigger && _modalTrigger.focus) {
    try { _modalTrigger.focus(); } catch (e) {}
  }
  _modalTrigger = null;
}
$("#modal-back").addEventListener("click", e => { if (e.target.id === "modal-back") closeModal(); });

function md2html(text) {
  marked.setOptions({ breaks: true });
  let t = String(text || "");
  const cites = [];
  t = t.replace(/<cite\s+doc="([^"]*)"\s+page="([^"]*)"[^>]*\/?>/g,
    (m, d, p) => { cites.push([d, p]); return `<sup class="cite">[${cites.length}]</sup>`; });
  return { html: marked.parse(t), cites };
}

/* 答案分块渲染：把【结论】【依据】【方法或改进】【缺失】渲染成独立色块卡片 */
const ANS_META = {
  "结论": { icon: "💡", cls: "conclusion", label: "结论" },
  "依据": { icon: "📌", cls: "basis", label: "依据" },
  "方法或改进": { icon: "🛠️", cls: "method", label: "方法与改进" },
  "方法": { icon: "🛠️", cls: "method", label: "方法" },
  "改进建议": { icon: "🛠️", cls: "method", label: "改进建议" },
  "缺失": { icon: "📎", cls: "missing", label: "还缺什么资料" },
};

function resetVerdictMarker(t) {
  return String(t || "").replace(/【判定[:：][^】]*】/g, "");
}

function renderAnswerHTML(text, verdict, compact) {
  let t = resetVerdictMarker(text);
  const re = /【(结论|依据|方法或改进|方法|改进建议|缺失)】/g;
  const parts = [];
  let m, last = 0, lastKey = null;
  while ((m = re.exec(t)) !== null) {
    const body = t.slice(last, m.index).trim();
    if (lastKey) parts.push({ key: lastKey, body });
    else if (body) parts.push({ key: "", body });
    lastKey = m[1]; last = re.lastIndex;
  }
  const tail = t.slice(last).trim();
  if (lastKey) parts.push({ key: lastKey, body: tail });
  else parts.push({ key: "", body: tail });
  if (!parts.some(p => p.key)) {
    return `<div class="ans-card">${md2html(t).html}</div>`;
  }
  return parts.filter(p => p.body || p.key).map(p => {
    if (!p.key) {
      return `<div class="ans-card">${md2html(p.body).html}</div>`;
    }
    const mm = ANS_META[p.key];
    let chip = "";
    if (p.key === "结论" && verdict) chip = verdictBadge(verdict);
    if (mm.cls === "method" && /推断|非文档原文/.test(p.body))
      chip = `<span class="ans-chip warn">含模型推断 · 非文档原文</span>`;
    return `<section class="ans-sec ans-${mm.cls}${compact ? " compact" : ""}">
      <div class="ans-head"><span class="ans-ico">${mm.icon}</span>
        <span class="ans-label">${mm.label}</span>${chip}</div>
      <div class="md">${md2html(p.body).html}</div></section>`;
  }).join("");
}

function encArg(s) {
  return encodeURIComponent(String(s || "")).replace(/'/g, "%27");
}

function renderProvenance(prov, compact) {
  if (!prov || !prov.length) return "";
  const meta = {
    direct: { cls: "ok", label: "原文直接给出" },
    derived: { cls: "warn", label: "由资料计算" },
    not_found: { cls: "gray", label: "未找到" },
  };
  return `<div class="prov">
    <div class="prov-title">溯源</div>
    ${prov.map(t => {
      const st = meta[t.status] || meta.not_found;
      const srcs = (t.sources || []).map(s => `
        <div class="prov-src">
          <a onclick="jumpToSource('${esc(s.db_id || "")}','${s.page || 0}',
            '${encArg((s.excerpt || "").slice(0, 80))}')">打开原文</a>
          <span>${esc(s.doc_name || "")} · 第 ${s.page || "?"} 页 · ${esc(s.kind || "text")}</span>
          ${s.excerpt ? `<div class="ex">${esc(s.excerpt.slice(0, 160))}</div>` : ""}
        </div>`).join("");
      const d = t.derivation || {};
      const deriv = (d.formula || (d.inputs || []).length) ? `
        <div class="prov-formula">${esc(d.formula || "")}</div>
        <div class="prov-inputs">${(d.inputs || []).map(x =>
          `· ${esc(x.value)} — ${esc(x.source)}`).join("<br>")}</div>` : "";
      const miss = (t.missing || []).length
        ? `<div class="prov-missing">缺失：${t.missing.map(esc).join("；")}</div>` : "";
      return `<div class="prov-target">
        <div class="prov-head"><span class="badge ${st.cls}">${st.label}</span>
          <span class="prov-value">${esc(t.value || "")}</span></div>
        ${srcs}${deriv}${miss}</div>`;
    }).join("")}
  </div>`;
}

async function jumpToSource(dbId, page, enc) {
  const excerpt = decodeURIComponent(enc || "");
  await openDocAtPage(dbId, page, excerpt);
}

async function openDocAtPage(dbId, page, excerpt) {
  if (!dbId) return;
  let d = state.docs.find(x => x.id === dbId);
  if (!d && state.lib) {
    try { state.docs = await api(`/api/libraries/${state.lib}/documents`); }
    catch (e) {}
    d = state.docs.find(x => x.id === dbId);
  }
  if (!d) return toast("未找到该文档，请先打开所属库", "err");
  if (state.drawer.did !== dbId) await openDocDrawer(dbId);
  state.drawer.mode = "page";
  state.drawer.page = Math.max(1, page || 1);
  drawDrawerContent();
  await new Promise(r => setTimeout(r, 250));
  await loadDrawerPage();
  if (excerpt) highlightExcerpt(excerpt);
}

function highlightExcerpt(needle) {
  const body = document.querySelector("#pg-body");
  if (!body || !needle) return;
  const clean = String(needle).replace(/[*#|>`]/g, " ").replace(/\s+/g, " ").trim();
  if (!clean) return;
  const attempts = [clean.slice(0, 60), clean.slice(0, 40),
    clean.slice(0, 20), clean.slice(0, 10)].filter(s => s.length >= 6);
  const full = body.textContent || "";
  let hit = null;
  for (const a of attempts) {
    const idx = full.indexOf(a);
    if (idx >= 0) { hit = { needle: a, idx }; break; }
  }
  if (!hit) { toast("原文页已打开，摘录未精确匹配"); return; }
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let node, pos = 0, found = null;
  while ((node = walker.nextNode())) {
    const len = node.nodeValue.length;
    if (hit.idx < pos + len) { found = { node, off: hit.idx - pos }; break; }
    pos += len;
  }
  if (!found) return;
  const val = found.node.nodeValue;
  const end = Math.min(val.length, found.off + hit.needle.length);
  const mark = document.createElement("mark");
  mark.className = "hl-flash";
  mark.textContent = val.slice(found.off, end);
  const parent = found.node.parentNode;
  parent.insertBefore(document.createTextNode(val.slice(0, found.off)), found.node);
  parent.insertBefore(mark, found.node);
  parent.insertBefore(document.createTextNode(val.slice(end)), found.node);
  parent.removeChild(found.node);
  mark.scrollIntoView({ block: "center", behavior: "smooth" });
  setTimeout(() => {
    if (mark.parentNode) mark.replaceWith(document.createTextNode(mark.textContent));
  }, 5200);
}

const KIND_ICON = { pdf: "📄", docx: "📃", pptx: "📊", xlsx: "📈", xls: "📈",
  md: "📝", txt: "📝", audio: "🎙️", image: "🖼️", unknown: "📎" };
const VERDICT_CLS = { "能": "ok", "不能": "err", "有条件": "warn",
  "未覆盖": "gray", "已回答": "info", "陈述": "info", "操作": "info", "对比": "info" };
// 可行性判定用"判定：X"；陈述/操作/对比类不套"能不能"，显示中性类型标签。
const FEASIBILITY = new Set(["能", "不能", "有条件", "未覆盖"]);
function verdictBadge(v) {
  if (!v) return "";
  const cls = VERDICT_CLS[v] || "info";
  if (FEASIBILITY.has(v)) return `<span class="badge ${cls} verdict-tag">判定：${esc(v)}</span>`;
  if (v === "已回答") return `<span class="badge ${cls} verdict-tag">临时问答</span>`;
  return `<span class="badge ${cls} verdict-tag">类型：${esc(v)}</span>`;
}

/* ── 启动 ── */
async function boot() {
  const saved = localStorage.getItem("dkb-theme") || "light";
  document.documentElement.dataset.theme = saved;
  $("#btn-theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("dkb-theme", next);
    // Re-render the active view so charts and computed colors follow the theme.
    if (state.view === "settings") renderSettings();
    else if (state.view !== "chat" && state.view !== "kb") switchView(state.view);
  };
  $$("#nav .nav-item").forEach(b => b.onclick = () => switchView(b.dataset.view));
  $("#btn-refresh").onclick = () => refreshAll();
  $("#btn-new-space").onclick = newSpaceDialog;
  updateClock();
  setInterval(updateClock, 1000);
  window.addEventListener("resize", () => {
    (state._charts || []).forEach(c => { try { c.resize(); } catch (e) {} });
  });
  $("#btn-new-chat").onclick = () => {
    state.sessionId = null;
    switchView("chat");
    renderChat();
  };
  $("#space-select").onchange = e => { state.space = e.target.value; state.sessionId = null;
    saveScope(); refreshAll(); };
  $("#drawer-mask").onclick = closeDrawer;
  $("#dw-close").onclick = closeDrawer;
  initDrawerResizer();
  if (window.KP) KP.init();
  initMiniChat();
  initSelectionToolbar();
  try {
    const st = await api("/api/status");
    $("#model-info").textContent = `${st.channel} · ${st.model}` + (st.ffmpeg ? "" : " · 无ffmpeg");
    $("#model-dot").className = "dot" + (st.channel ? "" : " bad");
  } catch (e) { $("#model-info").textContent = "后端未就绪"; $("#model-dot").className = "dot bad"; }
  await loadSpaces();
  await refreshAll();
}

async function loadSpaces() {
  state.spaces = await api("/api/spaces");
  const sel = $("#space-select");
  sel.innerHTML = state.spaces.map(s =>
    `<option value="${s.id}">${esc(s.name)}</option>`).join("") ||
    `<option value="">（尚无空间）</option>`;
  if (state.spaces.length) {
    if (!state.spaces.find(s => s.id === state.space)) state.space = state.spaces[0].id;
    sel.value = state.space;
  } else state.space = null;
}

function newSpaceDialog() {
  openModal("新建空间（知识隔离边界）", `
    <div class="field"><label>空间名称（如：数据智能部）</label>
      <input class="input" id="sp-name" placeholder="部门/团队名"></div>
    <div class="field"><label>说明（可选）</label>
      <textarea class="input" id="sp-desc" rows="2" placeholder="这个空间覆盖的业务范围"></textarea></div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" onclick="doNewSpace()">创建</button>`);
}
async function doNewSpace() {
  const name = $("#sp-name").value.trim();
  if (!name) return toast("空间名必填", "err");
  const r = await jpost("/api/spaces", { name, description: $("#sp-desc").value.trim() });
  closeModal(); await loadSpaces(); state.space = r.id; $("#space-select").value = r.id;
  toast("空间已创建", "ok"); refreshAll();
}

const VIEW_META = {
  dashboard: { title: "驾驶舱", sub: "空间总览与知识健康" },
  kb: { title: "知识空间", sub: "空间、库与文档" },
  kb2: { title: "知识库", sub: "解析、分块、检索与治理" },
  models: { title: "模型配置", sub: "LLM / 嵌入 / 重排 / 视觉 / 语音" },
  chat: { title: "智能问答", sub: "基于资料回答，逐句带出处" },
  agents: { title: "智能体", sub: "可配置、可测试、可发布的智能体" },
  skills: { title: "技能市场", sub: "1100+ 开源技能与自定义技能" },
  experts: { title: "专家广场", sub: "把智能体包装成可咨询的专家" },
  teams: { title: "团队协作", sub: "多智能体圆桌、调度与交接" },
  workflows: { title: "工作流", sub: "可视化编排、审批与运行追踪" },
  automation: { title: "自动化", sub: "定时任务与运行产物" },
  designer: { title: "AI 创建", sub: "和 AI 对话，实时看到草稿变化" },
  ops: { title: "运营后台", sub: "概览、审核、审计与权限" },
  evolve: { title: "经验中心", sub: "候选、规则与发行版" },
  settings: { title: "设置", sub: "模型、存储与外观" },
};

function switchView(v) {
  if (v !== "evolve") clearInterval(state._distillTimer);
  disposeCharts();
  state.view = v;
  $$("#nav .nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  const meta = VIEW_META[v] || VIEW_META.dashboard;
  $("#view-title").textContent = meta.title;
  $("#view-sub").textContent = meta.sub;
  $("#view").className = "view" + (v === "chat" ? " no-pad" : "");
  $("#view").innerHTML = "";
  $("#topbar-actions").innerHTML = "";
  $("#kb-fab").classList.toggle("show", false);
  if (v === "dashboard") renderDashboard();
  if (v === "kb") renderKB();
  if (v === "chat") renderChat();
  if (v === "evolve") renderEvolve();
  if (v === "settings") renderSettings();
  if (window.KP && KP.VIEWS.includes(v)) KP.render(v);
  loadHistory();
}

function updateClock() {
  const d = new Date();
  const w = "日一二三四五六"[d.getDay()];
  const pad = n => String(n).padStart(2, "0");
  const el = $("#clock");
  if (el) el.innerHTML =
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `<small>${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} 周${w}</small>`;
}

const CHART_PALETTE = ["#2f7df6", "#19c5d6", "#7a5cff", "#e97b2f",
  "#1fbc7a", "#d65b9e", "#5b8def", "#9b7bff"];
const TYPE_LABEL = { pdf: "PDF", docx: "Word", pptx: "PPT", xlsx: "Excel",
  xls: "Excel", md: "Markdown", txt: "文本", image: "图片", audio: "录音",
  unknown: "其他" };

function disposeCharts() {
  (state._charts || []).forEach(c => { try { c.dispose(); } catch (e) {} });
  state._charts = [];
}
function initChart(id, option) {
  const el = document.getElementById(id);
  if (!el || !window.echarts) return null;
  const c = echarts.init(el);
  c.setOption(option);
  state._charts.push(c);
  return c;
}
function cssVar(n) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}
function fmtNum(n) { return (n ?? 0).toLocaleString("zh-CN"); }
function animateNum(el, to, suffix = "") {
  if (!el) return;
  const start = performance.now(), dur = 900;
  const step = now => {
    const p = Math.min(1, (now - start) / dur);
    const k = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(to * k).toLocaleString("zh-CN") + suffix;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

async function refreshAll() {
  if (!state.space) return;
  await loadSpaceData();
  switchView(state.view);
}

async function loadSpaceData() {
  [state.libs, state.tags] = await Promise.all([
    api(`/api/spaces/${state.space}/libraries`),
    api(`/api/spaces/${state.space}/tags`)]);
  if (state.lib && !state.libs.find(l => l.id === state.lib)) state.lib = null;
  const sp = state.spaces.find(s => s.id === state.space);
  if (sp) $("#space-status").textContent =
    `${sp.lib_count} 个库 · ${sp.doc_count} 篇就绪`;
  loadScope();
}

/* ══════════ 驾驶舱 ══════════ */
async function renderDashboard() {
  disposeCharts();
  const v = $("#view");
  if (!state.space) {
    v.innerHTML = `<div class="empty"><div class="big">🗂️</div>
      还没有空间。先创建一个空间，再上传资料。</div>
      <div style="text-align:center"><button class="btn primary" onclick="newSpaceDialog()">＋ 新建空间</button></div>`;
    return;
  }
  const sp = state.spaces.find(s => s.id === state.space);
  $("#topbar-actions").innerHTML =
    `<button class="btn" onclick="switchView('kb')">上传资料</button>
     <button class="btn primary" onclick="switchView('chat')">开始问答</button>`;
  let d;
  try { d = await api(`/api/dashboard?space_id=${state.space}`); }
  catch (e) { v.innerHTML = `<div class="empty">驾驶舱加载失败：${esc(e.message)}</div>`; return; }

  const total = d.docs.total;
  const readyRate = total ? Math.round(d.docs.ready / total * 100) : 0;
  const uncovered = d.qa.uncovered || 0;
  const pending = d.evolution.pending_candidates || 0;
  const now = new Date();
  const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

  const summary = [];
  summary.push(`当前空间共 <span class="hl">${fmtNum(total)}</span> 篇文档，` +
    `就绪率 <span class="hl">${readyRate}%</span>；` +
    `累计问答 <span class="hl">${fmtNum(d.qa.total)}</span> 次，` +
    `其中未覆盖 <span class="hl">${fmtNum(uncovered)}</span> 次；` +
    `待审候选 <span class="hl">${fmtNum(pending)}</span> 条，` +
    `发行版 <span class="hl">v${d.evolution.digest_ver}</span>。`);
  const advice = [];
  if (d.docs.pending) advice.push(`有 ${d.docs.pending} 篇正在解析，完成后即可检索。`);
  if (d.docs.failed) advice.push(`${d.docs.failed} 篇解析失败，建议到知识空间查看原因。`);
  if (uncovered) advice.push(`${uncovered} 次问答未在资料中找到答案，建议补充资料或审核候选知识。`);
  if (pending) advice.push(`${pending} 条候选待审核，通过后会影响后续回答口径。`);
  if (!advice.length) advice.push("知识库状态良好，可继续积累资料与问答反馈。");

  v.innerHTML = `<div class="dash">
    <div class="dash-hero reveal">
      <div class="hero-mark">鉴</div>
      <div>
        <h1>知识驾驶舱</h1>
        <div class="hero-sub">空间：${esc(sp?.name || "")} · 数据截至 ${dateStr}</div>
      </div>
      <div class="hero-actions">
        <span class="tb-status"><span class="dot"></span>${d.docs.ready}/${total} 篇就绪</span>
      </div>
    </div>

    <div class="dash-summary reveal">
      <div class="dash-card summary-main">
        <h3>分析摘要</h3>
        <p>${summary.join("")}</p>
        <p style="margin-top:8px">${advice.map(x => "· " + x).join("<br>")}</p>
      </div>
      <div class="dash-card summary-side">
        <div class="side-metric"><div class="k">知识就绪率</div>
          <div class="v">${readyRate}%</div><div class="d">${d.docs.ready}/${total} 篇</div></div>
        <div class="side-metric"><div class="k">图片理解</div>
          <div class="v">${fmtNum(d.docs.images)}</div><div class="d">处</div></div>
        <div class="side-metric"><div class="k">待审候选</div>
          <div class="v">${fmtNum(pending)}</div><div class="d">条 · 规则 ${d.evolution.rules}</div></div>
      </div>
    </div>

    <div class="kpi-grid">
      ${kpiCard("文档总数", total, "篇", `就绪 ${d.docs.ready} · 排队 ${d.docs.pending}`)}
      ${kpiCard("知识就绪率", readyRate, "%", d.docs.failed ? `${d.docs.failed} 篇失败` : "全部正常")}
      ${kpiCard("图片理解", d.docs.images, "处", d.docs.images ? "参与检索与引用" : "暂无图片")}
      ${kpiCard("问答次数", d.qa.total, "次", `满意 ${d.qa.feedback_ok} · 纠正 ${d.qa.feedback_fix}`)}
      ${kpiCard("待审候选", pending, "条", `已生效规则 ${d.evolution.rules} 条`)}
      ${kpiCard("发行版", d.evolution.digest_ver, "", `v${d.evolution.digest_ver} · ${d.evolution.digest_bytes}B`, "v")}
    </div>

    <div class="dash-row reveal">
      <div class="dash-card">
        <div class="dc-head"><h3>问答与入库趋势</h3><span class="spacer"></span>
          <span class="muted">近 14 天</span></div>
        <div id="chart-trend" class="chart"></div>
      </div>
      <div class="dash-card">
        <div class="dc-head"><h3>回答判定分布</h3></div>
        <div id="chart-verdict" class="chart"></div>
      </div>
    </div>

    <div class="dash-row equal reveal">
      <div class="dash-card">
        <div class="dc-head"><h3>文档类型</h3></div>
        <div id="chart-type" class="chart sm"></div>
      </div>
      <div class="dash-card">
        <div class="dc-head"><h3>库健康</h3><span class="spacer"></span>
          <span class="muted">${d.libraries.length} 个库</span></div>
        ${d.libraries.length ? d.libraries.map(l => healthRow(l)).join("")
          : '<div class="empty" style="padding:20px">还没有库</div>'}
      </div>
    </div>

    <div class="dash-row equal reveal">
      <div class="dash-card">
        <div class="dc-head"><h3>风险与待办</h3></div>
        <div class="risk-list">${d.risks.length ? d.risks.map(r =>
          `<div class="risk"><span class="ic">${r.level === "error" ? "⛔" : r.level === "warn" ? "⚠️" : "💡"}</span>
            <span>${esc(r.text)}</span></div>`).join("")
          : '<div class="muted" style="padding:8px 0">暂无风险，状态良好。</div>'}</div>
      </div>
      <div class="dash-card">
        <div class="dc-head"><h3>最近会话</h3><span class="spacer"></span>
          <button class="btn sm ghost" onclick="switchView('chat')">全部</button></div>
        <div class="mini-list">${d.recent_sessions.length ? d.recent_sessions.map(s =>
          `<div class="mi" style="cursor:pointer" onclick="openSession('${s.id}','${s.mode}')">
            <span>${s.mode === "kb" ? "📚" : "📎"}</span>
            <span class="t">${esc(s.title || "会话")}</span>
            <span class="m">${esc((s.updated_at || "").slice(5, 16))}</span></div>`).join("")
          : '<div class="muted" style="padding:8px 0">还没有问答记录</div>'}</div>
      </div>
    </div>

    <div class="dash-row equal reveal">
      <div class="dash-card">
        <div class="dc-head"><h3>最近入库</h3><span class="spacer"></span>
          <button class="btn sm ghost" onclick="switchView('kb')">知识空间</button></div>
        <div class="mini-list">${d.recent_docs.length ? d.recent_docs.map(x =>
          `<div class="mi" style="cursor:pointer" onclick="openDocFromDashboard('${x.id}','${x.library_id}')">
            <span>${KIND_ICON[x.kind] || "📄"}</span>
            <span class="t">${esc(x.name)}</span>
            <span class="m">${esc((x.created_at || "").slice(5, 16))}</span></div>`).join("")
          : '<div class="muted" style="padding:8px 0">还没有文档</div>'}</div>
      </div>
      <div class="dash-card">
        <div class="dc-head"><h3>模型调用</h3><span class="spacer"></span>
          <span class="muted">全局 · 近 14 天</span></div>
        <div id="chart-usage" class="chart sm"></div>
      </div>
    </div>
  </div>`;

  // KPI 数字动画
  document.querySelectorAll(".kpi .kpi-num[data-to]").forEach(el => {
    animateNum(el, Number(el.dataset.to || 0));
  });
  initDashboardCharts(d);
}

function kpiCard(label, value, unit, desc, prefix = "") {
  return `<div class="kpi">
    <div class="k"><span>${label}</span></div>
    <div class="v">${prefix}<span class="kpi-num" data-to="${value}">0</span><small>${unit}</small></div>
    <div class="d">${esc(desc)}</div>
  </div>`;
}

function healthRow(l) {
  const total = l.total || 1;
  const okPct = l.ready / total * 100;
  const pendPct = l.pending / total * 100;
  const failPct = l.failed / total * 100;
  return `<div class="health-row">
    <span style="width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(l.name)}</span>
    <div class="health-bar">
      <i class="ok" style="width:${okPct}%"></i>
      <i class="pend" style="width:${pendPct}%"></i>
      <i class="fail" style="width:${failPct}%"></i>
    </div>
    <span class="muted" style="width:70px;text-align:right">${l.ready}/${l.total}</span>
  </div>`;
}

function initDashboardCharts(d) {
  const text3 = cssVar("--text-3"), border = cssVar("--border");
  const axisLabel = { color: text3, fontSize: 9 };
  const tooltip = {
    trigger: "axis", backgroundColor: "rgba(10,26,46,.92)",
    borderColor: "rgba(255,255,255,.16)", textStyle: { color: "#fff", fontSize: 11 },
  };
  const days = d.trend.map(x => x.day.slice(5));

  initChart("chart-trend", {
    color: CHART_PALETTE,
    tooltip,
    legend: { data: ["问答", "入库"], right: 0, top: 0,
      textStyle: { color: text3, fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
    grid: { left: 6, right: 6, top: 34, bottom: 2, containLabel: true },
    xAxis: { type: "category", data: days, axisLine: { show: false },
      axisTick: { show: false }, axisLabel },
    yAxis: { type: "value", minInterval: 1,
      splitLine: { lineStyle: { color: border, type: "dashed" } }, axisLabel },
    series: [
      { name: "问答", type: "line", smooth: true, symbol: "circle", symbolSize: 5,
        lineStyle: { width: 3 }, areaStyle: { opacity: .16 },
        data: d.trend.map(x => x.qa) },
      { name: "入库", type: "bar", barWidth: 8,
        itemStyle: { borderRadius: [4, 4, 0, 0], color: "rgba(25,197,214,.55)" },
        data: d.trend.map(x => x.docs) },
    ],
  });

  const vColor = name => ({
    "能": "#1fbc7a", "不能": "#e25563", "有条件": "#e97b2f",
    "未覆盖": "#8ba0b5", "陈述": "#2f7df6", "操作": "#19c5d6",
    "对比": "#7a5cff", "已回答": "#5b8def",
  }[name] || CHART_PALETTE[0]);
  const vData = Object.entries(d.qa.verdicts).map(([name, value]) =>
    ({ name, value, itemStyle: { color: vColor(name) } }));
  initChart("chart-verdict", {
    tooltip: { ...tooltip, trigger: "item" },
    series: [{
      type: "pie", radius: ["56%", "76%"], center: ["50%", "52%"],
      avoidLabelOverlap: true, label: { show: false },
      emphasis: { label: { show: true, fontSize: 12, fontWeight: 700,
        formatter: "{b}\n{c}" } },
      data: vData.length ? vData : [{ name: "暂无问答", value: 1,
        itemStyle: { color: "rgba(139,160,181,.25)" } }],
    }],
    graphic: [{ type: "text", left: "center", top: "46%",
      style: { text: String(d.qa.total), fill: cssVar("--text-1"),
        fontSize: 24, fontWeight: 800, textAlign: "center" } },
      { type: "text", left: "center", top: "58%",
        style: { text: "问答", fill: text3, fontSize: 10, textAlign: "center" } }],
  });

  const tData = Object.entries(d.docs.by_type).map(([k, val], i) =>
    ({ name: TYPE_LABEL[k] || k, value: val,
      itemStyle: { color: CHART_PALETTE[i % CHART_PALETTE.length] } }));
  initChart("chart-type", {
    tooltip: { ...tooltip, trigger: "item" },
    legend: { bottom: 0, textStyle: { color: text3, fontSize: 10 },
      itemWidth: 10, itemHeight: 8 },
    series: [{
      type: "pie", radius: ["48%", "70%"], center: ["50%", "44%"],
      label: { show: false }, data: tData.length ? tData :
        [{ name: "暂无文档", value: 1, itemStyle: { color: "rgba(139,160,181,.25)" } }],
    }],
  });

  initChart("chart-usage", {
    color: [CHART_PALETTE[0]], tooltip,
    grid: { left: 6, right: 6, top: 18, bottom: 2, containLabel: true },
    xAxis: { type: "category", data: days, axisLine: { show: false },
      axisTick: { show: false }, axisLabel },
    yAxis: { type: "value", splitLine: { lineStyle: { color: border,
      type: "dashed" } }, axisLabel },
    series: [{ type: "line", smooth: true, symbol: "none",
      lineStyle: { width: 2.5 }, areaStyle: { opacity: .18 },
      data: d.usage.trend.map(x => x.calls) }],
  });
}

function openDocFromDashboard(docId, libId) {
  state.lib = libId;
  switchView("kb");
  setTimeout(async () => {
    await renderLibDetail();
    openDocDrawer(docId);
  }, 60);
}

/* ══════════ 设置 ══════════ */
async function renderSettings() {
  disposeCharts();
  const v = $("#view");
  $("#topbar-actions").innerHTML = "";
  let st = {};
  try { st = await api("/api/status"); } catch (e) {}
  const theme = document.documentElement.dataset.theme;
  v.innerHTML = `<div class="settings-grid">
    <div class="dash-card reveal">
      <div class="dc-head"><h3>模型与端点</h3></div>
      <div class="setting-row"><span class="k">当前通道</span>
        <span class="v">${esc(st.channel || "-")} · ${esc(st.model || "-")}</span></div>
      <div class="setting-row"><span class="k">协议</span>
        <span class="v">${esc(st.protocol || "-")}</span></div>
      <div class="setting-row"><span class="k">密钥</span>
        <span class="v">来自环境变量或本地 .env，不在界面展示</span></div>
      <div class="help" style="margin-top:8px">修改模型或密钥：编辑项目根目录的 .env 后重启服务。</div>
    </div>
    <div class="dash-card reveal">
      <div class="dc-head"><h3>图片与语音</h3></div>
      <div class="setting-row"><span class="k">图片理解</span>
        <span class="v">上传时可选，默认开启（复用当前视觉模型）</span></div>
      <div class="setting-row"><span class="k">语音转写</span>
        <span class="v">${st.ffmpeg ? "ffmpeg 可用 · faster-whisper / 兼容端点" : "未检测到 ffmpeg"}</span></div>
      <div class="setting-row"><span class="k">OCR 缓存</span>
        <span class="v mono">data/ocr_cache</span></div>
    </div>
    <div class="dash-card reveal">
      <div class="dc-head"><h3>存储与数据</h3></div>
      <div class="setting-row"><span class="k">数据目录</span>
        <span class="v mono">${esc(st.data_dir || "data/")}</span></div>
      <div class="setting-row"><span class="k">数据范围</span>
        <span class="v">知识、索引、问答与演进数据均保存在本机</span></div>
      <div class="setting-row"><span class="k">备份</span>
        <span class="v">停止服务后备份整个数据目录即可</span></div>
    </div>
    <div class="dash-card reveal">
      <div class="dc-head"><h3>语言 / Language</h3></div>
      <div class="setting-row"><span class="k">界面语言</span><span class="v">
        <button class="btn sm ${window.I18N && I18N.current() === "zh" ? "primary" : ""}"
          onclick="I18N.set('zh')">中文</button>
        <button class="btn sm ${window.I18N && I18N.current() === "en" ? "primary" : ""}"
          onclick="I18N.set('en')">English</button></span></div>
      <div class="help" style="margin-top:8px">界面文案集中维护在 web/i18n.js；文档、智能体提示词等业务内容保持原文。</div>
    </div>
    <div class="dash-card reveal">
      <div class="dc-head"><h3>外观</h3></div>
      <div class="setting-row"><span class="k">主题</span><span class="v">
        <button class="btn sm ${theme === "light" ? "primary" : ""}" onclick="setTheme('light')">浅色玻璃</button>
        <button class="btn sm ${theme === "dark" ? "primary" : ""}" onclick="setTheme('dark')">Midnight</button>
      </span></div>
      <div class="help">浅色玻璃为默认主题，深色适合暗光环境。</div>
    </div>
    <div class="dash-card reveal">
      <div class="dc-head"><h3>Claude Code 接入</h3></div>
      <div class="setting-row"><span class="k">经验采集</span>
        <span class="v">可选，默认关闭（仅采集你选择的项目）</span></div>
      <div class="setting-row"><span class="k">发行版注入</span>
        <span class="v">可选，默认关闭（写入项目本地配置，可撤销）</span></div>
      <div class="help">采集与注入在"经验中心 → 开发经验"中配置。</div>
    </div>
    <div class="dash-card reveal">
      <div class="dc-head"><h3>关于</h3></div>
      <div class="setting-row"><span class="k">产品</span><span class="v">知鉴 KnowLens</span></div>
      <div class="setting-row"><span class="k">版本</span>
        <span class="v">v${esc(st.version || "0.1.0")}</span></div>
      <div class="setting-row"><span class="k">许可证</span><span class="v">MIT</span></div>
      <div class="setting-row"><span class="k">宣传语</span>
        <span class="v">有出处的回答，会进化的知识</span></div>
    </div>
  </div>`;
}
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("dkb-theme", t);
  if (state.view === "settings") renderSettings();
  else if (state.view === "dashboard") renderDashboard();
}

/* ══════════ 知识库视图 ══════════ */
async function renderKB() {
  const v = $("#view");
  // 默认收起库详情专属控件，仅 renderLibDetail 分支重新点亮（修复退出库后 FAB 残留）
  $("#kb-fab").classList.remove("show");
  $("#mini-chat").classList.remove("show");
  state.mini.open = false;
  if (!state.space) { v.innerHTML = `<div class="empty"><div class="big">🗂️</div>
    还没有空间。点击左侧「新建空间」开始。</div>`; return; }
  if (!state.libs.length) {
    v.innerHTML = `<div class="empty"><div class="big">📚</div>
      这个空间还没有库。库是空间内的检索隔离单元，可多库联合问答。</div>
      <div style="text-align:center"><button class="btn primary" onclick="newLibDialog()">＋ 新建库</button></div>`;
    return;
  }
  if (!state.lib) {
    $("#topbar-actions").innerHTML =
      `<button class="btn ghost" onclick="switchView('kb2')" title="统一知识库：解析、分块、检索与治理">知识库</button>
       <button class="btn primary" onclick="newLibDialog()">＋ 新建库</button>`;
    v.innerHTML = `<div class="grid" id="lib-grid"></div>`;
    $("#lib-grid").innerHTML = state.libs.map(l => `
      <div class="card clickable" onclick="openLib('${l.id}')">
        <div class="name">📁 ${esc(l.name)}</div>
        <div class="desc">${esc(l.summary || l.description || "（暂无摘要，随上传自动演进）")}</div>
        <div class="meta"><span>📄 ${l.doc_count} 篇</span>
          ${l.pending_count ? `<span style="color:var(--warning)">⏳ ${l.pending_count} 解析中</span>` : ""}
          ${l.failed_count ? `<span style="color:var(--error)">✕ ${l.failed_count} 失败</span>` : ""}
          <span>库内检索隔离</span>
          <span style="flex:1"></span><span onclick="event.stopPropagation();openMiniFor('${l.id}')"
            style="color:var(--brand)">💬 问答</span></div>
      </div>`).join("");
    return;
  }
  await renderLibDetail();
}

async function openLib(lid) {
  state.lib = lid;
  await renderLibDetail();
}

async function renderLibDetail() {
  const lib = state.libs.find(l => l.id === state.lib);
  if (!lib) { state.lib = null; return renderKB(); }
  state.docs = await api(`/api/libraries/${lib.id}/documents`);
  $("#topbar-actions").innerHTML = `
    <button class="btn" onclick="state.lib=null;renderKB()">← 返回库列表</button>
    <button class="btn" onclick="delLib('${lib.id}')" style="color:var(--error)">删除库</button>
    <button class="btn" onclick="urlIngestDialog()">🔗 链接入库</button>
    <button class="btn primary" onclick="uploadDialog('${lib.id}')">⬆ 上传文件</button>`;
  $("#kb-fab").classList.add("show");
  $("#kb-fab").title = `直接问答「${lib.name}」`;
  const v = $("#view");
  v.innerHTML = `
    <div class="flex items-center gap12 mb8">
      <h3 style="font-size:17px">📁 ${esc(lib.name)}</h3>
      <span class="muted">${esc(lib.description || "")}</span>
    </div>
    ${lib.summary ? `<div class="help mb8">库摘要（随上传自动演进）：${esc(lib.summary)}</div>` : ""}
    <div id="index-banner"></div>
    <div id="bulk-bar"></div>
    <div id="doc-rows"></div>`;
  drawDocTable();
  pollProgress();
}

function drawDocTable() {
  state._bulkSel = state._bulkSel || new Set();
  const rows = state.docs.map(d => {
    let st;
    if (d.status === "ready") st = `<span class="badge ok">就绪 ${d.pages}页</span>`;
    else if (d.status === "failed") st = `<span class="badge err" title="${esc(d.error)}">失败</span>
      <span class="muted" style="font-size:11px">${esc((d.error || "").slice(0, 60))}</span>`;
    else st = `<span class="badge warn">${d.status === "indexing" ? "索引中" : "排队"}</span>
      <span class="stage-pill" data-stage="${d.id}"></span>`;
    if (d.image_count) st += ` <span class="badge info">含图 ${d.image_count}</span>`;
    if (d.ocr_status) st += ` <span class="badge warn" title="${esc(d.ocr_status)}">图片部分失败</span>`;
    const tags = (d.tag_names || "").split(",").filter(Boolean)
      .map(t => `<span class="chip" style="font-size:11px">${esc(t)}</span>`).join(" ");
    const acts = [
      d.status === "ready" ? `<button class="btn sm ghost" style="color:var(--brand)" onclick="openDocDrawer('${d.id}')">查看内容</button>` : "",
      `<button class="btn sm ghost" onclick="reindexDoc('${d.id}')">重建</button>`,
      `<button class="btn sm ghost" style="color:var(--error)" onclick="delDoc('${d.id}','${esc(d.name)}')">删除</button>`,
    ].join("");
    const clickable = d.status === "ready";
    state._bulkSel = state._bulkSel || new Set();
    const checked = state._bulkSel.has(d.id) ? "checked" : "";
    return `<tr style="${clickable ? "cursor:pointer" : ""}"
      ${clickable ? `onclick="openDocDrawer('${d.id}')"` : ""}>
      <td onclick="event.stopPropagation()"><input type="checkbox" class="doc-sel"
        data-id="${d.id}" ${checked} onclick="toggleDocSel('${d.id}',this)"></td>
      <td>${KIND_ICON[d.kind] || "📎"} ${esc(d.name)}</td>
      <td>${esc(d.kind)}</td><td>${st}</td><td>${tags}</td>
      <td class="right" onclick="event.stopPropagation()">${acts}</td></tr>`;
  }).join("");
  $("#doc-rows").innerHTML = state.docs.length ? `
    <table class="doc-table"><thead><tr>
      <th style="width:28px"><input type="checkbox" id="doc-sel-all" title="全选"
        onclick="toggleAllDocs(this)"></th>
      <th style="width:32%">文件</th><th>类型</th><th>状态</th><th>标签</th>
      <th class="right">操作</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="help mt8">点击文件行查看内容（结构树 / 逐页浏览）；右下角 💬 直接对本库提问。</div>`
    : `<div class="empty"><div class="big">📭</div>库内暂无文件，点右上角「上传文件」或直接把文件/文件夹拖进来</div>`;
  const ids = new Set((state.docs || []).map(d => d.id));
  (state._bulkSel || new Set()).forEach(id => {
    if (!ids.has(id)) state._bulkSel.delete(id);
  });
  drawBulkBar();
  drawIndexBanner();
}

function toggleDocSel(id, box) {
  state._bulkSel = state._bulkSel || new Set();
  if (box.checked) state._bulkSel.add(id); else state._bulkSel.delete(id);
  drawBulkBar();
}

function toggleAllDocs(box) {
  state._bulkSel = new Set(box.checked ? (state.docs || []).map(d => d.id) : []);
  drawDocTable();
}

function drawBulkBar() {
  const bar = $("#bulk-bar");
  if (!bar) return;
  const n = (state._bulkSel || new Set()).size;
  bar.innerHTML = n ? `<div class="help" style="display:flex;align-items:center;gap:10px">
    <b>已选 ${n} 项</b>
    <button class="btn sm primary" onclick="bulkTagDialog()">批量标签</button>
    <button class="btn sm ghost" onclick="state._bulkSel.clear();drawDocTable()">清除选择</button>
  </div>` : "";
}

function bulkTagDialog() {
  const names = (state.tags || []).map(t => t.name);
  const boxes = (cls) => names.map(t =>
    `<label style="display:inline-flex;gap:5px;align-items:center;margin:3px 8px 3px 0">
      <input type="checkbox" class="${cls}" value="${esc(t)}"> ${esc(t)}
    </label>`).join("");
  openModal("批量标签", `
    <div class="help mb8">已选 ${state._bulkSel.size} 篇文档。勾选要添加或移除的标签。</div>
    <div class="mb8"><b style="font-size:12.5px">添加标签</b><div>${boxes("bt-add") || "暂无已有标签"}</div></div>
    <div class="mb8"><b style="font-size:12.5px">移除标签</b><div>${boxes("bt-remove") || "暂无已有标签"}</div></div>
    <label style="display:block;font-size:12.5px">新标签（逗号分隔）
      <input id="bt-new" class="w100" placeholder="例如：制度, 流程"></label>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" onclick="applyBulkTags()">应用</button>`);
}

async function applyBulkTags() {
  const add = Array.from(document.querySelectorAll(".bt-add:checked")).map(n => n.value);
  const remove = Array.from(document.querySelectorAll(".bt-remove:checked")).map(n => n.value);
  const extra = ($("#bt-new").value || "").split(/[,，;；\s]+/).map(x => x.trim()).filter(Boolean);
  if (!add.length && !remove.length && !extra.length) return toast("至少选择一个标签", "err");
  try {
    const r = await jpost("/api/platform/documents/bulk_tags", {
      document_ids: Array.from(state._bulkSel),
      add: add.concat(extra), remove,
    });
    toast(`已更新 ${r.documents} 篇：新增 ${r.added}，移除 ${r.removed}`, "ok");
    closeModal();
    state._bulkSel.clear();
    state.docs = await api(`/api/libraries/${state.lib}/documents`);
    drawDocTable();
  } catch (e) { toast(e.message, "err"); }
}


function urlIngestDialog() {
  openModal("网页链接入库", `
    <div class="help mb8">抓取网页正文，转成 Markdown 后进入当前库的索引流程。仅支持公开页面。</div>
    <label style="display:block;font-size:12.5px">页面链接
      <input id="ui-url" class="w100" placeholder="https://example.com/article"></label>
    <label style="display:block;font-size:12.5px;margin-top:8px">标签（逗号分隔，可选）
      <input id="ui-tags" class="w100" placeholder="外部资料, 行业"></label>
    <div style="margin-top:10px"><button class="btn sm" onclick="previewUrl()">预览正文</button></div>
    <div id="ui-preview" class="help" style="margin-top:8px;max-height:240px;overflow:auto"></div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" onclick="doUrlIngest()">入库</button>`);
}

async function previewUrl() {
  const url = $("#ui-url").value.trim();
  if (!url) return toast("请填写链接", "err");
  const box = $("#ui-preview");
  box.textContent = "抓取中…";
  try {
    const r = await jpost("/api/platform/ingest/preview", { url });
    box.innerHTML = `<b>${esc(r.title)}</b> · ${r.chars} 字 · ${esc(r.url)}
      <pre class="kp-pre" style="margin-top:6px;white-space:pre-wrap">${esc(r.markdown.slice(0, 3000))}</pre>`;
  } catch (e) { box.textContent = "抓取失败：" + e.message; }
}

async function doUrlIngest() {
  const url = $("#ui-url").value.trim();
  const tags = ($("#ui-tags").value || "").split(/[,，;；\s]+/).filter(Boolean);
  if (!url) return toast("请填写链接", "err");
  try {
    const r = await jpost("/api/platform/ingest/url", {
      space_id: state.space, library_id: state.lib, url, tags,
    });
    toast(`已入库：${r.title}`, "ok");
    closeModal();
    state.docs = await api(`/api/libraries/${state.lib}/documents`);
    drawDocTable();
    pollProgress();
  } catch (e) { toast(e.message, "err"); }
}

function drawIndexBanner() {
  const box = $("#index-banner");
  if (!box) return;
  const docs = state.docs || [];
  if (!docs.length) { box.innerHTML = ""; return; }
  const pending = docs.filter(d => d.status === "pending" || d.status === "indexing");
  const failed = docs.filter(d => d.status === "failed");
  const ready = docs.filter(d => d.status === "ready").length;
  const total = docs.length;
  if (!pending.length) {
    if (state._hadPending) {
      state._hadPending = false;
      box.innerHTML = `<div class="dw-progress" style="border-color:rgba(0,181,120,.35);
        background:rgba(0,181,120,.08);color:var(--success)">
        ✓ 全部处理完成：${ready}/${total} 篇就绪${failed.length ? `，${failed.length} 篇失败（见行内原因，可重建）` : ""}
        —— 现在可以开始提问了。</div>`;
      setTimeout(() => { if ($("#index-banner")) $("#index-banner").innerHTML = ""; }, 8000);
    } else if (failed.length) {
      box.innerHTML = `<div class="dw-progress" style="border-color:rgba(227,77,89,.35);
        background:rgba(227,77,89,.06);color:var(--error)">
        ${failed.length} 篇解析失败，其余 ${ready} 篇已就绪；失败的可在行内点「重建」重试。</div>`;
    } else box.innerHTML = "";
    return;
  }
  state._hadPending = true;
  const cur = [...document.querySelectorAll(".stage-pill")]
    .map(e => e.textContent.trim()).filter(Boolean)[0] || "排队中…";
  const pct = Math.max(4, Math.round(ready / total * 100));
  box.innerHTML = `<div class="dw-progress" style="display:block">
      <div class="flex items-center gap8" style="margin-bottom:6px">
        <span class="pulse"></span>
        <b>正在索引：就绪 ${ready}/${total} 篇</b>
        <span class="muted">· 排队/解析中 ${pending.length} 篇${failed.length ? ` · 失败 ${failed.length} 篇` : ""}</span>
        <span style="flex:1"></span>
        <span class="muted">${esc(cur)}</span>
      </div>
      <div class="prog-bar" style="width:100%"><i style="width:${pct}%"></i></div>
      <div class="help" style="margin-top:5px">已就绪的文档现在就可以提问；本轮全部完成后会提示。</div>
    </div>`;
}

let progressTimer = null;
function pollProgress() {
  clearInterval(progressTimer);
  const pending = state.docs.filter(d => d.status !== "ready" && d.status !== "failed");
  if (!pending.length || state.view !== "kb" || !state.lib) return;
  progressTimer = setInterval(async () => {
    try {
      const r = await api(`/api/events?space_id=${state.space}&since=${state.histCursor}`);
      if (r.events.length) {
        state.histCursor = r.last;
        r.events.filter(e => e.type === "progress").forEach(e => {
          const el = $(`[data-stage="${e.ref}"]`);
          if (el) el.textContent = e.payload.detail;
          if (state.drawer.did === e.ref) updateDrawerProgress(e.payload);
        });
        if (r.events.some(e => ["done", "failed"].includes(e.payload.stage))) {
          const before = (state.docs || []).filter(
            d => d.status !== "ready" && d.status !== "failed").length;
          state.docs = await api(`/api/libraries/${state.lib}/documents`);
          drawDocTable();
          await loadSpaceData();
          const after = state.docs.filter(
            d => d.status !== "ready" && d.status !== "failed").length;
          if (before > 0 && after === 0) {
            toast(`本批索引完成：${state.docs.filter(d => d.status === "ready").length} 篇就绪，可以开始提问`, "ok");
          }
          pollProgress();
        } else {
          drawIndexBanner();
        }
      }
    } catch (err) { /* ignore */ }
  }, 2000);
}

const UPLOAD_EXTS = new Set(["pdf", "docx", "pptx", "xlsx", "xls", "md", "txt",
  "png", "jpg", "jpeg", "webp", "mp3", "wav", "m4a", "mp4", "aac", "flac",
  "ogg", "wma"]);

/* ── 可搜索/可折叠标签选择器（上传、标签编辑、问答过滤共用） ── */
const TAG_LIMIT = 12;
const tagPickers = {};

function mountTagPicker(id, tags, selectedIds, onChange, placeholder) {
  tagPickers[id] = {
    tags: tags.slice(), sel: new Set(selectedIds || []),
    expanded: false, q: "", onChange: onChange || null,
  };
  const host = document.getElementById(id);
  if (!host) return;
  host.innerHTML = `
    <input class="input tag-search" placeholder="${placeholder || "搜索标签…"}"
      oninput="onTagSearch('${id}', this.value)">
    <div class="tag-pick" id="${id}-list"></div>
    <div class="flex items-center">
      <button class="btn sm ghost tag-more" id="${id}-more"
        onclick="toggleTagExpand('${id}')"></button>
      <span class="tag-hint" id="${id}-hint"></span>
    </div>`;
  drawTagPicker(id);
}

function drawTagPicker(id) {
  const p = tagPickers[id];
  const listEl = document.getElementById(id + "-list");
  if (!p || !listEl) return;
  const q = p.q.trim().toLowerCase();
  let list = p.tags.filter(t => !q || t.name.toLowerCase().includes(q));
  list = list.map((t, i) => ({ t, i, on: p.sel.has(t.id) }))
    .sort((a, b) => (b.on - a.on) || (a.i - b.i)).map(x => x.t);
  const shown = (p.expanded || q) ? list : list.slice(0, TAG_LIMIT);
  listEl.innerHTML = shown.length ? shown.map(t => `
    <span class="chip pick-tag ${p.sel.has(t.id) ? "on" : ""}"
      onclick="toggleTagPick('${id}','${t.id}')">${esc(t.name)}</span>`).join("")
    : `<span class="muted">${q ? "没有匹配的标签" : "标签词表为空"}</span>`;
  const more = document.getElementById(id + "-more");
  if (more) {
    if (q || list.length <= TAG_LIMIT) more.style.display = "none";
    else {
      more.style.display = "";
      more.textContent = p.expanded ? "收起"
        : `展开全部（${list.length}）`;
    }
  }
  const hint = document.getElementById(id + "-hint");
  if (hint) hint.textContent = p.sel.size ? `已选 ${p.sel.size} 个` : "";
}

function onTagSearch(id, val) {
  const p = tagPickers[id];
  if (!p) return;
  p.q = val; drawTagPicker(id);
}
function toggleTagExpand(id) {
  const p = tagPickers[id];
  if (!p) return;
  p.expanded = !p.expanded; drawTagPicker(id);
}
function toggleTagPick(id, tid) {
  const p = tagPickers[id];
  if (!p) return;
  if (p.sel.has(tid)) p.sel.delete(tid); else p.sel.add(tid);
  drawTagPicker(id);
  if (p.onChange) p.onChange([...p.sel]);
}
function pickerValues(id) {
  const p = tagPickers[id];
  return p ? [...p.sel] : [];
}

function uploadDialog(lid) {
  state._uploadFiles = [];
  openModal("上传文件到「" + (state.libs.find(l => l.id === lid)?.name || "") + "」", `
    <div class="field"><label>选择文件，或整个文件夹（含子目录）</label>
      <div class="flex gap8 items-center" style="flex-wrap:wrap">
        <button class="btn" onclick="document.getElementById('up-files').click()">📄 选择文件</button>
        <button class="btn" onclick="document.getElementById('up-dir').click()">📁 选择文件夹</button>
        <span class="muted" id="up-count">未选择</span>
      </div>
      <input type="file" id="up-files" multiple style="display:none">
      <input type="file" id="up-dir" webkitdirectory multiple style="display:none">
      <div class="help mt8" id="up-preview"></div>
      <div class="help">支持 .md/.txt/.pdf/.docx/.pptx/.xlsx/图片/录音；不支持的类型自动跳过。
        文件夹会递归收集其中所有支持的文件。</div>
    </div>
    <div class="field">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" id="up-vision" checked>
        启用图片理解（扫描页、图文混排、内嵌图片会调用视觉模型识别）</label>
      <div class="help">关闭则只索引文字，速度快、成本低；扫描版 PDF 将无法检索。</div>
    </div>
    <div class="field"><label>标签（可搜索、可展开；留空则由系统上传后自动建议）</label>
      <div id="up-tag-picker" class="tag-picker"></div>
      <div class="mt8 flex gap8"><input class="input" id="new-tag-name" placeholder="新标签名" style="flex:1">
      <button class="btn" onclick="addTagQuick('${state.space}')">添加</button></div></div>
    <div class="help">上传后管线自动执行：格式解析 → 层级索引树 → 摘要与标签建议 → 库摘要演进；
      多个文件自动排队（同时最多 2 个），已就绪的文档可立即提问。</div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" id="up-go" onclick="doUpload('${lid}')">开始上传</button>`);
  $("#up-files").onchange = e => setUploadFiles([...e.target.files]);
  $("#up-dir").onchange = e => setUploadFiles([...e.target.files]);
  mountTagPicker("up-tag-picker", state.tags, []);
}

function setUploadFiles(files) {
  const ok = [], bad = [];
  for (const f of files) {
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    (UPLOAD_EXTS.has(ext) ? ok : bad).push(f);
  }
  state._uploadFiles = ok;
  const cnt = $("#up-count");
  if (cnt) cnt.textContent = ok.length ? `已选 ${ok.length} 个文件` : "未选择";
  const pv = $("#up-preview");
  if (pv) {
    const names = ok.slice(0, 4).map(f => esc(f.name)).join("、");
    pv.innerHTML = ok.length
      ? `将上传：${names}${ok.length > 4 ? ` 等 ${ok.length} 个` : ""}
         ${ok.length > 100 ? `<br><span style="color:var(--warning)">数量较多，将排队依次索引，耗时较长。</span>` : ""}
         ${bad.length ? `<br><span style="color:var(--warning)">已跳过 ${bad.length} 个不支持的文件</span>` : ""}`
      : (bad.length ? `<span style="color:var(--warning)">所选内容中没有可上传的文件（支持 .md/.txt/.pdf/.docx/.pptx/.xlsx/图片/录音）</span>` : "");
  }
  const go = $("#up-go");
  if (go) go.textContent = ok.length ? `开始上传（${ok.length}）` : "开始上传";
}

async function addTagQuick(sid) {
  const name = $("#new-tag-name").value.trim();
  if (!name) return;
  const t = await jpost(`/api/spaces/${sid}/tags`, { name });
  if (!state.tags.find(x => x.id === t.id)) state.tags.push({ id: t.id, name });
  Object.entries(tagPickers).forEach(([pid, p]) => {
    p.tags = state.tags.slice();
    p.sel.add(t.id);
    drawTagPicker(pid);
  });
  $("#new-tag-name").value = "";
}

async function doUpload(lid) {
  const files = state._uploadFiles || [];
  if (!files.length) return toast("请选择文件或文件夹", "err");
  const tids = pickerValues("up-tag-picker");
  const fd = new FormData();
  fd.append("space_id", state.space);
  fd.append("library_id", lid);
  fd.append("tag_ids", JSON.stringify(tids));
  fd.append("vision", $("#up-vision")?.checked ? "1" : "0");
  for (const f of files) fd.append("files", f);
  $("#up-go").disabled = true; $("#up-go").textContent = `上传中（${files.length}）…`;
  try {
    await api("/api/documents/upload", { method: "POST", body: fd });
    closeModal();
    toast(`已接收 ${files.length} 个文件，正在排队索引（已完成 0/${files.length}）`, "ok");
    state.docs = await api(`/api/libraries/${lid}/documents`);
    drawDocTable(); pollProgress();
  } catch (e) { toast("上传失败: " + e.message, "err");
    $("#up-go").disabled = false;
    $("#up-go").textContent = `开始上传（${files.length}）`; }
}

async function reindexDoc(id) { await jpost(`/api/documents/${id}/reindex`);
  toast("已排队重建"); await renderLibDetail(); }
async function delDoc(id, name) {
  if (!confirm(`删除「${name}」？其索引与原文都会移除。`)) return;
  await api(`/api/documents/${id}`, { method: "DELETE" });
  if (state.drawer.did === id) closeDrawer();
  await loadSpaceData(); await renderLibDetail();
}

function newLibDialog() {
  openModal("新建库（空间内的检索隔离单元）", `
    <div class="field"><label>库名称（如：问数系统手册 / 会议纪要 / 规范制度）</label>
      <input class="input" id="lib-name"></div>
    <div class="field"><label>说明（可选，会用于检索路由）</label>
      <textarea class="input" id="lib-desc" rows="2"></textarea></div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" onclick="doNewLib()">创建</button>`);
}
async function doNewLib() {
  const name = $("#lib-name").value.trim();
  if (!name) return toast("库名必填", "err");
  const r = await jpost(`/api/spaces/${state.space}/libraries`,
    { name, description: $("#lib-desc").value.trim() });
  closeModal(); await loadSpaceData(); state.lib = r.id; renderKB();
}

async function delLib(lid) {
  const lib = state.libs.find(l => l.id === lid);
  if (!confirm(`删除库「${lib?.name || ""}」及其全部文档与索引？此操作不可撤销。`)) return;
  await api(`/api/libraries/${lid}`, { method: "DELETE" });
  state.lib = null;
  await loadSpaceData();
  toast("库已删除", "ok");
  switchView("kb");
}

/* ══════════ 文档详情抽屉（knowledge-hub 风格） ══════════ */
async function openDocDrawer(did) {
  const d = state.docs.find(x => x.id === did);
  if (!d) return;
  state.drawer = { ...state.drawer, did, doc: d, tree: null, page: 1,
    mode: "page", images: [] };
  $("#drawer-mask").classList.add("show");
  $("#doc-drawer").classList.add("show");
  $("#dw-icon").textContent = KIND_ICON[d.kind] || "📄";
  $("#dw-name").textContent = d.name;
  const lib = state.libs.find(l => l.id === d.library_id);
  $("#dw-lib").textContent = lib ? "库：" + lib.name : "";
  $("#dw-download").onclick = () => window.open(`/api/documents/${did}/download`);
  $("#dw-ask").onclick = askDocument;
  drawDrawerBody();
  try {
    state.drawer.images = await api(`/api/documents/${did}/images`);
  } catch (e) { state.drawer.images = []; }
  drawDrawerImages();
  if (d.status === "ready") {
    try { state.drawer.tree = await api(`/api/documents/${did}/tree`); drawDrawerContent(); }
    catch (e) { /* keep meta */ }
  }
}

function drawDrawerImages() {
  const box = $("#dw-images");
  if (!box) return;
  const imgs = state.drawer.images || [];
  const cnt = $("#dw-img-count");
  if (cnt) cnt.textContent = imgs.length ? `（${imgs.length}）` : "";
  if (!imgs.length) {
    box.innerHTML = `<div class="muted" style="font-size:12px">
      未识别到图片（或上传时关闭了图片理解）。</div>`;
    return;
  }
  box.innerHTML = imgs.map(im => `
    <div class="img-item">
      <div class="img-head" onclick="this.parentNode.classList.toggle('open')">
        <span>图${im.order_no}${im.page ? ` · 第${im.page}页` : ""}</span>
        <span class="chip" style="font-size:10.5px">${esc(im.img_type || "other")}</span>
        <span class="badge ${im.status === "ok" ? "ok" : "warn"}"
          style="font-size:10.5px">${im.status === "ok" ? "已识别" : "失败"}</span>
        <span style="flex:1"></span><span class="muted">▾</span>
      </div>
      <div class="img-body">
        <img src="/api/images/${im.id}/file" loading="lazy">
        ${im.transcription ? `<div class="img-txt"><b>转写</b>
          <div class="md">${md2html(im.transcription).html}</div></div>` : ""}
        ${im.description ? `<div class="img-txt"><b>说明</b>
          <div class="md">${md2html(im.description).html}</div></div>` : ""}
        <button class="btn sm" onclick="reocrImage('${im.id}')">重新识别</button>
        <span class="muted" style="font-size:11px">
          重识别后需在文档行点「重建」才会更新问答索引</span>
      </div>
    </div>`).join("");
}

async function reocrImage(iid) {
  toast("重新识别中…");
  try {
    const rec = await jpost(`/api/images/${iid}/reocr`);
    state.drawer.images = state.drawer.images.map(x => x.id === iid ? rec : x);
    drawDrawerImages();
    toast("识别结果已更新", "ok");
  } catch (e) { toast("重新识别失败：" + e.message, "err"); }
}
function closeDrawer() {
  $("#drawer-mask").classList.remove("show");
  $("#doc-drawer").classList.remove("show");
  state.drawer.did = null;
}
function initDrawerResizer() {
  const rz = $("#dw-resizer"), dw = $("#doc-drawer");
  const saved = localStorage.getItem("dkb-drawer-w");
  if (saved) dw.style.width = saved + "px";
  let sx = 0, sw = 0, drag = false;
  rz.addEventListener("mousedown", e => {
    drag = true; sx = e.clientX; sw = dw.offsetWidth;
    e.preventDefault();
  });
  window.addEventListener("mousemove", e => {
    if (!drag) return;
    const w = Math.min(Math.max(480, sw + (sx - e.clientX)),
      Math.min(1600, innerWidth * 0.95));
    dw.style.width = w + "px";
  });
  window.addEventListener("mouseup", () => {
    if (drag) localStorage.setItem("dkb-drawer-w", dw.offsetWidth);
    drag = false;
  });
}

function drawDrawerBody() {
  const d = state.drawer.doc;
  const stBadge = d.status === "ready" ? `<span class="badge ok">就绪 · ${d.pages} 页</span>`
    : d.status === "failed" ? `<span class="badge err">解析失败</span>`
    : `<span class="badge warn">${d.status === "indexing" ? "索引中" : "排队中"}</span>`;
  $("#dw-body").innerHTML = `
    <div id="dw-live"></div>
    ${d.status === "failed" ? `<div class="err-box mb8">${esc(d.error || "解析失败")}
      <button class="btn sm" style="margin-left:10px" onclick="reindexDoc('${d.id}');closeDrawer()">重新解析</button></div>` : ""}
    <div class="meta-grid">
      <div><div class="k">类型</div>${esc(d.kind)}</div>
      <div><div class="k">状态</div>${stBadge}</div>
      <div><div class="k">页数</div>${d.pages || "-"}</div>
      <div><div class="k">上传时间</div>${esc(d.created_at || "")}</div>
    </div>
    <div class="dw-sec">内容描述（索引时自动生成）</div>
    <div style="font-size:13px;color:var(--text-2)">${esc(d.description || "（无）")}</div>
    <div class="dw-sec">标签 <button class="btn sm ghost" style="color:var(--brand)" onclick="editDocTags()">✎ 编辑</button></div>
    <div class="tag-pick" id="dw-tags"></div>
    <div class="dw-sec">图片理解 <span id="dw-img-count"></span></div>
    <div id="dw-images"></div>
    ${d.status === "ready" ? `
    <div class="dw-sec">内容</div>
    <div class="view-toggle">
      <button id="vt-page" class="on" onclick="setDrawerMode('page')">逐页浏览</button>
      <button id="vt-tree" onclick="setDrawerMode('tree')">结构树</button>
    </div>
    <div id="dw-content" style="margin-top:6px"></div>` : ""}`;
  drawDrawerTags();
  if (d.status === "ready" && state.drawer.tree) drawDrawerContent();
}

function updateDrawerProgress(payload) {
  const el = $("#dw-live");
  if (!el || !state.drawer.did) return;
  if (payload.stage === "done" || payload.stage === "failed") { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="dw-progress"><span class="pulse"></span>${esc(payload.detail || "处理中…")}</div>`;
}

function drawDrawerTags() {
  const d = state.drawer.doc;
  const cur = (d.tag_names || "").split(",").filter(Boolean);
  $("#dw-tags").innerHTML = cur.length
    ? cur.map(t => `<span class="chip on">${esc(t)}</span>`).join("")
    : '<span class="muted">暂无标签</span>';
}
function setDrawerMode(m) {
  state.drawer.mode = m;
  $("#vt-page")?.classList.toggle("on", m === "page");
  $("#vt-tree")?.classList.toggle("on", m === "tree");
  drawDrawerContent();
}
async function editDocTags() {
  const d = state.drawer.doc;
  const cur = new Set((d.tag_names || "").split(",").filter(Boolean));
  const selected = state.tags.filter(t => cur.has(t.name)).map(t => t.id);
  openModal("编辑标签 · " + d.name, `
    <div class="field"><label>该文件的标签（可搜索、可展开；作为检索过滤维度）</label>
      <div id="ed-tag-picker" class="tag-picker"></div></div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" onclick="saveDocTags('${d.id}')">保存</button>`);
  mountTagPicker("ed-tag-picker", state.tags, selected);
}
async function saveDocTags(did) {
  const tids = pickerValues("ed-tag-picker");
  await jpost(`/api/documents/${did}/tags`, { tag_ids: tids });
  closeModal();
  const rows = await api(`/api/libraries/${state.drawer.doc.library_id}/documents`);
  state.docs = rows;
  state.drawer.doc = rows.find(x => x.id === did) || state.drawer.doc;
  drawDrawerBody(); drawDocTable();
  toast("标签已更新", "ok");
}
function drawDrawerContent() {
  const box = $("#dw-content");
  if (!box || !state.drawer.tree) return;
  if (state.drawer.mode === "tree") {
    const flat = (nodes, lvl = 0) => (nodes || []).map(n => `
      <div class="tree-node">
        <div class="tn-row" onclick="gotoPage(${n.start_index})" title="点击跳到第 ${n.start_index} 页">
          <span class="tw">${n.nodes && n.nodes.length ? "▾" : "·"}</span>
          <span>${esc(n.title || "")}</span>
          <span class="tp">p${n.start_index}-${n.end_index}</span>
        </div>
        ${n.summary ? `<div class="tn-sum">${esc((n.summary || "").slice(0, 120))}</div>` : ""}
        ${n.nodes && n.nodes.length ? `<div class="tn-kids">${flat(n.nodes, lvl + 1)}</div>` : ""}
      </div>`).join("");
    box.innerHTML = `<div class="help mb8">点任一章节跳到对应页；共 ${state.drawer.doc.pages} 页。</div>` + flat(state.drawer.tree);
  } else {
    box.innerHTML = `
      <div class="pager">
        <button class="btn sm" onclick="stepPage(-1)">‹</button>
        <span id="pg-label">第 ${state.drawer.page} / ${state.drawer.doc.pages} 页</span>
        <button class="btn sm" onclick="stepPage(1)">›</button>
      </div>
      <div class="page-content md" id="pg-body"><span class="muted">加载中…</span></div>`;
    loadDrawerPage();
  }
}
function stepPage(delta) {
  const max = state.drawer.doc.pages || 1;
  state.drawer.page = Math.min(max, Math.max(1, state.drawer.page + delta));
  $("#pg-label").textContent = `第 ${state.drawer.page} / ${max} 页`;
  loadDrawerPage();
}
function gotoPage(p) {
  state.drawer.page = Math.max(1, p | 0);
  setDrawerMode("page");
}
async function loadDrawerPage() {
  const body = $("#pg-body");
  if (!body) return;
  try {
    const pg = await api(`/api/documents/${state.drawer.did}/pages/${state.drawer.page}`);
    body.innerHTML = marked.parse(pg.markdown || "（本页无文本）");
  } catch (e) { body.innerHTML = `<span class="muted">读取失败：${esc(e.message)}</span>`; }
}

/* ══════════ 问答工作台（豆包风格输入卡） ══════════ */
function renderChat() {
  const v = $("#view");
  v.innerHTML = `
    <div id="chat-wrap">
      <div id="chat-main">
        <div id="kp-target-bar" style="display:none"></div>
        <div id="msgs"></div>
        <div id="composer">
          <div class="input-card" id="input-card">
            <div class="kp-composer-top">
              <button class="kp-inline-target" id="kp-inline-target"
                onclick="KP.openTargetHub()" title="切换目标"></button>
              <span class="kp-subs" id="kp-kb-subs"></span>
            </div>
            <div class="attach-preview" id="attach-list"></div>
            <div class="scope-preview" id="scope-chips"></div>
            <textarea id="q-input" rows="2" placeholder="输入问题，Enter 发送，Shift+Enter 换行"></textarea>
            <div class="control-bar kp-kb-bar">
              <button class="kp-plus-btn" id="kp-kb-plus"
                onclick="KP.openKbPlusMenu()" title="添加文件 / 链接 / 范围 / 模式">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2"
                    stroke-linecap="round"/></svg></button>
              <button class="kp-scope-btn" id="kp-kb-scope"
                onclick="toggleLibPop()" title="选择检索范围">
                范围 <b id="kb-scope-summary">全部库</b></button>
              <span class="spacer"></span>
              <button class="send-round" id="btn-send" title="发送">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor"
                    stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </div>
            <div id="lib-pop"></div>
          </div>
          <div class="composer-hint" id="composer-hint"></div>
        </div>
      </div>
    </div>`;
  $("#btn-send").onclick = sendQuestion;
  $("#btn-send").disabled = state.running;
  const attachBtn = $("#btn-attach");
  if (attachBtn) attachBtn.onclick = () => $("#adhoc-input").click();
  const libBtn = $("#btn-libpick");
  if (libBtn) libBtn.onclick = toggleLibPop;
  $("#q-input").addEventListener("keydown", e => {
    if (e.key !== "Enter" || e.shiftKey) return;   // Shift+Enter 换行
    if (e.isComposing || e.keyCode === 229) return; // 输入法选词中不发送
    e.preventDefault();
    sendQuestion();
  });
  $$("#mode-seg button").forEach(b => b.onclick = () => setChatMode(b.dataset.m));
  document.addEventListener("click", escLibPop, true);
  let ih = $("#adhoc-input");
  if (!ih) {
    ih = document.createElement("input");
    ih.type = "file"; ih.id = "adhoc-input"; ih.multiple = true; ih.style.display = "none";
    $("#composer").appendChild(ih);
  }
  ih.onchange = doAdhocUpload;
  syncCard();
  ensureScrollGuard($("#msgs"));
  if (!state.sessionId) { welcomeScreen(); maybeRestoreLive(); }
  else reloadSession().then(() => maybeRestoreLive());
  const main = $("#chat-main");
  if (main) main.classList.toggle("home", !state.sessionId);
  if (window.KP) KP.mountChatTargets();
}

function escLibPop(e) {
  const pop = $("#lib-pop");
  if (pop && pop.classList.contains("show") && !e.target.closest("#lib-pop")
      && !e.target.closest("#btn-libpick")) pop.classList.remove("show");
}
function toggleLibPop() {
  const pop = $("#lib-pop");
  const show = !pop.classList.contains("show");
  pop.classList.toggle("show", show);
  if (show) drawLibPop();
}
function drawLibPop() {
  const pop = $("#lib-pop");
  pop.innerHTML = `
    <div class="lp-sec">选择检索库（可多选，空间内才可联合）</div>
    ${state.libs.map(l => `
      <label><input type="checkbox" value="${l.id}" ${state.scopeLibs.includes(l.id) ? "checked" : ""}>
        📁 ${esc(l.name)} <span class="muted" style="font-size:11px">${l.doc_count || 0} 篇就绪${
          l.pending_count ? ` · ${l.pending_count} 解析中` : ""}${
          l.failed_count ? ` · ${l.failed_count} 失败` : ""}</span></label>`).join("")}
    <div class="lp-sec">标签过滤（AND，缩小文档范围；可搜索）</div>
    <div id="lp-tag-picker" class="tag-picker"></div>`;
  $$("#lib-pop input[type=checkbox]").forEach(cb => cb.onchange = () => {
    state.scopeLibs = $$("#lib-pop input:checked").map(x => x.value);
    saveScope(); syncCard();
  });
  mountTagPicker("lp-tag-picker", state.tags, state.scopeTags, ids => {
    state.scopeTags = ids;
    saveScope(); syncCard();
  });
}
function syncCard() {
  const adhoc = state.chatMode === "adhoc";
  const scopeBox = $("#scope-chips");
  if (scopeBox) scopeBox.innerHTML = !adhoc ? state.scopeLibs.map(id => {
    const l = state.libs.find(x => x.id === id);
    return l ? `<span class="att-chip"><span class="ic">📁</span><span class="nm">${esc(l.name)}</span>
      <span class="x" onclick="rmScopeLib('${id}')">✕</span></span>` : "";
  }).join("") + state.scopeTags.map(tid => {
    const t = state.tags.find(x => x.id === tid);
    return t ? `<span class="att-chip"><span class="ic">#</span><span class="nm">${esc(t.name)}</span>
      <span class="x" onclick="rmScopeTag('${tid}')">✕</span></span>` : "";
  }).join("") : "";
  drawAttachChips();
  const summary = $("#kb-scope-summary");
  if (summary) summary.textContent = adhoc ? "临时文件"
    : (state.scopeLibs.length ? `${state.scopeLibs.length} 个库` : "全部库");
  const scopeBtn = $("#kp-kb-scope");
  if (scopeBtn) scopeBtn.classList.toggle("on",
    !adhoc && (!!state.scopeLibs.length || !!state.scopeTags.length));
  const pend = state.scopeLibs.reduce((n, id) => {
    const l = state.libs.find(x => x.id === id);
    return n + ((l && l.pending_count) || 0);
  }, 0);
  const hint = $("#composer-hint");
  if (hint) hint.textContent = adhoc
    ? "临时文件仅本次使用，不进入知识库"
    : (pend ? `所选库还有 ${pend} 篇正在索引` : "");
  const input = $("#q-input");
  if (input) input.placeholder = adhoc ? "针对文件提问…" : "问点什么…";
}
function rmScopeLib(id) { state.scopeLibs = state.scopeLibs.filter(x => x !== id); saveScope(); syncCard(); }
function rmScopeTag(id) { state.scopeTags = state.scopeTags.filter(x => x !== id); saveScope(); syncCard(); }

function setChatMode(m) {
  if (state.chatMode === m) return;
  state.chatMode = m; state.sessionId = null;
  syncCard(); welcomeScreen();
}

function saveScope() {
  if (!state.space) return;
  localStorage.setItem(`dkb-scope-${state.space}`, JSON.stringify(
    { libs: state.scopeLibs, tags: state.scopeTags }));
}
function loadScope() {
  state.scopeLibs = []; state.scopeTags = [];
  try {
    const s = JSON.parse(localStorage.getItem(`dkb-scope-${state.space}`) || "{}");
    state.scopeLibs = (s.libs || []).filter(id => state.libs.find(l => l.id === id));
    state.scopeTags = (s.tags || []).filter(id => state.tags.find(t => t.id === id));
  } catch (e) { /* ignore */ }
}

function welcomeScreen() {
  const adhoc = state.chatMode === "adhoc";
  const hints = adhoc
    ? ["总结这份文件的核心要点", "这份材料里的结论有哪些依据？",
       "对比材料中提到的方案", "基于材料给出改进建议"]
    : ["问数系统能不能支持自然语言生成 SQL？", "升级后的 SQL 生成模块改进了什么？",
       "使用手册里有哪些导出方式？", "如果现有系统做不到 X，该怎么改进？"];
  $("#msgs").innerHTML = `
    <div class="welcome">
      <div class="wl-top"><div class="wl-mark">鉴</div><h2>${adhoc ? "临时资料问答" : "今天想了解什么？"}</h2></div>
      <p>${adhoc ? "附加的文件只在本会话使用，不进入知识库；像和助手面对面看材料一样提问。"
        : "从知识、智能体、团队、工作流或 Excel 问数开始；答案会带出处。"}</p>
      <div class="suggests">${hints.map((h, i) => `
        <div class="suggest-card" style="animation-delay:${i * 55}ms" onclick="fillQ('${esc(h)}')">${esc(h)}</div>`).join("")}</div>
    </div>`;
}
function fillQ(t) { $("#q-input").value = t; $("#q-input").focus(); }

async function doAdhocUpload() {
  const files = $("#adhoc-input").files;
  if (!files.length) return;
  $("#composer-hint").textContent = "解析中…";
  const fd = new FormData();
  fd.append("space_id", state.space);
  for (const f of files) fd.append("files", f);
  try {
    const r = await api("/api/adhoc/upload", { method: "POST", body: fd });
    for (const f of r.files) {
      if (f.error) toast(`${f.name} 解析失败：${f.error}`, "err");
      else state.adhocFiles.push(f);
    }
    $("#composer-hint").textContent = "";
    syncCard();
  } catch (e) { toast("上传失败 " + e.message, "err"); $("#composer-hint").textContent = ""; }
  $("#adhoc-input").value = "";
}

function drawAttachChips() {
  const el = $("#attach-list");
  if (!el) return;
  el.innerHTML = state.adhocFiles.map(f => `
    <span class="att-chip"><span class="ic">${KIND_ICON[(f.name || "").split(".").pop()] || "📎"}</span>
      <span class="nm">${esc(f.name)}</span>
      <span class="sz">${f.mode === "inline" ? f.chars + "字" : (f.pages || 0) + "页索引"}</span>
      <span class="x" onclick="dropAdhoc('${f.id}')">✕</span></span>`).join("");
}
async function dropAdhoc(id) {
  state.adhocFiles = state.adhocFiles.filter(f => f.id !== id);
  syncCard();
  try { await api(`/api/adhoc/${id}`, { method: "DELETE" }); } catch (e) {}
}

/* 滚动守卫：用户一旦主动上滚/滚轮/触屏，就暂停自动跟随；
   滚回最底部才恢复。避免流式增量过密时把视口一直往下拖。 */
function ensureScrollGuard(el) {
  if (!el || el._guardInstalled) return;
  el._guardInstalled = true;
  el._paused = false;
  el._lastTop = el.scrollTop;
  el.addEventListener("wheel", () => { el._paused = true; }, { passive: true });
  el.addEventListener("touchmove", () => { el._paused = true; }, { passive: true });
  el.addEventListener("scroll", () => {
    const top = el.scrollTop;
    if (top < el._lastTop - 2) el._paused = true;          // 向上滚动
    if (el.scrollHeight - top - el.clientHeight <= 8) el._paused = false;
    el._lastTop = top;
  }, { passive: true });
}
function canFollow(el) { return !el || !el._paused; }
function stickBottom(el) { if (canFollow(el)) el.scrollTop = el.scrollHeight; }

function msgShell(cls, inner) {
  const box = $("#msgs");
  ensureScrollGuard(box);
  box._paused = false;
  const d = document.createElement("div");
  d.className = "msg " + cls; d.innerHTML = inner;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
  return d;
}

/* ── 流式直播状态：放在 state.live，视图切换不丢中间结果 ── */
function parseMaybeJson(v) {
  if (typeof v !== "string") return v;
  try { return JSON.parse(v); } catch (e) { return null; }
}

function toolHuman(name, rawArgs) {
  const a = parseMaybeJson(rawArgs) || {};
  if (name === "get_page_content" && a.doc_name)
    return `读取《${a.doc_name}》第 ${a.pages || "?"} 页`;
  if (name === "get_document_structure" && a.doc_name)
    return `查看《${a.doc_name}》的章节结构`;
  if (name === "browse_documents") return "浏览候选文档";
  if (name === "get_page_content") return "读取文档页面";
  return name;
}

function toolResultHuman(rawOutput) {
  const o = parseMaybeJson(rawOutput);
  if (o && typeof o === "object") {
    if (Array.isArray(o.pages)) return `已读取 ${o.pages.length} 页`;
    if (o.success === false && o.error) return `失败：${String(o.error).slice(0, 60)}`;
    if (o.success === true) return "已返回结果";
  }
  const s = String(rawOutput || "").replace(/\s+/g, " ").trim();
  return s ? s.slice(0, 60) + (s.length > 60 ? "…" : "") : "已返回空结果";
}

function rawBlock(label, payload) {
  const t = typeof payload === "string" ? payload : JSON.stringify(payload);
  if (!t) return "";
  return `<details class="raw"><summary>${label}</summary>
    <pre>${esc(t.slice(0, 2000))}</pre></details>`;
}

function liveStepHTML(s) {
  if (s.kind === "route")
    return `<div class="step">选中文档：${(s.docs || []).map(d =>
      `<span class="chip" style="font-size:11px">${esc(d.name)}</span>`).join(" ")
      || "（全部临时文档）"}</div>`;
  if (s.kind === "tool")
    return `<div class="step tool">
      <span class="nm">🔧 ${esc(toolHuman(s.name, s.arguments))}</span>
      ${rawBlock("原始参数", s.arguments)}</div>`;
  if (s.kind === "toolres")
    return `<div class="step">
      <span class="nm">↳ ${esc(toolResultHuman(s.output))}</span>
      ${rawBlock("原始结果", s.output)}</div>`;
  return "";
}

function liveAnswerHTML(live) {
  const verdict = live.result ? live.result.verdict : "";
  let html;
  if (live.acc) {
    html = renderAnswerHTML(live.acc, verdict);
    if (live.result && live.result.missing && String(live.result.missing).trim()
        && !live.acc.includes("【缺失】")) {
      html += `<section class="ans-sec ans-missing">
        <div class="ans-head"><span class="ans-ico">📎</span>
          <span class="ans-label">还缺什么资料</span></div>
        <div class="md"><p>${esc(live.result.missing)}</p></div></section>`;
    }
  } else if (live.error) {
    html = `<div class="ans-card" style="color:var(--error)">
      请求失败：${esc(live.error)}</div>`;
  } else {
    html = `<span class="pulse"></span> ${esc(live.stage || "准备中")}`;
  }
  return html;
}

function liveInnerHTML(live) {
  const stepsHTML = live.steps.map(liveStepHTML).join("")
    + (live.thinkChars
      ? `<div class="step think" data-think="1">💭 模型推理中（已接收约 ${live.thinkChars} 字，增量略）</div>`
      : "");
  return `<div class="row"><div class="avatar ai">知</div>
    <div style="min-width:0;flex:1">
      <div class="ai-body"><div class="ans-wrap">${liveAnswerHTML(live)}</div></div>
      <div class="trace ${live.traceOpen ? "open" : ""}">
        <div class="th"><span class="caret">▸</span> 中间过程
          <span class="stage-pill">${live.stage ? "· " + esc(live.stage) : ""}</span></div>
        <div class="steps">${stepsHTML}</div>
      </div>
      <div class="citations"></div>
      <div class="feedback" style="${live.result ? "display:flex" : "display:none"}">
        <button class="fb-btn like" title="回答有帮助">👍</button>
        <button class="fb-btn fix" title="回答有误，记录纠正">✏️</button>
        <span class="muted fb-state"></span>
      </div>
    </div></div>`;
}

function bindLiveRefs(shell, live) {
  const refs = {
    body: shell.querySelector(".ans-wrap"),
    steps: shell.querySelector(".steps"),
    stage: shell.querySelector(".stage-pill"),
    cites: shell.querySelector(".citations"),
    fb: shell.querySelector(".feedback"),
    trace: shell.querySelector(".trace"),
    th: shell.querySelector(".trace .th"),
  };
  ensureScrollGuard(refs.steps);
  refs.th.onclick = () => {
    live.traceOpen = !live.traceOpen;
    refs.trace.classList.toggle("open", live.traceOpen);
  };
  return refs;
}

function mountLiveShell() {
  const box = $("#msgs");
  if (!box || !state.live) return null;
  let shell = box.querySelector('[data-live="1"]');
  if (!shell) {
    shell = document.createElement("div");
    shell.className = "msg ai";
    shell.dataset.live = "1";
    box.appendChild(shell);
  }
  return shell;
}

function paintLive() {
  const live = state.live;
  const box = $("#msgs");
  if (!live || !box) return;
  const shell = box.querySelector('[data-live="1"]');
  if (!shell) return;

  // 首帧：完整构建一次，之后增量更新（不重建步骤区，保证滚动条可拖动）
  if (!shell._refs) {
    shell.innerHTML = liveInnerHTML(live);
    shell._refs = bindLiveRefs(shell, live);
    live._stepCount = live.steps.length;
    live._thinkChars = live.thinkChars;
    live._acc = live.acc;
    live._stage = live.stage;
    live._result = live.result;
    const r = shell._refs;
    r.steps.scrollTop = r.steps.scrollHeight;
    if (live.result) applyLiveResult(shell, live, r);
    syncLiveFeedback(shell, live, r);
    stickBottom(box);
    return;
  }

  const r = shell._refs;
  // 阶段文案
  if (live.stage !== live._stage) {
    r.stage.textContent = live.stage ? "· " + live.stage : "";
    live._stage = live.stage;
    if (!live.acc) r.body.innerHTML = liveAnswerHTML(live);
  }
  // 新步骤只追加，已有步骤 DOM 不动 → 滚动条不被打断
  if (live.steps.length > live._stepCount) {
    const followSteps = canFollow(r.steps);
    for (let i = live._stepCount; i < live.steps.length; i++) {
      r.steps.insertAdjacentHTML("beforeend", liveStepHTML(live.steps[i]));
    }
    live._stepCount = live.steps.length;
    if (followSteps) r.steps.scrollTop = r.steps.scrollHeight;
  }
  // 推理进度：原地更新同一条
  if (live.thinkChars !== live._thinkChars) {
    let t = r.steps.querySelector('[data-think="1"]');
    if (!t) {
      r.steps.insertAdjacentHTML("beforeend",
        `<div class="step think" data-think="1"></div>`);
      t = r.steps.querySelector('[data-think="1"]');
    }
    t.textContent = `💭 模型推理中（已接收约 ${live.thinkChars} 字，增量略）`;
    live._thinkChars = live.thinkChars;
  }
  // 答案正文
  if (live.acc !== live._acc || live.result !== live._result) {
    r.body.innerHTML = liveAnswerHTML(live);
    live._acc = live.acc;
    if (live.result && live.result !== live._result) {
      live._result = live.result;
      applyLiveResult(shell, live, r);
    }
  }
  syncLiveFeedback(shell, live, r);
  stickBottom(box);
}

function syncLiveFeedback(shell, live, r) {
  if (!live.result) return;
  const evId = live.qaEvent || live.result.qa_event || "";
  if (evId && r.fb.dataset.event !== evId) {
    r.fb.dataset.event = evId;
    bindFeedback(shell);
  }
}

function applyLiveResult(shell, live, r) {
  const cites = live.result.citations || [];
  if (cites.length) renderCites(r.cites, cites);
  if ((live.result.provenance || []).length) {
    r.cites.insertAdjacentHTML("beforeend",
      renderProvenance(live.result.provenance));
  }
  r.fb.style.display = "flex";
  r.fb.dataset.event = live.qaEvent || live.result.qa_event || "";
  bindFeedback(shell);
}

function maybeRestoreLive() {
  const live = state.live;
  if (!live || !live.running) return;
  const same = live.sessionId
    ? live.sessionId === state.sessionId
    : (live.spaceId === state.space && live.mode === state.chatMode && !state.sessionId);
  if (!same) return;
  const wl = $("#msgs .welcome"); if (wl) wl.remove();
  ensureScrollGuard($("#msgs"));
  mountLiveShell();
  paintLive();
}

async function sendQuestion() {
  const q = $("#q-input").value.trim();
  if (!q || state.running) return;
  if (state.chatMode === "kb" && !state.scopeLibs.length && !state.libs.length)
    return toast("请先在知识空间创建并勾选库", "err");
  if (state.chatMode === "adhoc" && !state.adhocFiles.length && !state.refLibs)
    return toast("请先附加临时文件", "err");
  const wl = $("#msgs .welcome"); if (wl) wl.remove();
  const cm = $("#chat-main"); if (cm) cm.classList.remove("home");
  state.running = true;
  $("#btn-send").disabled = true;
  msgShell("user", `<div class="row"><div class="bubble">${esc(q)}</div></div>`);
  $("#q-input").value = "";
  state.live = {
    q, acc: "", thinkChars: 0, steps: [], stage: "", result: null,
    qaEvent: "", error: "", sessionId: null, spaceId: state.space,
    mode: state.chatMode, running: true, traceOpen: false,
  };
  const live = state.live;
  mountLiveShell();
  paintLive();
  const reqBody = {
    space_id: state.space, mode: state.chatMode, question: q,
    session_id: state.sessionId, library_ids: state.scopeLibs,
    tag_ids: state.scopeTags, adhoc_ids: state.adhocFiles.map(f => f.id),
    ref_library_ids: state.refLibs ? state.scopeLibs : [],
    skill_ids: window.KP && KP.currentSkillIds ? KP.currentSkillIds() : [],
    kb_ids: window.KP && KP.currentKBIds ? KP.currentKBIds() : [],
  };
  try {
    await sseStream("/api/qa/stream", reqBody, ev => {
      if (ev.type === "start") {
        live.sessionId = ev.session_id; state.sessionId = ev.session_id;
      } else if (ev.type === "stage") {
        live.stage = ev.detail;
      } else if (ev.type === "route") {
        live.steps.push({ kind: "route", docs: ev.docs });
      } else if (ev.type === "thinking") {
        live.thinkChars += (ev.delta || "").length;
      } else if (ev.type === "tool_call") {
        live.steps.push({ kind: "tool", name: ev.name,
          arguments: ev.arguments });
      } else if (ev.type === "tool_result") {
        live.steps.push({ kind: "toolres", name: ev.name, output: ev.output });
      } else if (ev.type === "answer") {
        live.acc += ev.delta;
      } else if (ev.type === "result") {
        live.result = ev; live.acc = ev.answer || live.acc; live.stage = "";
      } else if (ev.type === "error") {
        live.error = ev.message;
      } else if (ev.type === "done") {
        live.qaEvent = ev.qa_event || live.qaEvent;
      }
      paintLive();
    });
  } catch (e) {
    live.error = e.message; paintLive();
  }
  live.running = false;
  state.running = false; $("#btn-send").disabled = false;
  state.live = null;   // 结束后以服务端历史为准，切回时 reloadSession 恢复
  loadHistory();
}

function renderCites(box, cites) {
  if (!cites || !cites.length) return;
  box.innerHTML = `<div class="side-sec" style="padding-left:0">引用依据（${cites.length}）</div>` +
    cites.map((c, i) => `
    <div class="cite-item" data-did="${c.doc_id}" data-page="${c.page}">
      <div class="h" onclick="this.parentNode.classList.toggle('open');loadCiteEx(this.parentNode)">
        <span class="pg">[${i + 1}]</span> ${esc(c.doc_name)} · 第 ${c.page} 页
        <span class="muted" style="margin-left:auto">▼ 原文</span></div>
      <div class="ex md">${md2html(c.excerpt || "").html}</div>
    </div>`).join("");
}
async function loadCiteEx(el) {
  if (el.dataset.loaded) return;
  el.dataset.loaded = "1";
  try {
    const did = state.docs.find(d => d.doc_id === el.dataset.did)?.id;
    if (!did) return;
    const pg = await api(`/api/documents/${did}/pages/${el.dataset.page}`);
    el.querySelector(".ex").innerHTML =
      md2html((pg.markdown || "").slice(0, 1600)).html;
  } catch (e) {}
}

function bindFeedback(shell) {
  const fb = shell.querySelector(".feedback");
  const evId = fb.dataset.event;
  if (!evId) return;
  fb.querySelector(".like").onclick = async () => {
    await jpost("/api/feedback", { event_id: evId, kind: "ok" });
    fb.querySelector(".fb-state").textContent = "已记录：回答有帮助";
    toast("已作为满意样本记录（会加强此类问答路径）", "ok");
  };
  fb.querySelector(".fix").onclick = () => {
    openModal("纠正这条回答", `
      <div class="field"><label>写下正确说法（将进入演进候选，审核后成为部门知识）</label>
      <textarea class="input" id="fix-text" rows="3" placeholder="例如：问数系统 v2.3 起已支持 X 功能"></textarea></div>`,
      `<button class="btn" onclick="closeModal()">取消</button>
       <button class="btn primary" onclick="doFix('${evId}')">提交纠正</button>`);
  };
}
async function doFix(evId) {
  const text = $("#fix-text").value.trim();
  if (!text) return toast("请填写纠正内容", "err");
  await jpost("/api/feedback", { event_id: evId, kind: "fix", correction: text });
  closeModal();
  toast("已生成候选知识，请到经验中心审核", "ok");
}

async function sseStream(url, body, onEvent) {
  const r = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(await r.text());
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      if (chunk.startsWith("data: ")) {
        try { onEvent(JSON.parse(chunk.slice(6))); } catch (e) {}
      }
    }
  }
}

/* ── 会话历史（侧栏） ── */
async function loadHistory() {
  if (!state.space) return;
  const rows = await api(`/api/spaces/${state.space}/sessions`);
  $("#hist-list").innerHTML = rows.slice(0, 30).map(s => `
    <div class="hist-item ${s.id === state.sessionId ? "active" : ""}" onclick="openSession('${s.id}','${s.mode}')">
      <div class="t">${s.mode === "kb" ? "📚" : "📎"} ${esc(s.title || "会话")}</div>
      <div class="m">${esc(s.updated_at || "")}</div>
      <button class="hist-del" title="删除会话"
        onclick="event.stopPropagation();delSession('${s.id}')">🗑</button>
    </div>`).join("");
}
async function delSession(id) {
  if (!confirm("删除该会话？消息记录一并移除。")) return;
  await api(`/api/sessions/${id}`, { method: "DELETE" });
  if (state.sessionId === id) { state.sessionId = null; if (state.view === "chat") renderChat(); }
  loadHistory();
}
function openSession(id, mode) {
  state.sessionId = id; state.chatMode = mode;
  if (state.view !== "chat") switchView("chat"); else renderChat();
  loadHistory();
}
async function reloadSession() {
  const s = await api(`/api/sessions/${state.sessionId}`);
  $("#chat-main")?.classList.remove("home");
  $("#msgs").innerHTML = "";
  for (const m of s.messages) {
    if (m.role === "user") msgShell("user", `<div class="row"><div class="bubble">${esc(m.content)}</div></div>`);
    else {
      const meta = m.meta || {};
      const shell = msgShell("ai", `<div class="row"><div class="avatar ai">知</div>
        <div style="min-width:0;flex:1">
        <div class="ai-body"><div class="ans-wrap"></div></div>
        <div class="trace"><div class="th" onclick="this.parentNode.classList.toggle('open')">
          <span class="caret">▸</span> 中间过程</div><div class="steps"></div></div>
        <div class="citations"></div>
        <div class="feedback"><button class="fb-btn like" title="回答有帮助">👍</button>
          <button class="fb-btn fix" title="记录纠正">✏️</button>
          <span class="muted fb-state"></span></div>
        </div></div>`);
      shell.querySelector(".ans-wrap").innerHTML =
        renderAnswerHTML(m.content, meta.verdict);
      renderCites(shell.querySelector(".citations"), meta.citations || []);
      if ((meta.provenance || []).length) {
        shell.querySelector(".citations").insertAdjacentHTML("beforeend",
          renderProvenance(meta.provenance));
      }
      const steps = shell.querySelector(".steps");
      if (meta.route && meta.route.docs) {
        const d0 = document.createElement("div"); d0.className = "step";
        d0.innerHTML = "选中文档：" + meta.route.docs.map(x => esc(x.name)).join("、");
        steps.appendChild(d0);
      }
      (meta.tools || []).forEach(t => {
        const d = document.createElement("div"); d.className = "step tool";
        d.innerHTML = `<span class="nm">🔧 ${esc(t)}</span>`; steps.appendChild(d);
      });
      if (meta.qa_event) {
        shell.querySelector(".feedback").dataset.event = meta.qa_event;
        bindFeedback(shell);
      }
    }
  }
  $("#msgs").scrollTop = $("#msgs").scrollHeight;
  syncCard();
}

/* ══════════ 迷你问答窗（库详情右下 FAB） ══════════ */
function miniKey() {
  const sc = state.mini.scope || {};
  return `${state.mini.libId || ""}|${sc.type || "library"}|${sc.docId || ""}`;
}
function defaultMiniScope() {
  return { type: "library", docId: null, dbId: null, docName: "",
           page: 0, selection: "", before: "", after: "" };
}
function truncateText(s, n) {
  s = String(s || "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}
function initMiniChat() {
  $("#kb-fab").onclick = () => {
    const shown = $("#mini-chat").classList.contains("show");
    if (shown && state.mini.libId === state.lib) {
      $("#mini-chat").classList.remove("show");
      state.mini.open = false;
    } else {
      openMiniFor(state.lib);
    }
  };
  $("#mc-close").onclick = () => {
    $("#mini-chat").classList.remove("show");
    state.mini.open = false;
  };
  $("#mc-new").onclick = () => {
    if (state.mini.running) return toast("回答中，请稍候", "err");
    delete state.mini.sessions[miniKey()];
    miniWelcome();
    drawMiniScope();
  };
  $("#mc-send").onclick = miniAsk;
  $("#mc-q").addEventListener("keydown", e => {
    if (e.key !== "Enter" || e.shiftKey) return;
    if (e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    miniAsk();
  });
}
function scopePlaceholder() {
  const sc = state.mini.scope || {};
  if (sc.type === "selection") return "针对选中片段提问，Enter 发送…";
  if (sc.type === "document") return `针对《${truncateText(sc.docName, 20)}》提问…`;
  return "针对本库提问，Enter 发送，Shift+Enter 换行";
}
function miniWelcome() {
  const lib = state.libs.find(l => l.id === state.mini.libId);
  const sc = state.mini.scope || {};
  let text;
  if (sc.type === "selection") {
    text = `已选中「${esc(truncateText(sc.selection, 60))}」。<br>
      可直接问“这个数怎么来的”、原文出处或它的含义。`;
  } else if (sc.type === "document") {
    text = `针对《${esc(truncateText(sc.docName, 30))}》提问。<br>
      回答会尽量给出页码与原文；选中正文中的文字可直接问来源。`;
  } else {
    text = `直接问答「${esc(lib?.name || "")}」（${lib?.doc_count || 0} 篇）。<br>
      回答会给出引用；点引用或溯源卡可跳回原文。`;
  }
  $("#mc-msgs").innerHTML = `<div class="muted"
    style="text-align:center;padding:20px;font-size:12px">💬 ${text}</div>`;
}
function drawMiniScope() {
  const lib = state.libs.find(l => l.id === state.mini.libId);
  const sc = state.mini.scope || {};
  let html = `<span class="chip ${sc.type === "library" ? "on" : ""}"
    onclick="setMiniScope('library')">本库：${esc(truncateText(lib?.name || "", 14))}</span>`;
  if (sc.docId) {
    html += `<span class="chip ${sc.type === "document" ? "on" : ""}"
      onclick="setMiniScope('document')">本文档：${esc(truncateText(sc.docName, 16))}</span>`;
  }
  if (sc.selection) {
    html += `<span class="chip on">选中：${esc(truncateText(sc.selection, 16))}
      <span class="x" onclick="clearMiniSelection()">✕</span></span>`;
  }
  $("#mc-scope").innerHTML = html;
  $("#mc-q").placeholder = scopePlaceholder();
}
function setMiniScope(type) {
  if (state.mini.running) return toast("回答中，请稍候", "err");
  const sc = state.mini.scope || {};
  if (type === "library") {
    state.mini.scope = defaultMiniScope();
  } else if (type === "document") {
    if (!sc.docId) return;
    state.mini.scope = { ...defaultMiniScope(), type: "document",
      docId: sc.docId, dbId: sc.dbId, docName: sc.docName, page: sc.page };
  }
  drawMiniScope();
  const sid = state.mini.sessions[miniKey()];
  $("#mc-msgs").innerHTML = "";
  if (sid) restoreMini(sid); else miniWelcome();
}
function clearMiniSelection() {
  const sc = state.mini.scope || {};
  if (!sc.docId) return;
  state.mini.scope = { ...defaultMiniScope(), type: "document",
    docId: sc.docId, dbId: sc.dbId, docName: sc.docName, page: sc.page };
  drawMiniScope();
  const sid = state.mini.sessions[miniKey()];
  $("#mc-msgs").innerHTML = "";
  if (sid) restoreMini(sid); else miniWelcome();
}
function openMiniFor(lid) {
  const lib = state.libs.find(l => l.id === lid);
  if (!lib) return;
  if (state.mini.running) return toast("回答中，请稍候", "err");
  state.mini.libId = lid;
  state.mini.scope = defaultMiniScope();
  openMiniPanel();
}
async function openMiniPanel() {
  const lib = state.libs.find(l => l.id === state.mini.libId);
  if (!lib) return;
  state.mini.open = true;
  $("#mc-lib").textContent = lib.name;
  $("#mini-chat").classList.add("show");
  drawMiniScope();
  ensureScrollGuard($("#mc-msgs"));
  const key = miniKey();
  if (state.mini.running && state.mini.runningKey === key) return;
  if (!state.mini.sessions[key]) {
    try {
      const rows = await api(`/api/spaces/${state.space}/sessions?mode=kb`);
      const sc = state.mini.scope || {};
      const match = rows.find(s => {
        let x = {};
        try { x = JSON.parse(s.scope || "{}"); } catch (e) { return false; }
        if (!x.panel) return false;
        if (!(x.library_ids || []).includes(state.mini.libId)) return false;
        const ds = x.doc_ids || [];
        if (sc.docId) return ds.includes(sc.docId);
        return ds.length === 0 && sc.type === "library";
      });
      if (match) state.mini.sessions[key] = match.id;
    } catch (e) {}
  }
  if (miniKey() !== key) return;
  const sid = state.mini.sessions[key];
  $("#mc-msgs").innerHTML = "";
  if (sid) restoreMini(sid); else miniWelcome();
}
function askDocument() {
  const d = state.drawer.doc;
  if (!d) return;
  state.mini.libId = d.library_id;
  state.mini.scope = { ...defaultMiniScope(), type: "document",
    docId: d.doc_id, dbId: d.id, docName: d.name,
    page: state.drawer.page || 1 };
  closeDrawer();
  openMiniPanel();
}
function askSelection(action) {
  const ctx = state._selContext;
  const d = state.drawer.doc;
  if (!ctx || !d) return toast("请先在文档正文中选中内容", "err");
  state.mini.libId = d.library_id;
  state.mini.scope = { type: "selection", docId: d.doc_id, dbId: d.id,
    docName: d.name, page: ctx.page || (state.drawer.page || 1),
    selection: ctx.text, before: ctx.before, after: ctx.after };
  $("#sel-toolbar").classList.remove("show");
  closeDrawer();
  openMiniPanel();
  if (action === "why") {
    $("#mc-q").value = "这个数怎么来的？请给出计算依据或原文出处。";
    miniAsk();
  } else {
    $("#mc-q").focus();
  }
}
function initSelectionToolbar() {
  const tb = $("#sel-toolbar");
  document.addEventListener("mouseup", e => {
    if (e.target.closest("#sel-toolbar")) return;
    setTimeout(() => {
      const sel = window.getSelection();
      const text = sel ? String(sel).trim() : "";
      const pg = $("#pg-body");
      if (!pg || !text || !sel.rangeCount
          || !pg.contains(sel.anchorNode) || !pg.contains(sel.focusNode)) {
        tb.classList.remove("show");
        return;
      }
      const full = pg.textContent || "";
      const idx = full.indexOf(text);
      state._selContext = {
        text: text.slice(0, 500),
        page: state.drawer.page || 1,
        before: idx >= 0 ? full.slice(Math.max(0, idx - 300), idx) : "",
        after: idx >= 0 ? full.slice(idx + text.length,
                                     idx + text.length + 300) : "",
      };
      const r = sel.getRangeAt(0).getBoundingClientRect();
      tb.style.left = Math.min(window.innerWidth - 250,
                               Math.max(10, r.left)) + "px";
      tb.style.top = Math.max(70, r.top - 46) + "px";
      tb.classList.add("show");
    }, 0);
  });
  tb.querySelectorAll("button").forEach(b => b.onclick =
    () => askSelection(b.dataset.act));
  document.addEventListener("mousedown", e => {
    if (!e.target.closest("#sel-toolbar")) tb.classList.remove("show");
  });
}
function miniCitesHTML(cites) {
  if (!cites || !cites.length) return "";
  return `<div class="mc-meta" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'':'none'">引用 ${cites.length} 处 ▾</div>
    <div class="mc-cites" style="font-size:11px;color:var(--text-3);display:none;margin-top:4px">
    ${cites.map(c => `· ${esc(c.doc_name)} p${c.page}`).join("<br>")}</div>`;
}
async function restoreMini(sid) {
  const box = $("#mc-msgs");
  try {
    const s = await api(`/api/sessions/${sid}`);
    if (s.id !== sid) throw 0;
    box.innerHTML = "";
    for (const m of s.messages) {
      const b = document.createElement("div");
      b.className = "m " + (m.role === "user" ? "u" : "a");
      if (m.role === "user") b.textContent = m.content;
      else {
        const meta = m.meta || {};
        b.innerHTML = `${renderAnswerHTML(m.content, meta.verdict, true)}
          ${miniCitesHTML(meta.citations || [])}
          ${renderProvenance(meta.provenance || [], true)}`;
      }
      box.appendChild(b);
    }
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    delete state.mini.sessions[miniKey()];
    miniWelcome();
  }
}
async function miniAsk() {
  const q = $("#mc-q").value.trim();
  if (!q || state.mini.running) return;
  state.mini.running = true;
  state.mini.runningLibId = state.mini.libId;
  const key = miniKey();
  state.mini.runningKey = key;
  $("#mc-send").disabled = true;
  const box = $("#mc-msgs");
  ensureScrollGuard(box);
  box._paused = false;
  const tip = box.querySelector(".muted"); if (tip) tip.remove();
  const u = document.createElement("div"); u.className = "m u";
  u.textContent = q;
  box.appendChild(u);
  const a = document.createElement("div"); a.className = "m a";
  a.innerHTML = `<span class="pulse"></span> 检索中…`;
  box.appendChild(a);
  box.scrollTop = box.scrollHeight;
  $("#mc-q").value = "";
  let acc = "";
  const sc = state.mini.scope || {};
  const reqBody = {
    space_id: state.space, mode: "kb", question: q, panel: true,
    session_id: state.mini.sessions[key] || null,
    library_ids: [state.mini.libId], tag_ids: [],
    doc_ids: sc.docId ? [sc.docId] : [],
    focus: sc.type === "selection" ? {
      document_id: sc.docId, db_id: sc.dbId, page: sc.page,
      selection_text: sc.selection, selection_before: sc.before,
      selection_after: sc.after,
    } : null,
  };
  try {
    await sseStream("/api/qa/stream", reqBody, ev => {
      if (ev.type === "start") state.mini.sessions[key] = ev.session_id;
      else if (ev.type === "answer") {
        acc += ev.delta;
        const follow = canFollow(box);
        a.innerHTML = renderAnswerHTML(acc, null, true);
        if (follow) box.scrollTop = box.scrollHeight;
      } else if (ev.type === "result") {
        const finalText = ev.answer || acc;
        const follow = canFollow(box);
        a.innerHTML = `${renderAnswerHTML(finalText, ev.verdict, true)}
          ${miniCitesHTML(ev.citations || [])}
          ${renderProvenance(ev.provenance || [], true)}`;
        if (follow) box.scrollTop = box.scrollHeight;
      } else if (ev.type === "error") {
        a.innerHTML = `<span style="color:var(--error)">${esc(ev.message)}</span>`;
      }
    });
  } catch (e) {
    a.innerHTML = `<span style="color:var(--error)">${esc(e.message)}</span>`;
  }
  state.mini.running = false;
  state.mini.runningLibId = null;
  state.mini.runningKey = null;
  $("#mc-send").disabled = false;
  loadHistory();
}

/* ══════════ 演进中心 ══════════ */
async function renderEvolve() {
  const v = $("#view");
  if (!state.space) { v.innerHTML = `<div class="empty">先创建空间</div>`; return; }
  const [cands, rules, digest, qa, runs] = await Promise.all([
    api(`/api/spaces/${state.space}/candidates?status_=pending`),
    api(`/api/spaces/${state.space}/rules`),
    api(`/api/spaces/${state.space}/digest`),
    api(`/api/spaces/${state.space}/qa_history`),
    api(`/api/spaces/${state.space}/distill_runs`)]);
  $("#topbar-actions").innerHTML =
    `<button class="btn" onclick="doDistill()">🧪 立即蒸馏部门认知</button>`;
  v.innerHTML = `<div class="panes">
    <div class="pane">
      <div class="ph">待审核候选 <span class="count">${cands.length}</span>
        <span style="flex:1"></span><span class="muted" style="font-size:11px;font-weight:400">审核通过后才会进入问答提示词</span></div>
      <div class="pb" id="cand-box">${cands.length ? cands.map(c => `
        <div class="cand" data-id="${c.id}">
          <div class="st">${esc(c.statement)}</div>
          <div class="ev">
            <span class="badge info" style="font-size:10px">${esc(sourceHuman(c.source))}</span>
            ${/repeat:R\d+/.test(c.source || "") ? '<span class="badge warn" style="font-size:10px">重复出现，将更新既有规则</span>' : ""}
            ${c.times_seen > 1 ? `<span class="badge gray" style="font-size:10px">出现 ${c.times_seen} 次</span>` : ""}
            <span class="muted"> · 类型 ${esc(c.kind)}</span>
          </div>
          <div class="acts">
            <button class="btn sm primary" onclick="approveCand('${c.id}')">✓ 通过</button>
            <button class="btn sm" onclick="editCand('${c.id}', this)" data-st="${esc(c.statement)}">✎ 改写后通过</button>
            <button class="btn sm" onclick="showEvidence('${c.id}')">查看证据</button>
            <button class="btn sm danger" onclick="rejectCand('${c.id}')">✕ 拒绝</button>
          </div>
        </div>`).join("")
        : `<div class="empty" style="padding:24px">暂无候选。多上传会议纪要、多用纠正反馈后会自动产生。</div>`}</div>
    </div>
    <div class="pane">
      <div class="ph">部门规则（已生效）<span class="count">${rules.length}</span></div>
      <div class="pb">${rules.map(r => `<div class="dv-row">
        <span class="mono">R${r.id}</span><span style="flex:1">${esc(r.statement)}</span>
        <span class="badge gray" style="font-size:10px">命中 ${r.hits || 0}</span>
        ${r.corrections ? `<span class="badge warn" style="font-size:10px">纠偏 ${r.corrections}</span>` : ""}
        <button class="btn sm ghost" onclick="delRule(${r.id})">移除</button></div>`).join("")
        || '<span class="muted">还没有规则</span>'}
        <div class="mt8 flex gap8"><input class="input" id="manual-rule" placeholder="手动新增一条部门规则（直接生效）" style="flex:1">
        <button class="btn" onclick="addManualRule()">添加</button></div></div>
    </div>
  </div>
  <div class="panes" style="margin-top:18px">
    <div class="pane">
      <div class="ph">知识发行版 digest
        ${digest.current ? `<span class="count">v${digest.current.ver}</span>
          <span class="muted" style="font-size:11px;font-weight:400">${digest.current.bytes}B · nonce ${digest.current.nonce}</span>`
          : `<span class="muted" style="font-size:11px;font-weight:400">未生成</span>`}
        <span style="flex:1"></span>
        ${digest.current ? `<button class="btn sm" onclick="showDigest()">查看</button>` : ""}
        <button class="btn sm" onclick="showAllDigests()">全部版本 (${digest.versions.length})</button></div>
      <div class="pb">
        <div id="distill-bar"></div>
        <div class="help mb8">每次问答都会把当前生效 digest 注入提示词——这就是“越用越懂你们部门”的载体。超过 20KB 会拒绝渲染并保留旧版。</div>
        ${digest.versions.slice(0, 5).map(dv => `<div class="dv-row">
          <span class="mono">v${dv.ver}</span>
          <span>${dv.active ? '<span class="badge ok">生效中</span>' : ""}</span>
          <span class="muted">${dv.bytes}B · ${esc(dv.created_at)}</span>
          <span style="flex:1"></span>
          ${dv.active ? "" : `<button class="btn sm" onclick="rollbackDigest('${dv.id}')">回滚</button>`}
        </div>`).join("")}</div>
    </div>
    <div class="pane">
      <div class="ph">问答反馈记录<span class="count">${qa.length}</span>
        <span style="flex:1"></span>
        <button class="btn sm" onclick="showAllQa()">全部记录</button></div>
      <div class="pb">${qa.slice(0, 7).map(x => `<div class="dv-row">
        <span class="badge ${VERDICT_CLS[x.verdict] || "info"}">${esc(x.verdict || "-")}</span>
        <span style="flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(x.question)}</span>
        ${x.feedback === "fix" ? '<span class="badge warn">已纠正</span>'
          : x.feedback === "ok" ? '<span class="badge ok">满意</span>' : ""}
      </div>`).join("") || '<span class="muted">还没有问答记录</span>'}</div>
    </div>
  </div>
  <div class="panes" style="margin-top:18px">
    <div class="pane">
      <div class="ph">开发经验（Claude Code）
        <span class="badge gray" style="font-size:10px">可选 · 本地读取</span></div>
      <div class="pb" id="cc-box"><span class="muted">加载中…</span></div>
    </div>
    <div class="pane">
      <div class="ph">蒸馏历史<span class="count">${runs.length}</span></div>
      <div class="pb">${runs.slice(0, 5).map(r => `<div class="dv-row">
        <span class="badge ${r.status === "done" ? "ok" : r.status === "failed" ? "err" : "warn"}">${esc(r.status)}</span>
        <span style="flex:1">${esc(r.detail || "")}</span>
        <span class="muted">新增 ${r.candidates_added || 0} 条 · ${esc((r.finished_at || r.started_at || "").slice(5, 16))}</span>
      </div>`).join("") || '<span class="muted">还没有蒸馏记录</span>'}</div>
    </div>
  </div>`;
  renderCC();
}

function sourceHuman(src) {
  if (!src) return "来源未知";
  if (src.startsWith("ingest:")) return "来自文档入库";
  if (src.startsWith("qa_fix:")) return "来自问答纠正";
  if (src.startsWith("cc:")) return "来自 Claude Code 会话";
  if (src.startsWith("manual")) return "手动添加";
  if (src.includes("repeat:R")) return "重复出现的规则";
  return "自动抽取";
}

async function showEvidence(cid) {
  let e;
  try { e = await api(`/api/candidates/${cid}/evidence`); }
  catch (err) { return toast("证据加载失败：" + err.message, "err"); }
  openModal("候选证据", `
    <div style="font-size:13.5px;margin-bottom:12px">${esc(e.statement)}</div>
    ${e.evidence.length ? e.evidence.map(x => `
      <div class="cand" style="margin-bottom:10px">
        <div class="ev">${x.type === "document" ? "📄 文档"
          : x.type === "qa" ? "💬 问答记录" : "🧑‍💻 开发会话"} · ${esc(x.title)}</div>
        <div class="md" style="font-size:12.5px">${md2html(x.excerpt || "").html}</div>
      </div>`).join("") : '<div class="muted">该候选没有可回放的证据。</div>'}`,
    `<button class="btn" onclick="closeModal()">关闭</button>`);
}

async function renderCC() {
  const box = $("#cc-box");
  if (!box) return;
  let rows = [];
  try { rows = await api(`/api/spaces/${state.space}/cc/projects`); }
  catch (e) { box.innerHTML = `<span class="muted">加载失败：${esc(e.message)}</span>`; return; }
  box.innerHTML = (rows.map(p => `
    <div class="dv-row">
      <span class="mono">${esc(p.name)}</span>
      <span style="flex:1;font-size:11.5px;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(p.path)}</span>
      <span class="badge gray" style="font-size:10px">${p.session_count} 个会话</span>
      ${p.injected ? '<span class="badge ok" style="font-size:10px">已注入</span>' : ""}
      <button class="btn sm" onclick="ccImport('${p.id}')">导入最近会话</button>
      <button class="btn sm" onclick="ccInject('${p.id}','${p.injected ? "uninstall" : "install"}')">${p.injected ? "取消注入" : "注入发行版"}</button>
      <button class="btn sm ghost" onclick="ccRemove('${p.id}')">移除</button>
    </div>`).join("") || '<span class="muted">尚未添加项目。添加你使用 Claude Code 的代码目录，即可导入开发经验。</span>')
    + `<div class="mt8 flex gap8">
        <input class="input" id="cc-path" placeholder="代码项目路径，例如 D:\\work\\my-project" style="flex:1">
        <button class="btn" onclick="ccAdd()">添加项目</button>
      </div>
      <div class="help mt8">仅读取本机 Claude Code 会话；导入时自动脱敏。注入会写入项目的
        .claude/settings.local.json（自动备份，可取消）。</div>`;
}

async function ccAdd() {
  const path = $("#cc-path").value.trim();
  if (!path) return toast("请填写项目路径", "err");
  try {
    await jpost(`/api/spaces/${state.space}/cc/projects`, { path });
    toast("项目已添加", "ok"); renderCC();
  } catch (e) { toast("添加失败：" + e.message, "err"); }
}
async function ccImport(pid) {
  toast("正在导入并蒸馏，请稍候…");
  try {
    const r = await jpost(`/api/cc/projects/${pid}/import`, { limit: 3 });
    const n = (r.imported || []).reduce((a, x) => a + (x.candidates || 0), 0);
    toast(`导入 ${(r.imported || []).length} 个会话，生成候选 ${n} 条`, "ok");
    renderEvolve();
  } catch (e) { toast("导入失败：" + e.message, "err"); }
}
async function ccInject(pid, action) {
  const msg = action === "install"
    ? "将修改该项目的 .claude/settings.local.json 并自动备份，继续？"
    : "取消该项目的发行版注入？";
  if (!confirm(msg)) return;
  try {
    await jpost(`/api/cc/projects/${pid}/inject`, { action });
    toast(action === "install" ? "已注入，下次 Claude Code 会话生效" : "已取消注入", "ok");
    renderCC();
  } catch (e) { toast("操作失败：" + e.message, "err"); }
}
async function ccRemove(pid) {
  if (!confirm("移除该项目？已导入的候选不受影响。")) return;
  await api(`/api/cc/projects/${pid}`, { method: "DELETE" });
  renderCC();
}

async function approveCand(id) {
  await jpost(`/api/candidates/${id}/approve`, {});
  toast("已晋升并更新发行版", "ok"); renderEvolve();
}
async function rejectCand(id) {
  await jpost(`/api/candidates/${id}/reject`, {}); renderEvolve();
}
function editCand(id, btn) {
  openModal("改写后晋升", `
    <div class="field"><label>规则陈述</label><textarea class="input" id="ed-st" rows="2">${esc(btn.dataset.st)}</textarea></div>
    <div class="field"><label>反例（可选）</label><input class="input" id="ed-bad"></div>
    <div class="field"><label>正例（可选）</label><input class="input" id="ed-good"></div>`,
    `<button class="btn" onclick="closeModal()">取消</button>
     <button class="btn primary" onclick="doEditApprove('${id}')">确认晋升</button>`);
}
async function doEditApprove(id) {
  await jpost(`/api/candidates/${id}/approve`, {
    statement: $("#ed-st").value.trim(),
    ex_bad: $("#ed-bad").value.trim(), ex_good: $("#ed-good").value.trim() });
  closeModal(); toast("已按改写晋升", "ok"); renderEvolve();
}
async function addManualRule() {
  const st = $("#manual-rule").value.trim();
  if (!st) return;
  await jpost(`/api/spaces/${state.space}/rules`, { statement: st });
  $("#manual-rule").value = ""; renderEvolve();
}
async function delRule(rid) {
  if (!confirm("移除该规则？")) return;
  await api(`/api/rules/${rid}?space_id=${state.space}`, { method: "DELETE" });
  renderEvolve();
}
const DISTILL_STAGES = { gather: 25, model: 60, render: 85, done: 100 };
async function doDistill() {
  let r;
  try { r = await jpost(`/api/spaces/${state.space}/distill`); }
  catch (e) { return toast("启动蒸馏失败：" + e.message, "err"); }
  const bar = $("#distill-bar");
  if (bar) {
    bar.innerHTML = `<div class="dw-progress"><span class="pulse"></span>
      <span style="flex:1"><b id="db-stage">正在启动蒸馏…</b>
        <span class="muted" id="db-detail"></span></span>
      <span class="muted" id="db-elapsed">0s</span></div>
      <div class="prog-bar" style="width:100%;margin:-4px 0 12px">
        <i id="db-fill" style="width:5%"></i></div>`;
  }
  state._distillT0 = Date.now();
  clearInterval(state._distillTimer);
  // 记录游标：只取本空间启动之后产生的 distill_progress 事件
  let cursor = (await api(`/api/events?space_id=${state.space}&since=0`)).last || 0;
  state._distillTimer = setInterval(async () => {
    try {
      const r2 = await api(`/api/events?space_id=${state.space}&since=${cursor}`);
      cursor = r2.last;
      const dp = r2.events.filter(e => e.type === "distill_progress");
      for (const e of dp) setDistillBar(e.payload.stage, e.payload.detail);
      const el = $("#db-elapsed");
      if (el && !["done", "failed"].includes(dp[dp.length - 1]?.payload?.stage))
        el.textContent = Math.round((Date.now() - state._distillT0) / 1000) + "s";
      if (dp.some(e => ["done", "failed"].includes(e.payload.stage))) {
        clearInterval(state._distillTimer);
        const failed = dp.find(e => e.payload.stage === "failed");
        if (failed) toast("蒸馏失败：" + failed.payload.detail, "err");
        else toast("蒸馏完成", "ok");
        setTimeout(() => renderEvolve(), 800);
      }
    } catch (e) { clearInterval(state._distillTimer); }
  }, 900);
  if (!r.started) { if (bar) setDistillBar("gather", "（已有蒸馏任务在运行，进度如下）"); }
}
function setDistillBar(stage, detail) {
  const s = $("#db-stage"), d = $("#db-detail"), f = $("#db-fill");
  const map = { gather: "① 收集材料", model: "② 模型整理认知",
    render: "③ 重渲染发行版", done: "✓ 完成", failed: "✕ 失败" };
  if (s) s.textContent = map[stage] || stage;
  if (d) d.textContent = "　" + (detail || "");
  if (f) f.style.width = (DISTILL_STAGES[stage] || 5) + "%";
}
async function showAllDigests() {
  const d = await api(`/api/spaces/${state.space}/digest`);
  openModal("全部发行版版本", (d.versions || []).map(v => `<div class="dv-row">
    <span class="mono">v${v.ver}</span>
    <span>${v.active ? '<span class="badge ok">生效中</span>' : ""}</span>
    <span class="muted">${v.bytes}B · ${esc(v.created_at)}</span>
    <span style="flex:1"></span>
    ${v.active ? "" : `<button class="btn sm" onclick="rollbackDigest('${v.id}')">回滚</button>`}
  </div>`).join("") || '<div class="muted">暂无版本</div>',
    `<button class="btn" onclick="closeModal()">关闭</button>`);
}

async function showAllQa() {
  const rows = await api(`/api/spaces/${state.space}/qa_history`);
  openModal("全部问答记录", rows.map(x => `<div class="dv-row">
    <span class="badge ${VERDICT_CLS[x.verdict] || "info"}">${esc(x.verdict || "-")}</span>
    <span style="flex:1">${esc(x.question)}</span>
    <span class="muted">${esc((x.created_at || "").slice(5, 16))}</span>
    ${x.feedback === "fix" ? '<span class="badge warn">已纠正</span>'
      : x.feedback === "ok" ? '<span class="badge ok">满意</span>' : ""}
  </div>`).join("") || '<div class="muted">暂无记录</div>',
    `<button class="btn" onclick="closeModal()">关闭</button>`);
}

async function showDigest() {
  const d = await api(`/api/spaces/${state.space}/digest`);
  state._digestContent = d.current.content || "";
  openModal(`知识发行版 v${d.current.ver}`,
    `<div class="digest-tabs">
       <button id="dg-md" class="on" onclick="setDigestView('md')">渲染</button>
       <button id="dg-raw" onclick="setDigestView('raw')">原始 Markdown</button>
     </div>
     <div id="digest-md" class="digest-md md">${md2html(state._digestContent).html}</div>
     <pre id="digest-raw" class="digest" style="display:none">${esc(state._digestContent)}</pre>`,
    `<button class="btn" onclick="closeModal()">关闭</button>`);
}
function setDigestView(mode) {
  const md = document.getElementById("digest-md");
  const raw = document.getElementById("digest-raw");
  document.getElementById("dg-md")?.classList.toggle("on", mode === "md");
  document.getElementById("dg-raw")?.classList.toggle("on", mode === "raw");
  if (md) md.style.display = mode === "md" ? "" : "none";
  if (raw) raw.style.display = mode === "raw" ? "" : "none";
}
async function rollbackDigest(did) {
  await jpost(`/api/spaces/${state.space}/digest/rollback`, { digest_id: did });
  toast("已回滚", "ok"); renderEvolve();
}

/* ── 全局拖拽上传 ── */
let dragDepth = 0;
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("dragenter", e => {
  e.preventDefault();
  if (state.view === "kb" && state.lib) { dragDepth++; $("#drop-mask").classList.add("show"); }
});
document.addEventListener("dragleave", () => {
  if (--dragDepth <= 0) { dragDepth = 0; $("#drop-mask").classList.remove("show"); }
});
async function collectDroppedFiles(dt) {
  const items = dt.items ? [...dt.items] : [];
  const entries = items.map(it => it.webkitGetAsEntry && it.webkitGetAsEntry())
    .filter(Boolean);
  if (!entries.length) return [...dt.files];
  const out = [];
  async function walk(entry) {
    if (entry.isFile) {
      try {
        out.push(await new Promise((res, rej) => entry.file(res, rej)));
      } catch (e) { /* skip unreadable */ }
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      while (true) {
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const en of batch) await walk(en);
      }
    }
  }
  for (const en of entries) await walk(en);
  return out;
}

document.addEventListener("drop", async e => {
  e.preventDefault(); dragDepth = 0;
  $("#drop-mask").classList.remove("show");
  if (state.view !== "kb" || !state.lib) {
    if (e.dataTransfer.items?.length) toast("请先进入一个库，再拖入文件或文件夹", "err");
    return;
  }
  let all = [];
  try { all = await collectDroppedFiles(e.dataTransfer); } catch (err) { all = [...e.dataTransfer.files]; }
  const files = all.filter(f => {
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    return UPLOAD_EXTS.has(ext);
  });
  if (!files.length) return toast("没有可上传的文件（支持 .md/.txt/.pdf/.docx/.pptx/.xlsx/图片/录音）", "err");
  const fd = new FormData();
  fd.append("space_id", state.space);
  fd.append("library_id", state.lib);
  fd.append("tag_ids", "[]");
  fd.append("vision", "1");
  for (const f of files) fd.append("files", f);
  try {
    await api("/api/documents/upload", { method: "POST", body: fd });
    toast(`已接收 ${files.length} 个文件，正在排队索引（已完成 0/${files.length}）`, "ok");
    state.docs = await api(`/api/libraries/${state.lib}/documents`);
    drawDocTable(); pollProgress();
  } catch (err) { toast("拖拽上传失败：" + err.message, "err"); }
});

boot();
