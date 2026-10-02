# AI Agent 测试平台 · 升级开发计划

> 生成日期：2026-09-18
> 依据：OEC 内部插件市场（oec-dev / oec-test / oec-panshi / oec-devops）的设计参考 + 本平台主打亮点中尚未落地的部分
> 执行方式：**审核通过后按阶段开发，不一口气做完；每个阶段完成后必须自测通过再进入下一阶段**

---

## 0. 背景与目标

本平台定位：**通用内核 + 专用测试域插件**的多 Agent 硬件设备自动化测试平台，把资深工程师的「分层故障定位」方法论 Agent 化。

这份计划要解决两件事：

1. **补齐主打亮点中尚未真正落地的部分**（下表标 🔴/🟡），让面试叙事里讲的每一条都有代码支撑。
2. **吸收 OEC 插件市场的三条工程纪律**，提升平台成熟度与可迁移性：
   - 规则性判定用**确定性代码**，LLM 只负责「问对问题、读对 JSON」（对标 oec-dev 的 `oec-spec.mjs` runtime）。
   - **能力核心与平台适配层解耦**（对标 `prd-publish` 不知道磐石实现，靠显式调用 + 依赖声明）。
   - **方法论资产化**：把知识/契约从代码里抽成结构化文件（对标 oec-test 的 references、oec-dev 的 contract 文件）。

### 亮点现状盘点（计划的基线）

| 主打亮点 | 现状 | 定位 |
|---|---|---|
| 分层定位能力 | `AnalysisAgent` 多轮工具循环 + 插件 `analysis_prompt`/`checkpoints`/`knowledge_base` | ✅ 已实现，可强化 |
| 多 subagent 协作 | 5 个 `LLMAgent` + `Coordinator` 串行编排 | ✅ 已实现 |
| 通用内核 + 测试域插件 | `TestPlugin` 抽象 + acoustic/lamp 两插件 + `PluginRegistry` | ✅ 已实现 |
| checkpoint / resume | `CheckpointStore` + drift 检测 + `PreflightAgent` + `resume()` | ✅ 已实现，较完整 |
| bad-case / golden-set 记忆库 | `golden_set/` 全套，tag/signature 打分检索 | 🟡 已实现，检索偏弱 |
| **harness 兜底架构** | 仅 `try/except` 记 `stage_errors` 降级，无重试/退避/熔断/统一错误面 | 🔴 基本未实现 |
| **平台适配层（可迁移性）** | 无抽象，需求源/结果汇/触发器/认证全未抽象 | 🔴 未实现 |
| 用例生成 Agent | `CaseAgent` 走插件模板，非「从需求/代码生成」 | 🟡 半实现 |
| 结构化输出健壮性 | 复测决策靠 `_extract_tail_json` 从 markdown 尾抠 JSON | 🔴 脆弱 |
| 可观测性 | 无 token/耗时/工具调用集中收集 | 🔴 未实现 |
| **证据置信度 / 防假绿** | `Diagnosis` 有 need_reproduce/is_new_bug，但无置信度、无「证据是否充分」门槛，模型给结论就当结论 | 🔴 未实现 |
| **收敛停机准则** | 复测循环只有 `MAX_REPRODUCE_ROUNDS=2` 硬上限，无「两轮无实质增量就停」的停滞检测 | 🔴 未实现 |
| **运行审计 / 阶段门禁** | checkpoint 是「续跑」用，产物平铺在 workspace，无一次运行的完整审计目录与阶段 gate | 🟡 偏弱 |
| **预算管控** | 无 token 预算硬闸、无单次运行 wall-clock 止损，只有复测轮数上限 | 🔴 未实现 |

> 说明：末四行是对照公司 TestAgent-CC（Agentic QE 平台）后提炼的**可靠性 & 可观测工程纪律**——不抄它的多测试域广度，只吸收「不假绿 / 会收敛 / 可审计 / 控成本」这套让 Agent 系统可信的纪律，落到本平台单域纵深上。对应 P8 / P9 / P10 与 P5 扩展。

