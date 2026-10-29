/* KnowLens UI dictionary + live language switch.
 *
 * UI chrome is centralised here; user/department content (document names,
 * agent prompts, skill descriptions) intentionally stays in its source
 * language. Switching walks the rendered DOM and replaces exact UI phrases,
 * then keeps watching for re-renders. */
(function () {
  "use strict";

  const EN = {
    "驾驶舱": "Cockpit",
    "知识空间": "Knowledge",
    "智能问答": "Assistant",
    "智能体": "Agents",
    "技能市场": "Skills",
    "专家广场": "Experts",
    "团队协作": "Teams",
    "工作流": "Workflows",
    "自动化": "Automation",
    "运营后台": "Operations",
    "经验中心": "Evolution",
    "设置": "Settings",
    "空间总览与知识健康": "Space overview and knowledge health",
    "空间、库与文档": "Spaces, libraries and documents",
    "基于资料回答，逐句带出处": "Grounded answers with citations",
    "可配置、可测试、可发布的智能体": "Configurable, testable, publishable agents",
    "1100+ 开源技能与自定义技能": "1,100+ open skills plus your own",
    "把智能体包装成可咨询的专家": "Package agents as discoverable experts",
    "多智能体圆桌、调度与交接": "Round-table, supervisor and handoff teams",
    "可视化编排、审批与运行追踪": "Typed DAGs, approvals and run traces",
    "定时任务与运行产物": "Scheduled runs and artifacts",
    "概览、审核、审计与权限": "Overview, review, audit and access",
    "候选、规则与发行版": "Candidates, rules and releases",
    "模型、存储与外观": "Models, storage and appearance",
    "搜索名称、描述、标签": "Search name, description, tags",
    "全部状态": "All statuses",
    "草稿": "Draft",
    "待审核": "In review",
    "已发布": "Published",
    "已下线": "Offline",
    "＋ 新建智能体": "+ New agent",
    "从模板创建": "From template",
    "对话": "Chat",
    "详情": "Details",
    "测试": "Test",
    "＋ 自定义技能": "+ Custom skill",
    "导入链接": "Import URL",
    "浏览开源目录": "Browse catalog",
    "已安装": "Installed",
    "开源技能目录": "Open catalog",
    "技能包": "Skill packs",
    "全部分类": "All categories",
    "仅宽松许可": "Permissive licence only",
    "加载更多": "Load more",
    "安装": "Install",
    "＋ 创建专家": "+ New expert",
    "咨询": "Consult",
    "＋ 新建团队": "+ New team",
    "运行": "Run",
    "编辑": "Edit",
    "删除": "Delete",
    "＋ 新建工作流": "+ New workflow",
    "定时任务": "Schedules",
    "运行产物": "Artifacts",
    "＋ 新建定时任务": "+ New schedule",
    "立即运行": "Run now",
    "记录": "History",
    "启用": "Enable",
    "停用": "Disable",
    "概览": "Overview",
    "审核队列": "Review queue",
    "通知": "Notifications",
    "审计日志": "Audit log",
    "用户与权限": "Users and access",
    "产物": "Artifacts",
    "导出问答 CSV": "Export Q&A CSV",
    "清理 90 天前运行": "Prune runs older than 90 days",
    "导出平台数据": "Export platform data",
    "全部标记已读": "Mark all read",
    "已读": "Read",
    "保存": "Save",
    "取消": "Cancel",
    "关闭": "Close",
    "应用": "Apply",
    "刷新目录": "Refresh catalog",
    "应用技能包": "Apply pack",
    "＋ 新建技能包": "+ New pack",
    "搜索 1100+ 开源技能": "Search 1,100+ open skills",
    "搜索文档、智能体、技能、专家、团队、工作流、会话…": "Search documents, agents, skills, experts, teams, workflows, sessions…",
    "↑↓ 选择 · Enter 打开 · Esc 关闭 · Ctrl/Cmd+K 唤起": "↑↓ select · Enter open · Esc close · Ctrl/Cmd+K",
    "还没有符合条件的智能体": "No agents match",
    "新建一个，或调整筛选条件": "Create one or adjust the filters",
    "还没有安装技能": "No skills installed",
    "从开源目录安装，或创建自定义技能": "Install from the catalog or create your own",
    "还没有技能包": "No skill packs yet",
    "新建一个，把常用技能打包": "Create one to bundle common skills",
    "还没有专家": "No experts yet",
    "把一个已发布的智能体包装成可检索的专家": "Publish an agent as a discoverable expert",
    "还没有团队": "No teams yet",
    "把多个智能体组合成圆桌、主管调度或交接流程": "Combine agents into a team",
    "还没有工作流": "No workflows yet",
    "用模板创建一个：知识简报、评审或条件分流": "Start from a template",
    "还没有定时任务": "No schedules yet",
    "让智能体或工作流按 cron 自动运行": "Run agents or workflows on a cron",
    "没有通知": "No notifications",
    "运行失败时会在这里提醒": "Failures will appear here",
    "提交审核": "Submit for review",
    "审核通过": "Approve",
    "驳回": "Reject",
    "发布": "Publish",
    "下线": "Take offline",
    "重新编辑": "Reopen",
    "回滚": "Roll back",
    "评测": "Evaluate",
    "角色设定": "Persona",
    "绑定": "Bindings",
    "版本": "Versions",
    "状态": "Status",
    "运行次数": "Runs",
    "成功率": "Success rate",
    "技能": "Skills",
    "工具": "Tools",
    "记忆": "Memory",
    "知识": "Knowledge",
    "团队成员": "Members",
    "最大轮次": "Max turns",
    "协作策略": "Strategy",
    "综合智能体": "Synthesizer",
    "输入": "Input",
    "名称": "Name",
    "描述": "Description",
    "类型": "Type",
    "分类": "Category",
    "标签": "Tags",
    "目标": "Target",
    "启用记忆注入": "Inject memory",
    "知识空间范围": "Knowledge scope",
    "工具权限": "Tool permissions",
    "温度": "Temperature",
    "最大推理步数": "Max steps",
    "模型": "Model",
    "中文": "中文",
  };

  const REV = {};
  Object.keys(EN).forEach((k) => { if (!(EN[k] in REV)) REV[EN[k]] = k; });

  const STORAGE = "knowlens-lang";
  let lang = localStorage.getItem(STORAGE) || "zh";
  let applying = false;
  let timer = null;

  function table() { return lang === "en" ? EN : REV; }

  function translateNode(node) {
    const raw = node.nodeValue;
    if (!raw) return;
    const key = raw.trim();
    if (!key) return;
    const hit = table()[key];
    if (!hit) return;
    node.nodeValue = raw.replace(key, hit);
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { translateNode(root); return; }
    if (!root.querySelectorAll) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        const p = n.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        if (p.closest("script,style,textarea,code,pre")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(translateNode);
    root.querySelectorAll("[placeholder],[title],[aria-label]").forEach((n) => {
      ["placeholder", "title", "aria-label"].forEach((attr) => {
        const v = n.getAttribute(attr);
        if (!v) return;
        const hit = table()[v.trim()];
        if (hit) n.setAttribute(attr, hit);
      });
    });
  }

  function apply() {
    if (applying) return;
    applying = true;
    try {
      walk(document.body);
      document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    } finally {
      applying = false;
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(apply, 60);
  }

  function set(next) {
    lang = next === "en" ? "en" : "zh";
    localStorage.setItem(STORAGE, lang);
    apply();
    if (window.KP && window.KP.rerender) window.KP.rerender();
  }

  function current() { return lang; }

  const observer = new MutationObserver(schedule);
  function start() {
    ["#view", "#nav", "#topbar-actions", "#modal-back", "#toast-box"]
      .forEach((sel) => {
        const node = document.querySelector(sel);
        if (node) observer.observe(node, { childList: true, subtree: true, characterData: true });
      });
    apply();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.I18N = { set, current, apply,
    t: (zh) => (lang === "en" ? (EN[zh] || zh) : zh) };
})();