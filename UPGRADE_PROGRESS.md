# 升级开发进度跟踪

> **单一事实源**：跨对话、跨日期续开发时，先读本文件了解「现在到哪了」，收工前更新本文件。
> 配套：`DEVELOPMENT_PLAN.md`（总计划）、`docs/UPGRADE_DESIGN.md`（细化设计）、`docs/学习路线.md`（每阶段该学的框架 + 边做真项目边学）。
> 最后更新：2026-09-23

---

## 当前状态

- **当前阶段**：P9（收敛闭环 & 复测停滞检测）已完成（代码+单测）。P0-A/P0-B/P8/P5 此前完成。
- **整体进度**：5 / 12 阶段完成（代码+单测）。
- **待办（下次开工前）**：无阻塞项。注意 `examples/demo.py` 走的是 `analyze_only()`（只跑分析 Agent 的 MVP 便宜路径），不经 `run()`，故不产 trace.json / 停滞检测——这是预期；全流程行为由各端到端单测覆盖。
- **下一步阶段（按修订顺序）**：**P10 —— 运行审计目录 & 阶段门禁**（产物收拢进 `runs/run-{ts}/` + gates.json + fail-closed，参见 `docs/UPGRADE_DESIGN.md` P10 节；可并行，无前置依赖）。修订后完整顺序：P8 → P5 → P9 → **P10** → P1 → P2 → P3 → P4 → P6 → P7。

---

## 阶段看板

图例：⬜ 未开始 / 🔵 进行中 / ✅ 完成（自测通过）/ ⏸️ 阻塞

| 阶段 | 主题 | 状态 | 自测 | 完成日期 | 备注 |
|---|---|---|---|---|---|
| P0-A | 结构化输出：消除脆弱 JSON 解析 | ✅ | 63 passed | 2026-09-18 | FinalTool 机制；单测用假 client 覆盖 3 路径 |
| P0-B | 工具寻址 & 统一错误面 | ✅ | 71 passed | 2026-09-18 | ToolRole 枚举 + tool_for_role + normalize_result；不引入 ToolResult（忠于现状） |
| P1 | Harness 兜底架构 | ⬜ | - | - | |
| P2 | 平台适配层 PlatformAdapter | ⬜ | - | - | |
| P3 | 用例生成 Agent（从需求/代码） | ⬜ | - | - | |
| P4 | Golden-set 记忆库强化（档一） | ⬜ | - | - | 档二保留后续 |
| P5 | 可观测性 + **预算管控**（trace/token/耗时/成本硬闸） | ✅ | 99 passed | 2026-10-02 | core/observability（Tracer + RunBudget）；base.py 埋点 usage/耗时；Coordinator 事件多播 + 预算越界诚实收尾 + trace.json + 消耗概览 |
| P6 | MCP server 化 | ⬜ | - | - | 细化设计待 P5 后补 |
| P7 | 并行采集 & 方法论资产化 | ⬜ | - | - | 细化设计待 P6 后补 |
| P8 | 证据置信度门槛 & 防假绿 | ✅ | 87 passed | 2026-10-02 | confidence/evidence_sufficiency 两维 + Coordinator 分流 + 确定性复核横幅 + 落库 CONFIRMED/HYPOTHESIS 分流 |
| P9 | 收敛闭环 & 复测停滞检测 | ✅ | 108 passed | 2026-10-02 | 证据指纹 + 停滞检测；三类停机准则并列（证据充分/停滞/预算）；停滞强制转人工 + 报告收敛止损横幅；MAX_REPRODUCE_ROUNDS 退居兜底 |
| P10 | 运行审计目录 & 阶段门禁 | ⬜ | - | - | 2026-09-23 新增；runs/run-{ts}/ + gate fail-closed |

> **修订执行顺序（2026-09-23）**：P8 → P5 → P9 → P10 → P1 → P2 → P3 → P4 → P6 → P7。阶段编号只表身份不表顺序（P8/P9/P10 编号续接，不打乱既有引用）。

---

## 已确认决策（2026-09-18）

1. 执行顺序：P0-A → P0-B → P1 → P2 → P3 → P4 → P5 → P6 → P7。
2. P4 记忆库本轮做**档一**（工具化 + 打分强化）；档二（向量语义检索）保留为后续升级，接口预留扩展位。
3. P6 / P7 纳入本轮计划。
4. 每阶段动手前先出细化设计（已在 `docs/UPGRADE_DESIGN.md`）。
5. 每阶段完成后必须自测通过再进入下一阶段。

