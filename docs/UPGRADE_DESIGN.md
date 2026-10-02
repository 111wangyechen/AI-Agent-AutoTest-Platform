# 升级细化设计（接口签名 + 改动清单）

> 配套文档：`DEVELOPMENT_PLAN.md`（总计划）、`UPGRADE_PROGRESS.md`（进度跟踪）
> 生成日期：2026-09-18
> 说明：本文件给出每个阶段动手前的细化设计——接口签名、文件改动清单、数据结构、自测点。
> 每阶段开工前先在此处敲定设计，实现中若偏离设计需回来更新本文件并在进度文档记录原因。

---

## 约定

- 所有新代码遵循现有风格：`from __future__ import annotations`、中文 docstring、dataclass 优先、stdlib 优先（除非阶段明确引入依赖）。
- 保留向后兼容：旧路径（如 `_extract_tail_json`）降级保留，不硬删。
- 每阶段末尾：新增单测 + 现有单测全绿 + `examples/demo.py` mock 跑通。

---

## P0-A · 结构化输出：消除脆弱 JSON 解析

### 目标
`AnalysisAgent` 的复测/记忆库决策不再靠正则从 Markdown 尾抠 JSON，改为 Anthropic tool-use 强制工具调用，schema 约束字段。

### 接口签名

`core/agents/base.py` — `LLMAgent` 增加「结论工具」机制：

```python
class LLMAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        registry: ToolRegistry | None = None,
        max_rounds: int = 12,
        max_tokens: int = 8000,
        final_tool: FinalTool | None = None,   # 新增：结论型工具
    ) -> None: ...

    # run() 返回值扩展：
    # {"text": str, "rounds": int, "tool_calls": list, "final_tool_input": dict | None}
```

```python
# core/agents/base.py 新增
@dataclass
class FinalTool:
    """结论型工具：Agent 必须调用它来提交结构化结论。"""
    name: str
    description: str
    input_schema: dict          # JSON Schema
    required: bool = True        # True 时用 tool_choice 强制调用（收尾轮）
```

**实现要点**：
- `final_tool` 存在时，把它并入 `tools` 列表。
- 循环逻辑：正常多轮工具调用照旧；当模型停止调取证工具、准备收尾时，用 `tool_choice={"type": "tool", "name": final_tool.name}` 强制其调用结论工具（或在最后一轮注入强制）。
- 拿到结论工具入参 → 作为 `final_tool_input` 返回；同时把文本结论保留在 `text`。
- 模型始终没调结论工具（异常）→ `final_tool_input=None`，上层降级。

`core/agents/subagents.py` — `AnalysisAgent`：

```python
DECISION_SCHEMA = {  # 复测 + 记忆库决策的 JSON Schema
  "type": "object",
  "properties": {
    "need_reproduce": {"type": "boolean"},
    "reproduce_hint": {"type": "string"},
    "target_evidence": {"type": "array", "items": {"type": "string"}},
    "matched_bad_cases": {"type": "array", "items": {"type": "string"}},
    "is_new_bug": {"type": "boolean"},
    "bug_summary": {"type": "object"},
  },
  "required": ["need_reproduce", "is_new_bug"],
}

class AnalysisAgent:
    def run(self, task, on_event=None) -> Diagnosis:
        # 用 final_tool_input 构造 Diagnosis；为空则降级（need_reproduce=False, is_new_bug=False）
```

### 改动清单
- [ ] `core/agents/base.py`：新增 `FinalTool` dataclass；`LLMAgent.__init__` 加 `final_tool` 参；`run()` 支持强制结论工具、返回 `final_tool_input`。
- [ ] `core/agents/subagents.py`：定义 `DECISION_SCHEMA`；`AnalysisAgent` 用 `final_tool` 装配；`run()` 改用 `final_tool_input`；`_extract_tail_json` 标注 deprecated 但保留。
- [ ] `REPRODUCE_DECISION_INSTRUCTION`：措辞从「追加 json 块」改为「调用 submit_diagnosis_decision 工具」。

### 自测点
- [ ] `tests/test_analysis_decision.py`：mock 下决策字段正确填充；模型不调结论工具时优雅降级不崩。
- [ ] 全量单测绿 + `examples/demo.py` mock 跑通。

---

## P0-B · 工具寻址 & 统一错误面