---

## 1. 阶段总览与优先级

按「投入产出比 + 面试价值 + 依赖关系」排序。每个阶段独立可交付、独立自测。

| 阶段 | 主题 | 对应亮点 | 依赖 | 预估规模 |
|---|---|---|---|---|
| **P0-A** | 结构化输出：消除脆弱 JSON 解析 | 分析 Agent 健壮性 | 无 | 小（半天） |
| **P0-B** | 工具寻址 & 统一错误面 | 执行健壮性 | 无 | 小 |
| **P1** | Harness 兜底架构 | 🔴 harness 兜底 | P0-A/B | 中 |
| **P2** | 平台适配层 `PlatformAdapter` | 🔴 可迁移性 | 无（可并行） | 中 |
| **P3** | 用例生成 Agent（从需求/代码生成） | 🟡 用例生成 | P0-A | 中 |
| **P4** | Golden-set 记忆库强化 | 🟡 记忆库 | 无 | 中 |
| **P5** | 可观测性 + **预算管控**（trace / token / 耗时 / 成本硬闸） | 🔴 可观测性 + 🔴 预算管控 | P1 | 中 |
| **P6**（可选） | MCP server 化 | 可迁移性升级 | P2 | 中-大 |
| **P7**（可选） | 并行采集 & 方法论资产化 | 泛化能力 | 无 | 中 |
| **P8** | 证据置信度门槛 & 防假绿 | 🔴 证据置信度 | P0-A | 小 |
| **P9** | 收敛闭环 & 复测停滞检测 | 🔴 收敛停机准则 | P8 | 小-中 |
| **P10** | 运行审计目录 & 阶段门禁 | 🟡 运行审计/门禁 | 无（可并行） | 中 |

> **阶段编号只表身份、不表顺序**（P0-A/P0-B 已完成，续接编号避免打乱既有跨文档引用）。
>
> **修订后建议执行顺序（2026-09-23）**：把「可靠性 + 可观测」四块前置，因为它们改动小、投入产出比最高、且是面试最高频追问区——
> **P8 → P5 → P9 → P10 → P1 → P2 → P3 → P4**，再视时间做 P6/P7。
> 理由：P8（不假绿）改动最小收益最大，先做；P5（成本可见）与 P8 共用 `on_event` 管线，紧随；P9（会收敛）依赖 P8 的置信度字段；P10（可审计）独立可并行。四块合起来是同一个「可信 Agent 系统」叙事的四根支柱。
> 每个阶段结束后停下，自测通过并让我确认，再进入下一阶段。

---

## 2. 分阶段详细设计

### P0-A · 结构化输出：消除脆弱 JSON 解析