## 已确认决策（2026-09-23，追加）

> 背景：探索了公司开源 Agentic QE 平台 TestAgent-CC 后，提炼其「可靠性 + 可观测」工程纪律进本计划，作为秋招主打项目差异化亮点。本日只规划，不写代码。

6. **新增三根支柱**：P8 证据置信度门槛 & 防假绿 / P9 收敛闭环 & 停滞检测 / P10 运行审计目录 & 阶段门禁。
7. **P5 扩展**：并入预算管控（token 硬闸 + 墙钟止损，越界诚实收尾），与可观测性共用同一份 usage 采集。
8. **修订执行顺序**：可靠性四块前置——P8 → P5 → P9 → P10 → P1 → P2 → P3 → P4 → P6 → P7。
9. **边界**：不引入 TestAgent-CC 的多测试域广度；坚持「单域纵深 + 可信 Agent 纪律」定位。
10. **兼容红线**：新阶段保持 `workspace_path=None` / 未配预算时行为同当前（强回归保护）；旧数据缺新字段取安全默认。

---

## 基线（升级前的关键事实，2026-09-18 核对）

- 分层定位、5 subagent 协作、通用内核+插件、checkpoint/resume：**已实现**。
- harness 兜底：仅 `try/except` 记 `stage_errors`，无重试/退避/熔断——**待 P1 建**。
- 平台适配层：**无**，需求源/结果汇/触发器/认证未抽象——**待 P2 建**。
- 复测决策：`subagents.py::_extract_tail_json` 从 markdown 尾抠 JSON——**脆弱，待 P0-A 改**。
- 工具寻址：`ExecutorAgent._role_tool` 靠方法名字符串——**脆弱，待 P0-B 改**。
- 用例生成：`CaseAgent` 走模板，非从需求/代码生成——**待 P3 补**。
- golden-set 检索：tag 精确 + signature LIKE，无语义——**待 P4 强化**。
- 可观测性：~~无 token/耗时/工具集中收集~~——**P5 已建**（2026-10-02）：`core/observability` Tracer 旁路订阅事件流，产 trace.json + 报告消耗概览。
- 并行采集：`_run_parallel` 是 TODO 串行——**待 P7**。
- 证据置信度：~~`Diagnosis` 无 confidence/evidence_sufficiency，模型给结论即当定论~~——**P8 已建**（2026-10-02）：两维门槛 + Coordinator 分流，低置信/证据不足不落 CONFIRMED、转人工复核 + 报告横幅。
- 收敛准则：~~复测循环只有 `MAX_REPRODUCE_ROUNDS=2` 硬上限，无停滞检测~~——**P9 已建**（2026-10-02）：证据指纹 + 停滞检测，三类停机准则并列，硬上限退居兜底。
- 预算管控：~~无 token 硬闸/墙钟止损~~——**P5 已建**（2026-10-02）：`RunBudget` token 硬闸 + 墙钟止损，越界诚实收尾（跳过下游 LLM + 报告标注提前终止）。
- 运行审计：产物平铺在 workspace，无自包含运行目录 + 阶段 gate——**待 P10 建**。

---

## 环境 & 自测命令备忘

环境已核对（2026-09-18）：venv 在 `.venv`，解释器 Python 3.12.13，pytest 9.1.1，mock 变量为 `PLATFORM_MOCK=1`（`core/config.py:29`）。

```bash
# 项目根
cd D:/junior-to-senior/projects/ai-agent-test-platform

# 单元测试（全量）。Git Bash 下前置 mock 变量更稳
PLATFORM_MOCK=1 ./.venv/Scripts/python.exe -m pytest -q

# mock 端到端（不连设备）
PLATFORM_MOCK=1 ./.venv/Scripts/python.exe examples/demo.py
```

> **升级前基线：60 passed（2.34s）。** 每阶段自测门槛：新增该阶段单测 + 全量 pytest 不低于基线且全绿 + demo mock 跑通。

---

## 开发日志（每次开工/收工追加，最新在上）