### 范围调整（2026-09-18 实施前核对现状）
核对代码后调整原设计，忠于现状、避免过度改动：
- **不引入 `ToolResult` dataclass**：现有全部工具已统一用 `{"status": "success"|"error", ...}`，且无「裸 error 键不带 status」的情况（已 grep 确认）。强行迁移 dataclass 会波及十几个工具与既有断言，收益低风险高。改为提供轻量 `normalize_result()` 做防御性归一（保证结果一定含 `status`），不动工具本身。
- **保留 `success`/`error` 字面量**（不改成 `ok`）：既有工具与 `test_executor_dispatch.py` 断言都用 `success`，沿用之。
- **真正的脆弱点是角色寻址的魔法字符串**：`_role_tool` 内部 `{"playback": "audio_playback_tool", ...}` 字典 + `getattr(plugin, 方法名)`。P0-B 聚焦消除它。

### 接口签名

`core/tools/base.py`：

```python
from enum import Enum

class ToolRole(str, Enum):
    """执行阶段的工具角色（一等公民，取代方法名魔法字符串）。"""
    PLAYBACK = "playback"
    AUDIO = "audio"
    LOG = "log"
    # 后续可扩展

def normalize_result(result: Any) -> dict[str, Any]:
    """防御性归一：保证工具结果是含 status 的 dict。
    - 已是含 status 的 dict → 原样返回
    - 是 dict 但缺 status → 补 status=success（视为成功数据）
    - 非 dict → 包成 {"status":"success","result":result}"""
```

`core/plugins/base.py` — `TestPlugin` 增加可选角色查询（默认回退旧方法名，向后兼容）：

```python
def tool_for_role(self, role: ToolRole) -> Tool | None:
    """按角色返回工具。默认实现回退到旧的 audio_playback_tool()/
    log_collector()/audio_collector()，保证现有插件零改动即可用。
    子类可覆盖为显式映射。role 未知或工具不存在返回 None。"""
```

`core/agents/subagents.py` — `ExecutorAgent._role_tool()` 改为查 `plugin.tool_for_role(ToolRole.X)`，不再 `getattr` 方法名字符串。保持返回工具名（`str | None`），现有调用点与测试不变。

### 改动清单
- [ ] `core/tools/base.py`：新增 `ToolRole` 枚举、`normalize_result()`。
- [ ] `core/tools/__init__.py`：导出 `ToolRole`、`normalize_result`。
- [ ] `core/tools/registry.py`：`execute()` 出参经 `normalize_result()` 归一（兼容现有返回，行为不变）。
- [ ] `core/plugins/base.py`：新增 `tool_for_role()` 默认实现（回退旧方法名 + `ToolRole` 映射）。
- [ ] `core/agents/subagents.py`：`ExecutorAgent._role_tool` 改用 `ToolRole` + `tool_for_role`；行为等价（角色缺失仍返回 None，缺 audio_file 仍产生显式错误 RawData）。

### 自测点
- [ ] `tests/test_tool_addressing.py`：`tool_for_role` 按角色解析正确；未知角色返回 None；插件改工具名后角色仍解析到（回归锁）；`normalize_result` 各分支正确。
- [ ] 现有 `test_executor_dispatch.py` 全绿（行为等价的强回归保护）。
- [ ] 全量单测绿。

---

## P1 · Harness 兜底架构

### 目标
把「可靠执行一次 Agent/工具调用」的策略集中收口：重试 + 指数退避 + 熔断 + 显式降级。

### 目录与接口

```
core/harness/
├── __init__.py       # 导出 HarnessRunner, RetryPolicy, CircuitBreaker, 错误类
├── errors.py
├── policy.py
└── runner.py
```

`core/harness/errors.py`：

```python
class HarnessError(Exception): ...
class RetryableError(HarnessError): ...   # 429 / 超时 / 5xx
class FatalError(HarnessError): ...        # 认证失败 / 参数错误
class DegradableError(HarnessError): ...   # 可降级：跳过/用缓存/转人工

def classify_exception(exc: Exception) -> type[HarnessError]:
    """把 anthropic/网络异常映射为三类之一。"""
```

`core/harness/policy.py`：

```python
@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter: bool = True
    retry_on: tuple[type[Exception], ...] = (RetryableError,)

    def delay_for(self, attempt: int) -> float:  # 指数退避 + 抖动
        ...

class CircuitBreaker:
    """连续失败达阈值 → OPEN（快速失败）；冷却后 HALF_OPEN 试探。"""
    def __init__(self, fail_threshold: int = 5, reset_timeout_s: float = 60.0): ...
    def allow(self) -> bool: ...
    def record_success(self) -> None: ...
    def record_failure(self) -> None: ...
```

`core/harness/runner.py`：

