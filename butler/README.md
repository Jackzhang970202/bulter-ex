# 经验管家 butler

本地 AI 工作经验沉淀系统（Claude Code CLI · 单机 · 手动复盘）。
设计文档见上级目录《经验管家-设计与实施计划.md》。

## 快速开始
```bash
pip install -r requirements.txt
python -m butler init            # 建数据目录与账本
python -m butler run             # 常驻：采集 + 大屏(http://127.0.0.1:8420) + 托盘
# 新项目接入（二选一）：
python -m butler connect D:\你的项目   # 直接接
#   或在任意会话干活后，大屏"项目接入"区点【接入】
python -m butler distill         # 手动复盘（等价于大屏/托盘按钮）
python -m butler export          # 导出纯 markdown 经验包（其他宿主/逃生舱）
python -m butler status          # 命令行体检
python -m butler test            # 全量测试
python -m butler autostart on    # 开机自启（计划任务 ButlerExperience，可 off）
python -m butler rehook          # 本文件夹搬家后重写所有项目 hook 路径
python -m butler uninstall --all # 摘除全部注入
```

## 日常用法（全部触点）
- **正常使用 Claude Code，不需要任何口令**——程序被动记录所有会话，大屏/托盘点"**现在复盘**"时自动从上次复盘以来的全部会话内容里提炼经验候选（同义自动合并；现有规则被反复违反会自动转成"改写建议"并计数）
- 候选在大屏**审批**（每条带证据回放，可改后批准）；批准即入库并重渲染发行版
- 可选实时捷径：想当场锁定某条纠正时，对 AI 说"**记入规则**"（写进候选，和蒸馏结果一起审批；不说完全没问题）
- 已开会话同步新规则：等它下次 compact/resume，或会话里说"**重新读发行版**"
- 不想被记录：托盘/大屏"**暂停采集 2h**"

## 架构一句话
托盘程序（采集/蒸馏/渲染）+ SQLite 账本 + 静态发行版文件经项目级 SessionStart hook
（只写 `.claude/settings.local.json`）注入 + 本地大屏 + markdown 导出逃生舱。
程序未运行时，已接入项目的规则注入照常工作（hook 只读静态文件）。

## 目录
`butler/` 源码 · `data/` 账本与发行版（可整体拷贝=备份）· `tests/` 全量测试。

## 分发给同事
- **给程序不给数据**：拷 `butler.exe + _internal/ + README.md` 打包即可（绿色免安装，
  首次运行自动在同级创建 `data/`）；`data/` 是各自私有的账本/画像/会话归档，互不共享
- 对方机器前提：装了并登录着 Claude Code CLI（蒸馏依赖 `claude -p`）
- 想共享团队共识规则：用 `butler export` 导出的纯 markdown 经验包传递，不要传数据库

## token 数字怎么读
- 口径 = transcript 里每次 API 调用的 usage 求和（**含缓存读**，这是真实计费口径，
  远大于"你打的字"；官方 `/cost` 同款口径）
- 归属 = 按消息自身时间戳归日（修复过"首次全量扫描全记成今天"的错误归日）
- $ 仅当模型名命中内置价目表才显示；网关自定义模型只报 token 不编造价

## 实机验收记录（2026-10-15）
- ✅ 单元测试 23/23 通过（`python -m butler test`），覆盖计划 §10 各阶段验收含去重
- ✅ `butler init` 建库与目录；`butler connect` 写入 `_hooktest/.claude/settings.local.json`
  （SessionStart hook，matcher 覆盖 startup|resume|clear|compact|fork）
- ✅ **最后一英里真实验证**：在已接入目录跑真实 `claude -p`，新会话从上下文准确复述
  发行版 nonce（8f825b9e）→ 注入链路 hook.cmd→type→stdout 端到端可用（$0.17）
- ✅ inject.log 正常记录（`ok <hash> <日期> <时间>`）
- ✅ 程序未运行时会话照常启动（hook 只读静态文件）；settings.local.json 合并语义实测：
  已有用户 hook 条目原样保留、我方条目幂等更新、uninstall 只摘我方
- ✅ 发行版含规则区/画像索引/可选指令区/优先级声明，体积守卫生效（>上限拒渲保旧版）
- ✅ 去重三级实测：同条 pending 合并(times_seen/证据并集)；已批准再现→自动转"改写建议"
  并给规则计 corrections；已拒绝永久静默；画像/过滤同义静默丢弃
- ✅ 常驻进程大屏 HTTP 实测：/api/state 26 键正常、采集心跳 3s 前、注入统计
  ok=1/miss=0、index.html 24KB 正常返回（后台运行期间验收，之后停止）
- ✅ **端到端主链路全通**：真实数据小预算复盘（60K 字符）→ 无头 claude -p 蒸馏
  （input_tokens 64K，status ok）→ 提炼 **8 条真实候选**（4 规则/1 画像/2 过滤/1 改写，
  每条带 4~10 个材料证据引用；蒸馏小结准确识别"材料交付/方案澄清"两类摩擦）→
  批准 1 条规则 T1 → 重渲染 v4 → **真实新会话逐字复述出 T1 全文**（$0.18）
  ——"正常使用零口令 → 复盘 → 审批 → 注入生效"闭环成立
- ✅ 复盘进行中新对话不受影响（无头独立进程 + 水位快照，材料定格；其余 2.1 亿字节
  滚存标记正确）
- ✅ 修复两个实测揪出的真 bug：sessions 表缺 archived 列导致采集每轮 IndexError
  ×8060（已加列+迁移，错误计数止增）；queue-operation 等合法行被误判漂移致
  schema_drift ×26（解析器新增 skip 行型，未知率回归正常）
- ✅ **exe 打包成功并实机验证**：`butler.exe`（onedir 绿色文件夹）跑 `run` 常驻——
  大屏在线、采集心跳活、24KB 页面正常。修掉 PyInstaller+conda 的 `_ssl` DLL 漏收
  （打包命令现自动带上 libssl/libcrypto）；GBK 终端中文乱码也修（控制台代码页自适应）
- ✅ 历史项目刷屏修复：待确认队列只显示近 3 天有新增活动的会话所在项目（曾一次出现 100 个）
- ⏳ 无害残留：`butler/_hooktest/` 测试目录句柄未释放暂删不掉，稍后手动删除即可
- ⏳ 剩余 **7 条真实候选在大屏待审**——打开 http://127.0.0.1:8420 就是你的第一批经验库存

## 现在就开始用（推荐路径）
1. 双击 `butler.exe`（或命令行 `butler.exe run`）→ 托盘出现图标 + 自动开大屏
   （exe 在 butler/ 目录下；`--no-tray` 为无托盘后台模式）
2. 大屏上把 7 条待审候选逐条看过（每条点"证据"可回放原话）→ 批准你想要的
3. 在任意项目里执行一次接入：`butler.exe connect D:\你的项目`（或干活时等托盘弹确认）
4. 之后就是循环：正常干活 → 攒几天点一次"现在复盘" → 审批 → 规则自动注入所有已接入会话
5. 可选：`butler.exe autostart on` 开机自启