### 2026-10-02（P9 完成：收敛闭环 & 复测停滞检测）
- **实现 P9**，按 `docs/UPGRADE_DESIGN.md` P9 节改动清单逐项落地：
  - `core/agents/coordinator.py`：新增 `_evidence_fingerprint(raw)`——把证据抽象成 (collector 集合, {(collector,status,payload键集,result键集)}) 的**结构性指纹**，刻意忽略时间戳/数值噪声，只认「有没有采到新种类证据」；新增 `_reproduce_should_stop(prev_fp,new_fp,diagnosis)`——三类停机准则收口，优先级 **budget > stall > sufficient > 继续**；新增 `_prepend_stall_banner()` 确定性收敛止损横幅。
  - **复测循环重构**：复测前记 `prev_fp`，`reproduce()` + `raw.extend()` 后算 `new_fp`，调 `_reproduce_should_stop`；`stall` → 置 `stall_aborted`/`stall_rounds`、emit `reproduce_stall`、结构化 `stage_errors`、break；`budget` → 复用 P5 诚实收尾。停滞收敛的在 `_gate_confidence` 前强制 `needs_human_review=True`。`MAX_REPRODUCE_ROUNDS` 保留为兜底上限。
  - **P8 咬合修正**：`_gate_confidence` 的 `needs_human_review` 赋值改为 **OR 既有值**（`= 既有 or low or insufficient or contradictory`），避免把 P9 上游已置的 True 覆盖回 False。
  - 报告侧：report 阶段按 `stall_aborted` 加收敛止损横幅，与 P5 预算止损、P8 复核横幅三种收尾表述并存。
- **决策（忠于设计）**：证据指纹走「结构性摘要」而非 payload 原值全量——否则每轮采集的时间戳必然让指纹不等、停滞永不触发。这是 P9 能真正生效的关键，已记入设计意图。
- **自测**：新增 `tests/test_reproduce_convergence.py`（9 例：指纹相同稳定相等/新 collector 不等/新 result 键不等；`_reproduce_should_stop` 四优先级；端到端同结构→stall 提前跳出+转人工+横幅、持续新证据→跑满上限不误判）；全量 **108 passed**（P5 后 99 + 新增 9），无回归；`examples/demo.py` mock exit=0。
- **P9 阶段完成。** 本轮将 P0+P8+P5+P9 一起提交。下一步：P10 运行审计目录 & 阶段门禁。

### 2026-10-02（P5 完成：可观测性 + 预算管控）
- **实现 P5**，按 `docs/UPGRADE_DESIGN.md` P5 节改动清单逐项落地：
  - **新建 `core/observability/`**：`budget.py`（`RunBudget`：token 硬闸 + 墙钟止损，start/add_usage/exceeded/reason/status，默认 None 不设限；token 优先于墙钟归因）、`tracer.py`（`Tracer`：订阅 on_event 累积 `LLMCall`/`ToolCall`，summary/dump trace.json；收 LLM usage 既累积又喂 budget，越界经 downstream 回放一次 `budget_exceeded`）、`__init__.py` 导出。
  - `core/config.py`：增 `max_total_tokens` / `max_wall_clock_s`（读环境变量 `MAX_TOTAL_TOKENS`/`MAX_WALL_CLOCK_S`，默认 None=不设限）。
  - `core/agents/base.py`：`run()` 每次 `create()` 计时 + emit `llm_call`（含 usage/duration/round，假 client 无 usage 时安全取 0）；工具执行计时，`tool_result` 事件补 `duration_s`/`ok`。
  - `core/agents/coordinator.py`：`__init__` 加 `budget` 参（显式优先，否则按 config 装配）；`run()` 开头建 Tracer + `on_event` **多播**（Tracer + 外部，下游零改动）+ `budget.start()`；analyze/复测/report 启动前查 `budget.exceeded()` → 越界诚实收尾（跳过 LLM、`stage_errors` 结构化记 `预算越界提前终止[...]`、emit `stage_skipped`、确定性产出中止报告 `_budget_abort_report`）；收尾 `tracer.dump(workspace/trace.json)` + `_append_consumption_overview` 把消耗概览附到报告末尾。
- **决策（忠于设计）**：预算越界时报告走**代码确定性兜底**（`_budget_abort_report`，不烧 LLM 硬出报告），与 P8 复核横幅同源——可靠性保证不依赖模型。Coordinator 内 `on_event` 多播用遮蔽同名闭包实现，`stage()`/子 Agent 调用全部自动经过 Tracer，无需改子 Agent 签名。
- **自测**：新增 `tests/test_observability.py`（7 例：Tracer 累积/dump/喂预算只通知一次 + 端到端 trace.json + 无 workspace 不落盘但有概览）、`tests/test_run_budget.py`（12 例：RunBudget 各分支 + 默认不设限回归 + 端到端越界跳过下游 LLM/标注止损 + 未设预算全跑完）；全量 **99 passed**（P8 后 87 + 新增 12），无回归；`examples/demo.py` mock exit=0（走 analyze_only，不经 run()，预期无 trace）。
- **P5 阶段完成，停下等确认再进 P9。** 下一步：P9 收敛闭环 & 复测停滞检测（P8/P5 依赖已就位）。