```python
@dataclass
class StageResult:
    ok: bool
    value: Any = None
    error: str = ""
    error_kind: str = ""     # retryable/fatal/degradable
    attempts: int = 0
    decision: str = ""        # success/degraded/aborted

class HarnessRunner:
    def __init__(self, policy: RetryPolicy | None = None,
                 breaker: CircuitBreaker | None = None,
                 on_event: OnEvent = None): ...

    def run(self, fn: Callable[[], Any], *, stage: str,
            degrade: Callable[[], Any] | None = None) -> StageResult:
        """执行 fn，按 policy 重试；失败按 degrade 降级；熔断时快速失败。
        全程发 on_event（harness_retry / harness_degrade / harness_open）。"""
```

### Coordinator 接入
- `Coordinator.__init__` 构造一个 `HarnessRunner`（可注入）。
- 5 个 stage 调用统一包一层 `runner.run(lambda: agent.run(...), stage=名, degrade=降级函数)`。
- `stage_errors` 由字符串升级为结构化记录（stage / error_kind / attempts / decision），`TestReport` 兼容旧格式（保留字符串视图）。

### 改动清单
- [ ] 新建 `core/harness/` 四个文件。
- [ ] `core/agents/coordinator.py`：接入 `HarnessRunner`；5 stage 改走 runner；`stage_errors` 结构化（保留向后兼容的字符串序列化）。
- [ ] `core/agents/base.py`：LLM 调用处抛出可被 `classify_exception` 识别的异常（或在 runner 侧适配 anthropic 异常）。

### 自测点
- [ ] `tests/test_harness_retry.py`：前 2 次 429、第 3 次成功 → 重试成功且触发退避。
- [ ] `tests/test_harness_circuit.py`：连续失败触发熔断，快速失败。
- [ ] `tests/test_coordinator_degrade.py`：某 stage 致命错误 → 其余继续、结构化记录、报告如实标注。
- [ ] 全量单测绿。

---

## P2 · 平台适配层 PlatformAdapter

### 目标
引入与 `TestPlugin`（测试域轴）正交的公司后端轴。核心不 import 公司 SDK，配置驱动，未配则降级 LocalAdapter。

### 目录与接口

```
core/platforms/
├── __init__.py
├── base.py       # PlatformAdapter ABC
├── local.py      # LocalAdapter
└── config.py     # load_adapter(name) 从 config/platforms/<name>.yaml 装配
```

`core/platforms/base.py`：

```python
class PlatformAdapter(ABC):
    name: str = ""

    @abstractmethod
    def fetch_requirement(self, ref: str) -> TestRequirement:
        """从平台/文件按引用取需求。"""

    @abstractmethod
    def push_report(self, report: TestReport) -> str:
        """把报告推到结果汇，返回定位（URL/路径/id）。"""

    def create_defect(self, diagnosis: Diagnosis) -> str | None:
        """把诊断转缺陷单，返回单号。默认 None（不支持）。"""
        return None

    def query_history(self, signature: str) -> list["BadCase"]:
        """历史记忆查询，默认走本地 golden-set。可被平台侧覆盖。"""
        return []
```

`core/platforms/local.py`：`LocalAdapter` —— `fetch_requirement` 从文件/字符串读，`push_report` 写本地 Markdown，`query_history` 委托本地 `GoldenSet`。复用现有 mock/文件逻辑。

`core/platforms/config.py`：

```python
def load_adapter(name: str | None = None) -> PlatformAdapter:
    """按名装配 adapter；name 为空或配置缺失 → LocalAdapter（降级）。"""
```

配置样例 `config/platforms/local.yaml`：

```yaml
adapter: local
requirement_dir: ./requirements
report_dir: ./reports
```

### Coordinator 接入
- `Coordinator.__init__(plugin, workspace_path=None, adapter: PlatformAdapter | None = None)`；`adapter` 为空时 `load_adapter()` 降级 LocalAdapter。
- 需求入口可选走 `adapter.fetch_requirement(ref)`；报告产出后可选 `adapter.push_report(report)`。保持现有直传 requirement 字符串的调用方式兼容。

### 改动清单
- [ ] 新建 `core/platforms/` 四个文件 + `config/platforms/local.yaml`。
- [ ] `core/agents/coordinator.py`：`__init__` 加 `adapter` 参 + 降级装配；需求/报告收口点接 adapter（保持旧调用兼容）。
- [ ] 红线检查：core 层不出现任何公司 SDK import。

### 自测点
- [ ] `tests/test_platform_adapter.py`：LocalAdapter 读需求/写报告；Coordinator 注入后端到端 mock 跑通；未配时降级 LocalAdapter 行为与当前一致。
- [ ] 全量单测绿。

---

## P3 · 用例生成 Agent（从需求/代码生成）

### 目标
`CaseAgent` 增加 LLM 生成能力（模式判定 + 回归代码影响口径 + 量化门槛），模板作 fallback，产出走 P0-A 结构化工具输出。