**问题**：`core/agents/subagents.py::AnalysisAgent.run()` 依赖 `_extract_tail_json()` 从模型 Markdown 输出末尾正则抠 `​```json` 块。格式一漂就丢字段，复测/记忆库决策链路静默失效。

**方案**：改用 Anthropic tool-use 的**强制工具调用**收敛结构化决策。分析 Agent 在给出结论后，必须调用一个 `submit_diagnosis_decision` 工具，参数即 schema 约束的决策字段（`need_reproduce` / `reproduce_hint` / `target_evidence` / `matched_bad_cases` / `is_new_bug` / `bug_summary`）。工具的执行体只做校验并回存，不再靠正则。

**改动点**：
- `core/agents/base.py`：`LLMAgent` 增加对「结论型工具」的支持（或新增一个可选的 `final_tool` 机制），能拿到工具入参作为结构化结果。
- `core/agents/subagents.py`：`AnalysisAgent` 注册决策工具，`run()` 从工具入参构造 `Diagnosis`，删除对 `_extract_tail_json` 的依赖（保留函数并加 `@deprecated` 兼容旧数据）。
- 保留 `REPRODUCE_DECISION_INSTRUCTION` 的字段说明，但改成「调用工具」而非「追加 json 块」。

**自测标准**：
- 新增 `tests/test_analysis_decision.py`：mock 模式下，构造分析任务，断言返回的 `Diagnosis` 各字段被正确填充；构造模型不返回决策的场景，断言优雅降级（`need_reproduce=False`，不崩）。
- 现有 60+ 单测全绿；`examples/demo.py` mock 模式跑通。

---

### P0-B · 工具寻址 & 统一错误面

**问题一**：`ExecutorAgent._role_tool()` 通过 `getattr(plugin, "audio_playback_tool")` 用**方法名字符串**寻址工具，插件改名 silent failure。
**问题二**：工具返回错误格式不统一（有的 `{"status":"error","result":...}`，有的 `{"error":...}`），Agent 需学会处理多种格式。

**方案**：
- 引入显式角色枚举 `ToolRole`（`PLAYBACK` / `AUDIO` / `LOG` / ...），`TestPlugin` 提供 `tool_for_role(role) -> Tool | None` 的可选接口，`_role_tool` 改为查枚举而非方法名。旧插件保留兼容回退。
- 在 `core/tools/base.py` 定义统一的工具结果结构（如 `ToolResult{status, data, error}` 或统一 dict 约定），`ToolRegistry.execute()` 保证出参归一化。

**自测标准**：
- `tests/test_tool_addressing.py`：插件角色缺失/改名时，断言不再 silent skip，而是产生显式的错误 `RawData`。
- `tests/test_tool_result_shape.py`：断言所有工具（mock）返回统一结构。
- 全量单测绿。

---

### P1 · Harness 兜底架构（🔴 核心亮点补齐）

**问题**：当前「兜底」只有 `Coordinator.run()` 里每段 `try/except` 记 `stage_errors`。没有重试、指数退避、熔断，没有统一的失败恢复策略，谈不上「harness」。

**方案**：抽出一个 `core/harness/` 层，把「可靠执行一个 Agent/工具调用」的策略集中收口：

```
core/harness/
├── __init__.py
├── policy.py       # RetryPolicy（次数/退避/可重试异常白名单）、CircuitBreaker
├── runner.py       # HarnessRunner.run(callable, policy) 统一执行 + 兜底
└── errors.py       # 统一异常分类：Retryable / Fatal / Degradable
```

- **重试 + 指数退避**：网络超时、429、5xx 等可重试错误自动退避重试（对标短板「网络超时只有简单重试」）。
- **熔断**：连续失败达阈值后快速失败，不再空转烧 token。
- **降级策略显式化**：每个 stage 声明「失败时降级为什么」（跳过 / 用缓存 / 转人工），而不是散在 `except` 里。
- **Coordinator 接入**：5 个 stage 的调用统一走 `HarnessRunner`，`stage_errors` 记录结构化失败原因（阶段/错误类型/重试次数/最终决策）。

**面试价值**：这是把「兜底架构」从口号变成可讲的工程设计——「我的 harness 层区分可重试/致命/可降级三类错误，对 LLM 调用做指数退避 + 熔断，每个流水线阶段声明自己的降级策略」。

**自测标准**：
- `tests/test_harness_retry.py`：注入「前 2 次抛 429、第 3 次成功」的假调用，断言重试成功且退避被触发。
- `tests/test_harness_circuit.py`：连续失败触发熔断，断言快速失败不再调用。
- `tests/test_coordinator_degrade.py`：某 stage 注入致命错误，断言其余 stage 继续、`stage_errors` 结构化记录、最终报告如实标注。
- 全量单测绿。

---

### P2 · 平台适配层 `PlatformAdapter`（🔴 可迁移性核心）

**问题**：平台和「公司」之间有 4 条边界未抽象——需求输入源、结果/报告汇、缺陷/知识回写、CI 触发器，外加认证。现在要么写死要么缺失，导致「带去别的公司」无从谈起。

**方案**：引入与 `TestPlugin`（测试域轴）**正交**的第二根抽象轴——`PlatformAdapter`（公司后端轴）。对标 OEC「能力核心不知道平台实现，靠显式调用 + 依赖声明」的解耦。

```
core/platforms/
├── __init__.py
├── base.py         # PlatformAdapter ABC
├── local.py        # LocalAdapter：文件读需求 + 本地 Markdown 出报告（复用现有 mock 逻辑，降级兜底）
└── config.py       # 从 config/platforms/<company>.yaml 装配 adapter
```

```python
class PlatformAdapter(ABC):
    def fetch_requirement(self, ref: str) -> TestRequirement: ...   # 需求来源
    def push_report(self, report: TestReport) -> str: ...           # 结果去向
    def create_defect(self, diagnosis: Diagnosis) -> str | None: ... # bad-case → 缺陷单
    def query_history(self, signature: str) -> list[BadCase]: ...   # 历史记忆（可挂平台侧）