### 2026-10-02（P8 完成：证据置信度门槛 & 防假绿）
- **实现 P8**，按 `docs/UPGRADE_DESIGN.md` P8 节改动清单逐项落地：
  - `core/agents/subagents.py`：`DECISION_SCHEMA.properties` 增 `confidence`(high/medium/low) + `evidence_sufficiency`(sufficient/insufficient)，`required` 不变（旧兼容）；`DECISION_TOOL_INSTRUCTION` 补「证据不足不得填 high、须置 insufficient + need_reproduce=true」；`AnalysisAgent.run()` 填两字段（缺失取 medium/sufficient 安全默认）。`ReportAgent.run()` 把两维 + review_notice 透给报告 LLM。
  - `core/plugins/models.py`：`Diagnosis` 增 `confidence`/`evidence_sufficiency`/`needs_human_review` 三字段（默认 medium/sufficient/False，兼容旧数据）。
  - `core/agents/coordinator.py`：新增 `_gate_confidence()`（低置信/证据不足/矛盾信号任一→置 needs_human_review，emit `human_review_flagged`），analyze 落盘前调用；**Golden-set 写库收紧**——仅「高置信+证据充分+未标复核」才落 `CONFIRMED`，否则留档为 `HYPOTHESIS`（不假绿）；新增 `_prepend_review_banner()` 确定性在报告顶部插复核横幅（不依赖 LLM 自觉）；`_save/_load_diagnosis` 增三键（旧文件缺键取默认）。
- **决策（忠于现状的小调整）**：复核横幅做成**代码确定性兜底**（`_prepend_review_banner`），而非只靠 ReportAgent 的 prompt——防假绿是硬保证，不能依赖模型是否照做。已同步此设计意图到本日志。
- **自测**：新增 `tests/test_confidence_gating.py`（16 例，覆盖 schema/字段填充/四条分流分支/落盘往返/横幅/端到端 CONFIRMED vs HYPOTHESIS）；全量 **87 passed**（P0-B 后 71 + 新增 16），无回归；`examples/demo.py` mock 跑通（exit=0，LLM 真实 API、分析 Agent 正常经决策工具提交结论）。
- **P8 阶段完成，停下等确认再进 P5。** 下一步：P5 可观测性 + 预算管控。

### 2026-09-23（规划扩充：吸收 TestAgent-CC 工程纪律，只规划不开发）
- **背景**：探索了公司开源 Agentic QE 平台 `TestAgent-CC-v1.6.2`（13 Agent / 84 Skill，横跨 API/WebUI/移动/桌面/覆盖率）。结论：不抄它的多测试域广度（会稀释本项目单域纵深叙事），只提炼其「让 Agent 结论可信」的四条工程纪律，落到本平台。
- **对照现有代码核对了四个短板的真实基线**（读了 `subagents.py`/`base.py`/`coordinator.py`/`models.py`/`golden_set/index.py`+`models.py`）：置信度、收敛准则、预算、运行审计确为空白/偏弱，非臆测。
- **新增/扩充规划（三份文档联动更新）**：
  - **P8 证据置信度门槛 & 防假绿**：`DECISION_SCHEMA` 加 `confidence`/`evidence_sufficiency`，`Diagnosis` 加两字段 + `needs_human_review`，`Coordinator` 写库前分流（低置信不落 CONFIRMED、进人工复核）。改动最小、面试价值最高，列为下一步。
  - **P9 收敛闭环 & 复测停滞检测**：给复测循环加证据指纹 + 停滞检测，三类停机准则并列（证据充分/停滞/预算越界），`MAX_REPRODUCE_ROUNDS` 退居兜底。
  - **P10 运行审计目录 & 阶段门禁**：产物收拢进 `runs/run-{ts}/` + `gates.json`，执行空数据时 analyze 阶段 fail-closed 不产伪结论。
  - **P5 扩展**：并入 `RunBudget`（token 硬闸 + 墙钟止损，越界诚实收尾），与 Tracer 共用一份 usage 采集。