### 接口签名

`core/agents/subagents.py` — `CaseAgent`：

```python
class GenMode(str, Enum):
    NEW = "new"            # 从需求生成
    REGRESSION = "regression"  # 从代码变更生成增量+回归

class CaseAgent:
    def __init__(self, plugin, use_llm: bool = True): ...

    def run(self, req: TestRequirement, on_event=None,
            code_diff: dict | None = None) -> list[TestCase]:
        """有 code_diff → REGRESSION 模式，判定代码层面影响再决定回归用例；
        否则 NEW 模式从需求生成。use_llm=False 或生成失败 → 回退 plugin.generate_cases()。"""
```

- 用例产出用 `FinalTool`（schema 化 TestCase 列表），不解析自由文本。
- 回归口径：只有变更在代码层面（调用链/共享组件/状态/存储/接口）影响老模块才生成回归用例；汇总说明每组回归对应的代码影响来源。
- 量化门槛：优先级分层、覆盖正常/异常/边界。

### 改动清单
- [ ] `core/agents/subagents.py`：`CaseAgent` 增 LLM 生成路径 + 模式判定 + fallback；用例 schema。
- [ ] `core/plugins/models.py`：`TestCase` 如需 priority/is_regression/impact_source 字段则补（保持 to_dict/from_dict 兼容）。

### 自测点
- [ ] `tests/test_case_generation.py`：需求生成产出结构合法、优先级齐全；「变更但无代码影响」不误标回归；生成失败回退模板。
- [ ] 全量单测绿。

---

## P4 · Golden-set 记忆库强化（档一）

### 目标
档一：把检索做成分析 Agent 的显式工具 + 打分强化。保留档二（向量语义）扩展位。

### 接口签名

`core/golden_set/index.py` — `search()` 打分强化：

```python
def search(self, ..., 
           severity_weight: bool = True,
           recency_weight: bool = True) -> list[dict]:
    """在现有 tag(+2)/signature(+3) 基础上叠加：
    severity 高 +N、seen_count 高 +N、last_seen 近 +N。"""
```

`core/golden_set/tools.py` — 暴露为分析 Agent 工具：

```python
def golden_set_query_tool(golden_set: "GoldenSet") -> Tool:
    """返回一个 Tool，Agent 可带 symptom/tags/log_signatures 主动查询记忆库。"""
```

- 分析 Agent 的 `analysis_tools()` 里挂上 `golden_set_query`，让 Agent 主动查历史 case 并二次研判命中。
- 档二预留：`index.py` 留一个 `search_semantic(query: str)` 的 stub / 扩展点注释，不实现。

### 状态机
巩固 `HYPOTHESIS → CONFIRMED → RESOLVED / REGRESSED`：确保 `add`/更新时状态流转有校验。

### 改动清单
- [ ] `core/golden_set/index.py`：`search()` 打分强化；预留 `search_semantic` 扩展点。
- [ ] `core/golden_set/tools.py`：新增 `golden_set_query_tool`。
- [ ] 插件 `analysis_tools()`：挂上查询工具（acoustic/lamp）。

### 自测点
- [ ] `tests/test_golden_set_search.py`：相似 case 排序靠前、不相似不误命中；打分权重生效；索引重建后结果一致。
- [ ] 全量单测绿。

---

## P5 · 可观测性 + 预算管控

### 目标
在 harness 层挂轻量 trace 收集器，记录 token/耗时/工具调用，写 `trace.json`，报告附消耗概览。复用 `on_event`，不侵入业务。**在同一份 usage 数据上叠一层预算账本**（token 硬闸 + 墙钟止损），越界时置停机标志，Coordinator 诚实收尾。

### 接口签名

`core/observability/tracer.py`：

```python
@dataclass
class LLMCall:
    agent: str; model: str
    input_tokens: int; output_tokens: int
    duration_s: float; rounds: int

@dataclass
class ToolCall:
    agent: str; name: str; duration_s: float; ok: bool

class Tracer:
    def on_event(self, kind: str, payload: dict) -> None:
        """订阅现有事件流，累积 LLMCall/ToolCall。"""
    def summary(self) -> dict: ...       # 总 token/总耗时/各 agent 分项
    def dump(self, path: str) -> None:   # 写 trace.json
```

`core/observability/budget.py`（新增，轻量版 Run Budget）：

```python
@dataclass
class RunBudget:
    """单次运行的预算闸。两个上限任一越界即置停机标志。默认 None=不设限（旧行为）。"""
    max_total_tokens: int | None = None   # input+output 累计上限
    max_wall_clock_s: float | None = None # 从 start() 起的墙钟上限

    def start(self) -> None: ...          # 记录起始时刻
    def add_usage(self, input_tokens: int, output_tokens: int) -> None: ...
    def exceeded(self) -> bool: ...       # 任一维度越界返回 True
    def reason(self) -> str: ...          # "token" / "wall_clock" / ""
    def status(self) -> dict: ...         # 已用/上限/剩余，供报告与事件用
```