```

**红线（来自 OEC）**：
- 能力核心（`Coordinator`/5 Agent）**永不 import 公司 SDK**；公司特定依赖只出现在具体 adapter 里。
- **配置驱动**：换公司 = 换 `config/platforms/<company>.yaml`，不改代码。
- **降级优雅**：未配平台时自动退回 `LocalAdapter`。

**范围控制（本阶段只做地基）**：只落 `PlatformAdapter` 接口 + `LocalAdapter` + `Coordinator` 依赖注入改造。不实现具体公司 adapter（磐石/DevOps 等留到有真实对接需求时）。

**自测标准**：
- `tests/test_platform_adapter.py`：`LocalAdapter` 从临时文件读需求、写报告；`Coordinator` 注入 adapter 后端到端 mock 跑通。
- 未配 adapter 时断言自动降级为 `LocalAdapter`，行为与当前一致（回归保护）。
- 全量单测绿。

---

### P3 · 用例生成 Agent（从需求/代码生成，🟡 补齐）

**问题**：`CaseAgent` 只调 `plugin.generate_cases()` 走固定模板，不是真正「从需求/代码变更生成用例」。`NEXT_STEPS.md` 里这是 P1 待办。

**方案**：借 oec-test 的方法论（模式判定 + 回归影响口径 + 量化门槛），给 `CaseAgent` 增加 LLM 生成能力，保留模板作为 fallback：
- **模式判定**：从需求/代码输入判断「新需求生成」还是「变更回归」。
- **回归的代码层面影响口径**（关键、专业）：只有当变更在**代码层面**（调用链、共享组件、状态、存储、接口）影响到老模块时才生成回归用例，而非「语义相关」就标。汇总需说明每组回归用例对应的代码影响来源。
- **量化产出门槛**：优先级分层、覆盖正常/异常/边界，产出规模有下限而非「尽量多」。
- 生成走 P0-A 的结构化工具输出（用例是 schema 化的 `TestCase` 列表，不靠解析自由文本）。

**自测标准**：
- `tests/test_case_generation.py`：给定需求文本，mock 生成，断言产出 `TestCase` 结构合法、优先级字段齐全；给定「变更但无代码影响」场景，断言不误标回归。
- 全量单测绿。

---

### P4 · Golden-set 记忆库强化（🟡）

**问题**：`GoldenSetIndex.search()` 只支持 tag 精确 + log_signature 的 LIKE 子串匹配，无语义检索，相似历史 case 易漏判/误判。

**方案**（分档，按时间选做）：
- **档一（低成本）**：把检索能力做成分析 Agent 的一个显式工具（`golden_set_query`），让 Agent 主动带症状/日志签名查询并二次研判命中；强化打分（severity/seen_count 加权、最近性）。
- **档二（进阶）**：引入本地向量检索（如 `sqlite-vss` 或轻量 embedding + 余弦），对 symptom/root_cause 做语义相似，替代纯 LIKE。保持「JSON 是唯一真相源、索引可重建」的现有设计。
- **状态机沉淀**：巩固 `HYPOTHESIS → CONFIRMED → RESOLVED / REGRESSED` 的记忆库生命周期，让「记忆库」有可讲的知识演进故事。

**自测标准**：
- `tests/test_golden_set_search.py`：构造相似/不相似 case，断言相似的排序靠前、不相似的不误命中。
- 索引重建后检索结果一致（回归保护）。
- 全量单测绿。

---

### P5 · 可观测性 + 预算管控（🔴 + 🔴，对标 TestAgent-CC Run Budget v4）

**问题一（可观测性）**：工具调用、token 消耗、每阶段耗时无集中收集，调试难、无法做成本评估。
**问题二（预算管控）**：没有 token 预算硬闸、没有单次运行 wall-clock 止损，只有复测轮数上限 `MAX_REPRODUCE_ROUNDS`。一次跑飞的分析可能空烧大量 token 无人拦。

**方案**：在 P1 的 harness 层挂一个轻量 trace 收集器，并在其上叠一层预算账本（二者共用同一份 usage 数据，一次采集两用）：

*可观测性（trace）*：
- 每次 LLM 调用记录 model / input_tokens / output_tokens / 耗时 / 轮数。
- 每次工具调用记录 name / 耗时 / 成功失败。
- 每个 stage 汇总，最终写入 workspace 的 `trace.json`，报告里附「本次测试消耗概览」。
- 复用现有 `on_event` 事件流，新增收集器订阅者即可，不侵入业务逻辑。

*预算管控（budget，吸收 TestAgent-CC 的 reserve/settle/status 思路，做轻量版）*：
- `RunBudget`：可配 `max_total_tokens`（token 硬闸）与 `max_wall_clock_s`（单次运行墙钟止损）。
- Tracer 每累加一次 usage 就问 budget 是否越界；越界时 emit `budget_exceeded` 事件并置「停机标志」。
- Coordinator 在每个 stage / 每轮复测前检查停机标志：已越界 → 不再启动新的 LLM 阶段，**诚实收尾**（报告标注「因预算/时限提前终止，结论基于已采证据」），而不是硬崩或空烧。
- 预算来源：`config` 增可选项（默认 `None`=不设限，保持旧行为），也可 `Coordinator(..., budget=RunBudget(...))` 注入。
- **与 P9 的关系**：P9 管「证据不再增长就停」（收敛准则），P5 预算管「烧太多就停」（成本准则），两者是互补的两类停机闸，都汇入 Coordinator 的统一停机判断。

**面试价值**：「我的平台每次运行产出 `trace.json`，含逐 Agent 的 token/耗时/工具调用分布；并有 token 硬闸 + 墙钟止损两道预算闸，越界时诚实收尾而非空烧——回答『你怎么控制 LLM Agent 的成本、怎么防跑飞』。」

**自测标准**：
- `tests/test_observability.py`：mock 跑一次，断言 `trace.json` 生成且含 token/耗时/工具调用统计。
- `tests/test_run_budget.py`：注入极小 `max_total_tokens`，断言越界后 emit `budget_exceeded`、后续 stage 被跳过、报告如实标注提前终止；未设预算时行为与当前一致（回归保护）。
- 全量单测绿。

---

### P6（可选）· MCP server 化

把平台能力（生成用例、跑测试、查 golden-set）暴露为 MCP server，同时可通过 MCP 消费公司平台能力。这是「带去别的公司」最现代的答案：换公司 = 换一组 MCP 配置。依赖 P2 的适配层抽象已就位。规模较大，视秋招时间投入。

**自测标准**：MCP server 本地可被 Claude Code / Cursor 连接并调用；端到端跑通一次用例生成。

---

### P7（可选）· 并行采集 & 方法论资产化

- **并行采集**：落地 `ExecutorAgent._run_parallel()` 的真并行（多线程），兑现灵犀屏并行能力（现为 TODO 串行）。
- **方法论资产化**：把 `shared_knowledge.py` 里硬编码的 7 层检查点、故障模式抽成可编辑的 YAML/MD 知识资产，让测试专家也能贡献、方法论与代码解耦（对标 oec-test references）。

**自测标准**：并行采集有并发正确性测试（无资源竞争、结果与串行一致）；知识资产改文件后无需改代码即生效。

---

## 2b. 可靠性 & 可观测支柱（P8-P10，2026-09-23 提炼自 TestAgent-CC）

> 这三节是对照公司 Agentic QE 平台后新增的三根支柱。核心不是抄它的测试域广度，而是吸收它「让 Agent 结论可信」的工程纪律，落到本平台的分层故障定位上。三者与 P5（预算管控）合起来构成完整的「可信 Agent 系统」叙事：**不假绿（P8）/ 会收敛（P9）/ 可审计（P10）/ 控成本（P5）**。

### P8 · 证据置信度门槛 & 防假绿（🔴 最高性价比）

**问题**：`AnalysisAgent` 现在只要模型经 `submit_diagnosis_decision` 给了结论，`Coordinator` 就当结论用（写 golden-set、出报告）。但**没有「这个结论有多可信」「证据是否充分」的显式维度**——模型在证据不足时也可能给出一个自信的根因，直接被当成定论。这正是 TestAgent-CC 反复强调的「假绿」在诊断场景的翻版：用一个看似确定的结论掩盖了实际的低置信。

**方案**（对标 TestAgent-CC「业务硬断言 + 软信号进人工复核队列」，做诊断版）：给决策工具的 schema 加两个维度，并在 Coordinator 侧据此分流：
- `confidence`：`high` / `medium` / `low`——模型对根因判断的置信度。
- `evidence_sufficiency`：`sufficient` / `insufficient`——现有证据是否足以支撑该结论。
- **分流规则**（Coordinator）：
  - `confidence=low` 或 `evidence_sufficiency=insufficient` → **不**直接落 golden-set 为 `CONFIRMED`；标记为「待人工复核」，报告置顶「⚠️ 低置信结论，需人工复核」，且此类结论**不允许**触发 `is_new_bug` 的自动写库（或只以 `HYPOTHESIS` 状态入库，与已有 `BadCaseStatus.HYPOTHESIS` 天然对齐）。
  - `evidence_sufficiency=insufficient` 同时 `need_reproduce=false` 时（模型说不够但又不想复测）→ 视为矛盾信号，强制转人工，不洗成通过。
- **红线**：低置信 / 证据不足 / 未执行的预期，一律照实列入「待复核」，绝不静默当成定论——与 CLAUDE.md 的输出红线、TestAgent-CC 的防假绿同源。

**为什么最高性价比**：改动集中在 `DECISION_SCHEMA` 加两字段 + `Diagnosis` 加两字段 + `Coordinator` 第 6 步写库前加一个分流判断，代码量小；但直接回答面试最核心的可靠性问题——「你怎么保证 LLM 不瞎猜根因？」

**面试价值**：「我给诊断决策加了 confidence + evidence_sufficiency 两维；低置信或证据不足的结论不会被自动写进知识库、不会伪装成定论，而是进入待人工复核队列，报告里显式标注。宁可漏报不假报。」

**自测标准**：
- `tests/test_confidence_gating.py`（假 client）：构造「高置信+证据充分」→ 正常落库；「低置信」→ 不落 CONFIRMED、报告含复核标记；「证据不足但 need_reproduce=false」→ 强制转人工。
- 未带新字段的旧决策（兼容）→ 默认 `confidence=medium`、`evidence_sufficiency=sufficient`，行为与当前一致（回归保护）。
- 全量单测绿 + demo mock 跑通。

### P9 · 收敛闭环 & 复测停滞检测（🔴，对标 TestAgent-CC 覆盖率闭环的 stall detection）

**问题**：`Coordinator` 的自主复测循环现在唯一的停机准则是硬上限 `MAX_REPRODUCE_ROUNDS=2`。它不判断「这一轮复测到底有没有带来新证据」——如果两轮复测采到的数据实质上和上一轮一样（设备状态没变、日志无增量），它照样空转到上限、每轮都烧一次分析 Agent 的 token。这正是 TestAgent-CC 覆盖率闭环里用「收敛停滞（convergence stall）」解决的问题。

**方案**：给复测循环加一个显式的「停机准则」判定，把「会收敛」从隐式硬上限升级为可讲的工程设计：
- **停滞检测**：每轮复测后，对比新采 `extra_raw` 与上一轮证据的「实质增量」——用一个轻量的证据指纹（如新增 collector 种类、payload 里关键指标/日志签名是否变化）判断。连续一轮无实质增量 → 判定停滞，提前跳出循环并如实标注「复测未能带来新证据，结论基于现有证据 + 转人工」。
- **三类停机准则并列**（Coordinator 统一收口）：① 证据充分且高置信（P8）→ 正常结束；② 证据无增量（P9 停滞）→ 提前收敛转人工；③ 预算/时限越界（P5）→ 诚实收尾。三者任一命中即停，`MAX_REPRODUCE_ROUNDS` 退居为最后的兜底上限。
- **与 P8 咬合**：停机后若仍是低置信/证据不足，走 P8 的待复核分流，不假绿。

**面试价值**：「我的复测循环不是傻跑到上限——它有停滞检测：连续一轮采不到新证据就判定收敛、提前止损转人工，而不是空烧 token。停机准则有三类：证据充分、证据停滞、预算越界。」

**自测标准**：
- `tests/test_reproduce_convergence.py`（假 client + 假 executor）：构造「每轮都返回相同证据」→ 断言在 `MAX_REPRODUCE_ROUNDS` 之前因停滞提前跳出、报告标注收敛止损；构造「每轮带新证据」→ 断言正常复测到证据充分或到上限。
- 现有复测相关行为回归绿（`MAX_REPRODUCE_ROUNDS` 兜底仍生效）。
- 全量单测绿。

### P10 · 运行审计目录 & 阶段门禁（🟡，对标 TestAgent-CC session 隔离 + gate）

**问题**：现在 `workspace_path` 下产物是**平铺**的（`raw_data.json` / `diagnosis.json` / `raw_data_round{N}.json` / `.checkpoints/`），且 checkpoint 的定位是「续跑」而非「一次运行的完整审计」。多次运行会互相覆盖，没有「这一次运行从需求到报告的全链产物 + 每阶段状态」的自包含目录，也没有 TestAgent-CC 那种「阶段门禁（gate）」——即某个关键产物没就绪就不许进下一步。

**方案**（对标 TestAgent-CC 的 `session-{timestamp}/` + gate 标记）：引入自包含的运行目录，把一次运行的全链产物 + 阶段门禁状态归档在一起。你现有的 `raw_data_round{N}.json` 已是雏形，顺势收拢：
- **运行目录**：`runs/run-{timestamp}/`，内含 `run_meta.json`（插件/需求/开始时间/最终状态）、各阶段产物（沿用现有 `raw_data.json` / `diagnosis.json` / 报告）、`gates.json`（阶段门禁状态）。`.checkpoints/` 继续复用现机制，放在运行目录内。
- **阶段门禁（gate）**：每个 stage 完成时写一条 gate 记录（`stage / status: pass|fail|degraded / 关键产物是否就绪`）。下一 stage 启动前检查前置 gate；关键前置未 pass → fail-closed（如「执行阶段没采到任何 raw_data」就不硬跑分析出空结论，而是显式标注阻塞）。
- **向后兼容**：`workspace_path=None` 时行为完全不变（不建运行目录、不打 gate，保持当前轻量路径）；配了 `workspace_path` 时自动在其下建 `runs/run-*/`。与现有 checkpoint/resume 不冲突——resume 时定位到对应运行目录续跑。
- **范围控制**：本阶段只做「目录收拢 + gate 记录 + fail-closed 判断」，不引入平台上传等重逻辑（那属于 P2 适配层）。

**面试价值**：「每次运行有一个自包含的 `runs/run-{ts}/` 审计目录，需求→用例→证据→诊断→报告全链 + 每阶段门禁状态都在里面，可复现、可追溯；关键前置产物没就绪就 fail-closed，不硬跑出空壳结论。」这与已有的 checkpoint/resume 是同一个「生产可靠性」故事的自然延伸。

**自测标准**：
- `tests/test_run_directory.py`：配 workspace 跑一次 mock，断言生成 `runs/run-*/` 且含 run_meta/gates/各阶段产物；执行阶段 raw 为空时断言分析阶段 gate 记 fail-closed、报告如实标注、不产出伪结论。
- `workspace_path=None` 时断言行为与当前完全一致（强回归保护）。
- 与现有 `test_checkpoint.py` 全绿（不破坏续跑）。
- 全量单测绿。

---

## 3. 全局约定

- **每阶段独立分支/独立提交**，完成后自测通过再进下一阶段；不一口气全做完。
- **自测优先**：每阶段都新增对应单测 + 保证现有 60+ 单测全绿 + `examples/demo.py` mock 跑通。真机验证在有设备时补。
- **可逆优先**：改动尽量限制在内核内、保留旧路径兼容（如 `_extract_tail_json` 保留降级），降低风险。
- **不改依赖极简原则**：除非某阶段（如 P4 档二向量检索、P6 MCP）确有必要，不引入重依赖；引入时在该阶段说明理由并锁版本。
- **面试叙事对齐**：每完成一个阶段，同步更新 README 的技术亮点说明，确保「讲的每条都有代码」。

---

## 4. 已确认决策（2026-09-18）

1. **执行顺序**：按 P0-A → P0-B → P1 → P2 → P3 → P4 → P5 → P6 → P7。
2. **P4 记忆库**：本轮做**档一**（工具化 + 打分强化，轻量）；**保留档二**（向量语义检索）作为后续升级方案，接口预留扩展位，不在本轮实现但设计上不堵死。
3. **P6 MCP、P7 并行/资产化**：纳入本轮计划。
4. **细化设计**：每阶段动手前先出细化设计（接口签名 + 改动清单）——已统一沉淀到 `docs/UPGRADE_DESIGN.md`。
5. **进度跟踪**：跨对话、跨日期的单一事实源为 `UPGRADE_PROGRESS.md`（根目录）。每次开工前先读它、收工前更新它。

## 5. 已确认决策（2026-09-23，追加）

> 背景：探索了公司开源的 Agentic QE 平台 TestAgent-CC 后，把它的「可靠性 + 可观测」工程纪律提炼进本计划，作为秋招主打项目的差异化亮点。本日只做规划，不写代码。

1. **新增三根支柱 P8 / P9 / P10**（编号续接，只表身份不表顺序）：
   - P8 证据置信度门槛 & 防假绿（confidence + evidence_sufficiency，低置信不落库/进人工复核）。
   - P9 收敛闭环 & 复测停滞检测（连续无证据增量即提前止损，不空转到硬上限）。
   - P10 运行审计目录 & 阶段门禁（`runs/run-{ts}/` 自包含 + gate fail-closed）。
2. **P5 扩展**：并入「预算管控」（token 硬闸 + 墙钟止损，越界诚实收尾），与可观测性共用同一份 usage 采集。
3. **修订执行顺序**：可靠性四块前置——**P8 → P5 → P9 → P10 → P1 → P2 → P3 → P4 → P6 → P7**。理由见 §1 阶段总览。
4. **不做的事（明确边界）**：不引入 TestAgent-CC 的多测试域广度（WebUI/移动/桌面/API 等）；本平台坚持「单域纵深（AIoT 声学分层定位）+ 可信 Agent 纪律」的定位，避免稀释叙事。
5. **兼容红线不变**：所有新阶段保持 `workspace_path=None` / 未配预算时行为与当前完全一致（强回归保护）；旧决策数据缺新字段时取安全默认。

> 相关文档：
> - 细化设计（接口签名 + 改动清单）：`docs/UPGRADE_DESIGN.md`
> - 进度跟踪（跨对话续开发）：`UPGRADE_PROGRESS.md`