- **确认边界**：坚持「单域纵深（AIoT 声学分层定位）+ 可信 Agent 纪律」定位；所有新阶段保持向后兼容（未配 workspace/预算时行为同当前）。
- **文档更新**：`DEVELOPMENT_PLAN.md`（亮点盘点表 + 阶段总览表 + P8/P9/P10 详细节 + P5 扩展 + §5 决策）、`docs/UPGRADE_DESIGN.md`（P8/P9/P10 接口签名+改动清单+自测点 + P5 预算设计）、本文件。**本日无代码改动。**
- 下次开工：先跑真实 API demo 验 P0，再开 P8。

### 2026-09-18（P0-B 完成，P0 阶段收工）
- **实现 P0-B 工具寻址 & 统一错误面**：
  - 实施前核对现状，**调整了原设计**（已同步 `docs/UPGRADE_DESIGN.md` P0-B「范围调整」节）：现有工具已统一 `status:success/error`，无裸 error，故**不引入 `ToolResult` dataclass**（过度改动），改为轻量 `normalize_result()` 防御归一；保留 `success`/`error` 字面量不改 `ok`。真正脆弱点是角色寻址魔法字符串，本阶段聚焦消除它。
  - `core/tools/base.py`：新增 `ToolRole` 枚举（PLAYBACK/AUDIO/LOG）+ `normalize_result()`。
  - `core/tools/__init__.py`：导出 `ToolRole`、`normalize_result`。
  - `core/tools/registry.py`：`execute()` 出参经 `normalize_result` 归一（行为兼容）。
  - `core/plugins/base.py`：新增 `tool_for_role()`，默认回退旧方法名（现有插件零改动可用）。
  - `core/agents/subagents.py`：`ExecutorAgent._role_tool` 改用 `ToolRole` + `tool_for_role`，接受枚举或字符串（兼容旧测试）。
  - 新增 `tests/test_tool_addressing.py`（8 例）。
- **自测**：新测 8 passed；`test_executor_dispatch.py` 回归 8 passed；全量 **71 passed**（P0-A 后 63 + 新增 8），无回归。
- **P0 阶段（P0-A + P0-B）完成，本日开发到此。** 下次从 P1 Harness 开始；开工前先跑真实 API demo 验证 P0 的两项改造。

### 2026-09-18（P0-A 完成）
- **实现 P0-A 结构化输出改造**：
  - `core/agents/base.py`：新增 `FinalTool` dataclass；`LLMAgent` 支持 `final_tool`（收尾轮用 `tool_choice` 强制调用，强制单工具时关掉互斥的 thinking）+ 可注入 `client`（单测用假 client 绕开真实 API）；`run()` 返回值加 `final_tool_input`；结论工具不执行只回占位 tool_result 满足协议。
  - `core/agents/subagents.py`：`AnalysisAgent` 用 `FinalTool` + `DECISION_SCHEMA` 替换 `_extract_tail_json`；模型未提交决策时优雅降级（安全默认 + 旧式 json 兜底）；`_extract_tail_json` 标注 deprecated 保留。
  - 新增 `tests/test_analysis_decision.py`：假 client 覆盖「直接提交决策/多轮取证后提交/不调用降级」3 路径。
- **自测**：新测 3 passed；全量 **63 passed**（基线 60 + 新增 3），无回归。导入链路验证 OK。
- 注意：`PLATFORM_MOCK=1` 只 mock 设备/工具，LLM 仍走真实 API；真实 demo 验证待用户决定（省 token）。

### 2026-09-18（规划）
- 完成升级规划：`DEVELOPMENT_PLAN.md`（总计划，9 阶段）、`docs/UPGRADE_DESIGN.md`（各阶段接口签名+改动清单+自测点）、本进度文档。
- 核对了全部亮点的代码基线（见上「基线」节）。
- 已确认 5 项决策。等待开始 P0-A。

---

## 续开发指引（给下一次对话/未来的自己）

1. 读本文件「当前状态」+「阶段看板」，确认从哪个阶段接着做。
2. 打开 `docs/UPGRADE_DESIGN.md` 对应阶段，按「改动清单」逐项实现。
3. 实现完成 → 跑「自测命令」→ 全绿后：把该阶段看板标 ✅、更新「当前状态」、在「开发日志」追加一条。
4. 若实现偏离设计，回 `docs/UPGRADE_DESIGN.md` 更新该阶段设计并在开发日志记录原因。
5. 一次只推进一个阶段，完成并自测通过后停下等确认，再进下一阶段。