- `LLMAgent.run()`：`emit` 事件补充 usage（`resp.usage.input_tokens/output_tokens`）与耗时；现有返回结构不变，usage 走事件通道，避免改调用方。
- `Tracer.on_event`：收到含 usage 的事件后，除累积 LLMCall 外，调用 `budget.add_usage(...)`；越界则 emit `budget_exceeded`（payload 带 `reason` + `status`）。
- `Coordinator`：
  - `__init__(..., budget: RunBudget | None = None)`；持有 `Tracer`（内含 budget），`on_event` 多播到 tracer。
  - 每个 stage / 每轮复测**启动前**检查 `budget.exceeded()`：已越界 → 跳过后续 LLM 阶段，`stage_errors` 记结构化「预算越界提前终止」，报告附「因预算/时限提前终止，结论基于已采证据」。
  - 收尾 `tracer.dump(workspace/trace.json)`，报告附 `budget.status()` 概览。

> **与 P9 的关系**：本阶段只负责「预算越界」这一类停机；「证据停滞」停机在 P9 实现。两者都汇入 Coordinator 的统一停机判断（P9 会把三类准则收口成一处，见 P9 设计）。本阶段先把预算这一路打通，Coordinator 侧留出停机判断的接入点。

### 改动清单（✅ 2026-10-02 完成）
- [x] 新建 `core/observability/tracer.py`、`core/observability/budget.py`、`core/observability/__init__.py`（导出 `Tracer` / `RunBudget` / `LLMCall` / `ToolCall`）。
- [x] `core/config.py`：新增可选 `max_total_tokens` / `max_wall_clock_s`（默认 None，从环境变量 `MAX_TOTAL_TOKENS`/`MAX_WALL_CLOCK_S` 读，保持不设限的旧行为）。
- [x] `core/agents/base.py`：`run()` 记录并 emit `llm_call`（usage + 耗时 + round，假 client 无 usage 安全取 0）；`tool_result` 事件补 `duration_s`/`ok`。
- [x] `core/agents/coordinator.py`：`__init__` 加 `budget` 参 + 构造 Tracer；`on_event` 多播（Tracer + 外部）；analyze/复测/report 启动前查 `budget.exceeded()` 诚实收尾；收尾 dump `trace.json` + `_append_consumption_overview` 报告附概览。

### 自测点（✅ 全绿）
- [x] `tests/test_observability.py`（7 例）：Tracer 累积/dump/喂预算只通知一次；端到端配 workspace 跑一次生成 `trace.json`，含 token/耗时/工具统计 + 报告附消耗概览；无 workspace 不落盘但仍有概览。
- [x] `tests/test_run_budget.py`（12 例）：注入极小 `max_total_tokens` → emit `budget_exceeded`、后续 stage 的 LLM 被跳过（calls==0）、`stage_errors` 结构化记录、报告标注提前终止；`RunBudget()` 默认不设限 → 行为与当前一致（回归保护）；`exceeded()`/`reason()`/`status()` 各分支正确。
- [x] 全量 **99 passed** + demo mock exit=0。
- **实现微调（忠于现状）**：预算越界报告走代码确定性兜底 `_budget_abort_report`（不烧 LLM），与 P8 复核横幅同源；Coordinator 内用遮蔽同名闭包做事件多播，子 Agent 签名零改动。

---

## P6（可选）· MCP server 化

### 目标
把平台能力暴露为 MCP server（生成用例 / 跑测试 / 查 golden-set），并可通过 MCP 消费公司平台能力。依赖 P2 适配层。

### 设计要点（细化待 P5 完成后补全）
- 选型：Python MCP SDK（`mcp`），本地 stdio server。
- 暴露 tools：`generate_cases` / `run_test` / `query_golden_set` / `get_report`。
- 与 PlatformAdapter 关系：MCP 是「对外暴露 + 对内消费」的传输层，adapter 是「公司后端语义」层，两者组合。
- 依赖：本阶段引入 `mcp` 包，锁版本，README 说明。

### 自测点
- [ ] MCP server 本地可被 Claude Code / Cursor 连接调用；端到端跑通一次用例生成。

---

## P7（可选）· 并行采集 & 方法论资产化

### 目标
- 并行采集：`ExecutorAgent._run_parallel()` 落地真并行（多线程），兑现灵犀屏并行能力。
- 方法论资产化：`shared_knowledge.py` 硬编码的 7 层检查点、故障模式抽成 YAML/MD 知识资产。

### 设计要点（细化待 P6 完成后补全）
- 并行：`concurrent.futures.ThreadPoolExecutor`；注意 adb 独占资源的互斥（音频抓取独占）；结果需与串行等价。
- 资产化：知识文件 schema（YAML）+ 加载器；插件从文件加载而非硬编码；改文件即生效。

### 自测点
- [ ] 并行采集并发正确性测试（无资源竞争、结果与串行一致）。
- [ ] 知识资产改文件后无需改代码即生效。

---

## P8 · 证据置信度门槛 & 防假绿

### 目标
给 `AnalysisAgent` 的决策加 `confidence` + `evidence_sufficiency` 两维；`Coordinator` 据此分流：低置信/证据不足的结论不自动落库为 `CONFIRMED`、不伪装成定论，而是进「待人工复核」并在报告标注。建立在 P0-A 的 `DECISION_SCHEMA` + `FinalTool` 之上，**零新增依赖**。

### 接口签名

`core/agents/subagents.py` — 扩展现有 `DECISION_SCHEMA`（在 `properties` 增两项，不改 `required` 以保持旧兼容）：

```python
DECISION_SCHEMA["properties"]["confidence"] = {
    "type": "string",
    "enum": ["high", "medium", "low"],
    "description": "对根因判断的置信度。证据链完整且指向单一根因=high；"
                   "有合理推断但存在其他可能=medium；主要靠猜/证据零散=low",
}
DECISION_SCHEMA["properties"]["evidence_sufficiency"] = {
    "type": "string",
    "enum": ["sufficient", "insufficient"],
    "description": "现有证据是否足以支撑该结论。不足时应同时置 need_reproduce=true",
}
# required 仍为 ["need_reproduce", "is_new_bug"]——新字段可选，缺失时下方取安全默认
```

`DECISION_TOOL_INSTRUCTION`：补一句「同时给出 confidence 与 evidence_sufficiency；证据不足时置信度不得填 high」。

`core/plugins/models.py` — `Diagnosis` 增两字段（带默认值，`to_dict`/`from_dict`/落盘读回兼容）：

```python
@dataclass
class Diagnosis:
    ...
    # ---------- 置信度门槛（P8）----------
    confidence: str = "medium"              # high / medium / low，缺省 medium（旧数据兼容）
    evidence_sufficiency: str = "sufficient"  # sufficient / insufficient，缺省 sufficient
    needs_human_review: bool = False        # 由 Coordinator 据上两者裁定，落盘供报告用
```

`AnalysisAgent.run()`：从 `decision` 取两字段填入 `Diagnosis`（缺失取默认），逻辑与现有取 `need_reproduce` 等字段一致。

`core/agents/coordinator.py` — 新增一个分流判定（第 6 步「Golden-set 记录」之前）：

```python
def _gate_confidence(self, diagnosis: Diagnosis | None) -> None:
    """低置信/证据不足 → 标记待人工复核，抑制自动写库为 CONFIRMED。"""
    if diagnosis is None:
        return
    low = diagnosis.confidence == "low"
    insufficient = diagnosis.evidence_sufficiency == "insufficient"
    # 矛盾信号：说证据不足却又不复测 → 强制转人工
    contradictory = insufficient and not diagnosis.need_reproduce
    diagnosis.needs_human_review = low or insufficient or contradictory
```

- 第 6 步写库条件收紧：`if diagnosis and diagnosis.is_new_bug and diagnosis.bug_summary and not diagnosis.needs_human_review:` 才走现有 `CONFIRMED`-向 `gs.add()`；否则**要么不写、要么以 `HYPOTHESIS` 入库**（与已有 `BadCaseStatus.HYPOTHESIS` 对齐——`BadCase.new()` 默认即 HYPOTHESIS，天然契合，只需在写库时不强升级状态）。
- `_save_diagnosis` / `_load_diagnosis`：payload 增 `confidence` / `evidence_sufficiency` / `needs_human_review` 三键（读回带默认，旧文件兼容）。
- 报告：`needs_human_review=True` 时，`ReportAgent` 的 summary 里置顶「⚠️ 低置信结论，需人工复核」（在 `_analysis_task`/report summary 注入标记，或在 `TestReport` 增一个只读视图字段）。

### 改动清单（✅ 2026-10-02 完成）
- [x] `core/agents/subagents.py`：扩 `DECISION_SCHEMA` 两 property；`AnalysisAgent.run()` 填两字段；`DECISION_TOOL_INSTRUCTION` 补措辞；`ReportAgent.run()` 把两维透给报告 LLM。
- [x] `core/plugins/models.py`：`Diagnosis` 增 `confidence`/`evidence_sufficiency`/`needs_human_review`（默认值兼容）。
- [x] `core/agents/coordinator.py`：新增 `_gate_confidence()`，在落盘/写库前调用；写库条件收紧（仅高置信+证据充分+未标复核才落 CONFIRMED，否则 HYPOTHESIS）；`_save/_load_diagnosis` 增三键。
- [x] 报告侧：低置信结论置顶复核标记——实现为 `_prepend_review_banner()` **代码确定性兜底**（不只靠 LLM prompt），防假绿是硬保证。

### 自测点（✅ 全绿）
- [x] `tests/test_confidence_gating.py`（假 client，16 例）：高置信+证据充分→落库 CONFIRMED；低置信→落 HYPOTHESIS、`needs_human_review=True`、报告有横幅；证据不足且 need_reproduce=false→强制转人工；旧决策无新字段→取默认、行为同当前。
- [x] `_save/_load_diagnosis` 往返一致（含新字段）+ 旧文件缺键取默认。
- [x] 全量 **87 passed** + demo mock 跑通（exit=0）。

---

## P9 · 收敛闭环 & 复测停滞检测

### 目标
把 `Coordinator` 复测循环的停机准则从「唯一硬上限 `MAX_REPRODUCE_ROUNDS`」升级为「三类停机准则并列 + 停滞检测」：证据充分（P8）/ 证据无增量（P9）/ 预算越界（P5）任一命中即停，硬上限退居兜底。

### 接口签名

`core/agents/coordinator.py` — 新增证据指纹与停滞判定：

```python
def _evidence_fingerprint(self, raw: list[RawData]) -> tuple:
    """把当前证据抽象成可比较的指纹，用于判断复测有无实质增量。
    维度示例：collector 种类集合 + 各 payload 里关键指标/日志签名的规整化摘要。
    只求「稳定可比」，不求精确——两轮指纹相同即视为无增量。"""

def _reproduce_should_stop(
    self, prev_fp: tuple, new_fp: tuple, diagnosis: Diagnosis,
) -> tuple[bool, str]:
    """返回 (是否停机, 原因)。原因取值：
    'sufficient'（证据充分/高置信，P8）/ 'stall'（无证据增量，P9）/
    'budget'（预算越界，P5）/ ''（继续复测）。"""
```

复测循环改造（现有 `run()` 第 4 步 `while True`）：
- 进入复测前记录 `prev_fp = self._evidence_fingerprint(raw)`。
- 每轮 `reproduce()` 得到 `extra_raw`、`raw.extend()` 后，算 `new_fp`。
- 调 `_reproduce_should_stop(prev_fp, new_fp, diagnosis)`：
  - `stall` → 跳出循环，`stage_errors` / 报告标注「复测未带来新证据，提前收敛转人工」，并置 `diagnosis.needs_human_review=True`（复用 P8 字段）。
  - `budget` → 跳出，走 P5 的诚实收尾（复用 P5 的 `budget.exceeded()`）。
  - `sufficient` 或 `''` → 维持现有逻辑（继续或正常退出）。
- `MAX_REPRODUCE_ROUNDS` 保留为最后兜底上限（现有 `reproduce_rounds < MAX_REPRODUCE_ROUNDS` 判断不删）。

> 依赖：P8（`needs_human_review`、`confidence`）、P5（`budget`）。故执行顺序 P8 → P5 → P9。若单独先做 P9，可先只实现 `stall` 一类，`sufficient`/`budget` 留占位，后续接上。

### 改动清单（✅ 2026-10-02 完成）
- [x] `core/agents/coordinator.py`：新增 `_evidence_fingerprint()`（结构性指纹，忽略值噪声）+ `_reproduce_should_stop()`（budget > stall > sufficient 优先级）；复测循环接入停滞检测；停机时标注 + 强制置 `needs_human_review`。
- [x] 报告侧：`_prepend_stall_banner()` 收敛止损横幅，与 P5 预算止损、P8 复核横幅区分三种收尾。
- [x] P8 咬合：`_gate_confidence` 的 `needs_human_review` 改为 OR 既有值，不覆盖 P9 上游置的 True。

### 自测点（✅ 全绿）
- [x] `tests/test_reproduce_convergence.py`（9 例，假 client + 假 executor）：
  - 每轮返回同结构证据 → 指纹不变 → 在 `MAX_REPRODUCE_ROUNDS` 前因 `stall` 提前跳出、报告标注收敛止损、`needs_human_review=True`、结构化 stage_errors。
  - 每轮带新 collector → 指纹持续变 → 正常复测到硬上限（不误判停滞、不转人工）。
  - `_evidence_fingerprint` 同结构稳定相等 / 新 collector / 新 result 键均不等；`_reproduce_should_stop` 四分支优先级正确。
- [x] 现有复测行为回归绿（兜底上限仍生效）。
- [x] 全量 **108 passed** + demo mock exit=0。
- **实现要点（忠于设计）**：证据指纹走结构性摘要而非 payload 原值——否则每轮时间戳必让指纹不等、停滞永不触发。这是 P9 真正生效的关键。

---

## P10 · 运行审计目录 & 阶段门禁

### 目标
把平铺在 `workspace_path` 下的产物收拢进自包含的 `runs/run-{timestamp}/`，加阶段门禁（gate）与 fail-closed。`workspace_path=None` 时行为完全不变。

### 目录结构

```
{workspace_path}/
└── runs/
    └── run-{YYYYMMDD_HHMMSS}/
        ├── run_meta.json      # 插件 / 需求 / 开始时间 / 最终状态 / test_id
        ├── gates.json         # 各阶段门禁状态（见下）
        ├── raw_data.json      # 沿用现有产物
        ├── raw_data_round{N}.json
        ├── diagnosis.json
        ├── trace.json         # P5 产出
        ├── report.md
        └── .checkpoints/      # 沿用现有 CheckpointStore 机制，置于运行目录内
```

### 接口签名

`core/agents/coordinator.py` — 运行目录与 gate（`workspace_path` 存在时启用）：

```python
def _run_dir(self, test_id: str):
    """返回本次运行的目录 Path（runs/run-{test_id}），首次调用时创建。
    workspace_path 为空 → 返回 None（旧行为，产物仍走原 _artifact_path）。"""

def _write_gate(self, stage: str, status: str, ready: bool, note: str = "") -> None:
    """向 gates.json 追加/更新一条门禁记录。status: pass/fail/degraded。"""

def _check_gate(self, stage: str) -> bool:
    """检查某前置阶段 gate 是否 pass。用于下一阶段 fail-closed 判断。"""
```

- `_artifact_path()` 改造：`workspace_path` 存在时，产物落到 `_run_dir(test_id)` 下而非 workspace 根（保持文件名不变）；`workspace_path=None` 时返回 None（完全同现在）。
- 每个 stage 完成后调 `_write_gate(stage, ...)`：产物就绪→`pass`，降级→`degraded`，关键产物缺失→`fail`。
- **fail-closed 关键点**：执行阶段产出空 `raw`（一条都没采到）时，`analyze` 阶段启动前 `_check_gate("execute")` 为 fail → **不硬跑分析出空结论**，直接标注阻塞、`stage_errors` 记结构化原因，报告如实说明（对齐 CLAUDE.md「拿不到就明确说拿不到」的红线）。
- `run_meta.json` 收尾写最终状态（success / degraded / aborted_budget / aborted_stall / blocked）。
- checkpoint：`CheckpointStore` 的根路径改为指向运行目录（`_run_dir`），resume 时先定位最新 `run-*` 目录再续跑。保持 `restore_state`/`resume` 现有逻辑不变。

### 改动清单
- [ ] `core/agents/coordinator.py`：新增 `_run_dir()` / `_write_gate()` / `_check_gate()`；`_artifact_path` 指向运行目录；各 stage 写 gate；analyze 前 fail-closed 判断；`run_meta.json` 收尾。
- [ ] checkpoint 根路径指向运行目录；resume 定位最新 `run-*`（`CheckpointStore` 初始化点调整）。
- [ ] 报告侧：blocked/aborted 状态如实标注。

### 自测点
- [ ] `tests/test_run_directory.py`：配 workspace 跑 mock → 生成 `runs/run-*/` 含 run_meta/gates/各产物；执行阶段 raw 为空 → analyze gate 记 fail-closed、报告标注阻塞、不产伪结论。
- [ ] `workspace_path=None` → 行为与当前完全一致（强回归保护）。
- [ ] 现有 `test_checkpoint.py` 全绿（不破坏续跑；resume 能定位运行目录）。
- [ ] 全量单测绿。

---

## 附：档二（记忆库向量检索）保留方案

后续升级时启用，本轮不实现：
- 存储：`sqlite-vss` 或 `chromadb` 本地库；或轻量 embedding（如本地模型 / API）+ 余弦。
- 对 `symptom` / `root_cause` 做语义向量，替代纯 LIKE 子串匹配。
- 保持「JSON 是唯一真相源、索引可重建」不变——向量索引同样可从 JSON 重建。
- 接口预留：`GoldenSetIndex.search_semantic(query, top_k)`，与现有 `search()` 结果融合排序。
