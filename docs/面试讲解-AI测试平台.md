# AI Agent 自动化测试平台 · 面试讲解材料

> 面试前过一遍。本文按「先讲清楚，再看代码」组织：每个模块先说**为什么这么设计**，
> 再贴**真实代码**逐行讲，最后给**面试怎么答**。所有代码都是本项目里真跑通的，不是伪代码。

---

## 目录

1. [一句话 / 30 秒 / 2 分钟三档电梯陈述](#1-电梯陈述)
2. [架构全景：一张图看懂](#2-架构全景)
3. [核心设计决策（面试官最爱问的 why）](#3-核心设计决策)
4. [代码精讲 ① 工具系统（通用内核底座）](#4-代码精讲--工具系统)
5. [代码精讲 ② 插件契约（通用性的落点）](#5-代码精讲--插件契约)
6. [代码精讲 ③ Agent 基类与主控编排](#6-代码精讲--agent-基类与主控编排)
7. [代码精讲 ④ 两个插件如何证明通用性](#7-代码精讲--两个插件)
8. [三个真实数据源（不是造的）](#8-三个真实数据源)
9. [autocall 工具的硬核踩坑（加分项）](#9-autocall-工具的硬核踩坑)
10. [现场 Demo 脚本](#10-现场-demo-脚本)
11. [高频追问与标准答法](#11-高频追问)
12. [诚实边界（别翻车）](#12-诚实边界)

---

## 1. 电梯陈述

**一句话**：
> 我做了一个「通用内核 + 场景插件」的多 Agent 自动化测试平台，把资深测试工程师「看日志→分层定位→给根因」的认知过程 Agent 化，换个插件就能从声学测试切到 WiFi/通话测试，内核代码一行不改。

**30 秒**：
> 平台是主控 Agent 编排 5 个子 Agent（需求分析/用例设计/执行/结果分析/报告生成）。核心价值在结果分析 Agent——它用 Claude Opus 4.8 自主调用工具、多轮取证、做分层故障定位。通用内核和测试域完全解耦，测试域封装成插件：声学插件负责语音链路，AI 台灯插件负责 WiFi 吞吐和通话稳定性。两个插件跑同一个 `Coordinator.run()`，证明了架构的通用性。数据都是真的——iperf3 是我实测的台灯吞吐日志，通话是接了我自己写的缺陷复现长跑工具。

**2 分钟**（加背景 + 亮点 + 结果）：
> 我实习负责 AIOT 设备（智慧主机、AI 台灯）测试。传统自动化是写死的脚本，遇到没预料的故障就只能人工看日志。我把这套「分层定位」方法论固化成了 AI Agent。
>
> 架构上分三层：**通用内核**（工具系统 + 插件契约 + Agent 编排，和测试域无关）、**场景插件**（每个测试域实现 5 个扩展点）、**演示层**。5 个扩展点和 5 个子 Agent 一一对应。
>
> 保真度我做了取舍：结果分析 Agent 是皇冠明珠，用真 LLM 多轮工具循环；需求/报告用真 LLM 单轮；用例设计和执行是确定性的、不烧 LLM——因为测试要可复现，执行阶段不该有随机性，这也省成本。
>
> 亮点是「换插件即换测试域」：声学插件定位到「PCM 资源竞争被误报成未唤醒」，台灯插件从真实 iperf3 数据里定位到「平均带宽达标但秒级断流是偶发卡顿的根源」，还从通话长跑 CSV 里把「通话中自发重启」定级为 P0 设备缺陷、和网络抖动分开——全程内核零改动。26 个单测全绿，端到端 mock 跑通。

---
## 2. 架构全景

```
                         ┌───────────────────────┐
                         │   Coordinator 主控     │  core/agents/coordinator.py
                         │   (持有一个 plugin)     │
                         └───────────┬───────────┘
                串行编排，artifact 逐段传递（某段失败降级续跑）
   ┌──────────┬──────────────┬──────────────┬──────────────┬──────────────┐
   ▼          ▼              ▼              ▼              ▼              
┌──────┐  ┌────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│需求  │→ │用例设计│ → │执行采集   │ → │结果分析★ │ → │报告生成  │   core/agents/subagents.py
│分析  │  │(确定性)│   │(确定性)   │   │(真LLM多轮)│   │(真LLM单轮)│
└──────┘  └────────┘   └──────────┘   └──────────┘   └──────────┘
   │                       │              │
   │ 都依赖抽象，不认识具体测试域            │
   ▼                       ▼              ▼
┌────────────────────────────────────────────────────────────────┐
│                    TestPlugin 契约（5 扩展点）                    │  core/plugins/base.py
│  generate_cases / collectors / metrics / analysis_* / report     │
└──────────────────────────┬─────────────────────────────────────┘
              ┌─────────────┴─────────────┐
        ┌─────▼─────┐               ┌─────▼─────┐
        │ 声学插件   │               │ AI台灯插件 │   core/plugins/{acoustic,lamp}/
        │ adb/logcat │               │ iperf3     │
        │ /serial    │               │ /autocall  │
        └─────┬─────┘               └─────┬─────┘
              └──────────┬────────────────┘
                   ┌─────▼─────┐
                   │ ToolRegistry │  core/tools/  —— 所有工具的注册与派发
                   └───────────┘
```

**三层职责**：
- **通用内核**（`core/tools` `core/plugins` `core/agents`）：与测试域无关，是可复用的引擎。
- **场景插件**（`core/plugins/acoustic` `core/plugins/lamp`）：实现契约即接入，各自封装工具/知识/prompt。
- **演示层**（`examples/`）：三个 demo，从「仅分析」到「完整流水线」到「插件切换」。

**5 扩展点 ↔ 5 子 Agent 一一对应**（这是设计的骨架，一定要背下来）：

| 扩展点 | 插件方法 | 消费它的子 Agent | 保真度 |
|---|---|---|---|
| 用例模板 | `generate_cases()` | 用例设计 | 确定性 |
| 采集器 | `collectors()` | 执行 | 确定性 |
| 指标 | `metrics()` | 执行/分析 | 数据 |
| **研判规则** | `analysis_prompt()`+`analysis_tools()`+`checkpoints()`+`knowledge_base()` | **结果分析 ★** | 真 LLM 多轮 |
| 报告 | `report_template()` | 报告生成 | 真 LLM 单轮 |

---

## 3. 核心设计决策

面试官会追着问「你为什么这么设计」。这几个 why 提前想清楚：

**Q：为什么是「通用内核 + 插件」，不直接写死声学测试？**
> 因为我实习要测多种设备（语音终端、AI 台灯），如果每种都写一套 Agent，方法论就无法复用。抽出通用内核后，新测试域只要实现一个插件类，主控和 5 个子 Agent 完全复用。这也是我把「分层定位」这个跨设备通用的方法论沉淀下来的方式。

**Q：为什么执行 Agent 不用 LLM？**
> 刻意的取舍。测试执行必须**可复现**——同样的用例跑两次要得到同样的采集动作，不能有 LLM 的随机性。而且执行阶段就是「按用例遍历、调采集器」，没有需要推理的地方。用 LLM 反而又慢又贵又不可控。我把 LLM 用在真正需要推理的地方：结果分析。这个「哪里该用 AI、哪里不该用」的判断，本身就是工程成熟度。

**Q：为什么结果分析是「多轮工具循环」而不是一次性给结论？**
> 因为分层定位本质是「取证→假设→再取证」的迭代过程。Agent 先抓 logcat 看走到哪一层，发现音频流打开失败，再调串口诊断排除硬件，再查进程占用……每一步的下一个动作取决于上一步的结果。这正是资深工程师排障的思维，一次性 prompt 给不出来。

**Q：为什么用 dataclass 不用 Pydantic？**
> 参照我学习的 KamaClaude（一个工业级 Agent harness）的极简依赖原则。内部数据模型用 stdlib dataclass 就够了，不引第三方。只有真正需要校验外部输入时才上 Pydantic。少一个依赖少一份维护成本。

---
## 4. 代码精讲 ① 工具系统

**它解决什么**：Claude 调工具需要两样东西——给它看的 JSON schema，和真正执行的 Python 函数。`Tool` 把这两样绑在一起，`ToolRegistry` 管理一组工具。这是整个内核最底层的复用点。

### `core/tools/base.py` —— Tool 抽象

```python
@dataclass
class Tool:
    """一个可被 Agent 调用的工具：schema + 执行函数。"""
    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., dict[str, Any]]

    def to_claude(self) -> dict[str, Any]:
        """转成 Claude Messages API 的 tools[] 元素。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def run(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """执行工具，参数不匹配时兜底为结构化错误，不让整轮循环崩掉。"""
        try:
            return self.func(**tool_input)
        except TypeError as e:
            return {"status": "error", "result": f"参数错误: {e}"}
        except Exception as e:  # 工具内部异常也要转成结果回传给 Agent
            return {"status": "error", "result": f"工具执行异常: {e}"}
```

**讲解要点**：
- `to_claude()` 和 `run()` 分离——**同一个 Tool 既是「给 LLM 的描述」又是「可执行体」**，schema 和实现不会漂移。
- `run()` 里两层 except 是**关键的健壮性设计**：工具抛异常绝不能让 Agent 的多轮循环崩掉，而是把异常转成 `{"status":"error"}` 回传给 LLM，让它自己决定换个工具或调整参数。面试可以强调「工具失败是 Agent 的正常输入，不是程序的异常」。

### `core/tools/registry.py` —— 注册表 + 派发

```python
class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def claude_schema(self) -> list[dict[str, Any]]:
        """给 messages.create(tools=...) 的定义列表。"""
        return [t.to_claude() for t in self._tools.values()]

    def execute(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """按名字执行工具，未知工具返回结构化错误。"""
        tool = self._tools.get(name)
        if tool is None:
            return {"status": "error", "result": f"未知工具: {name}"}
        return tool.run(tool_input)
```

**讲解要点**：
- 每个 Agent 持有**自己的** ToolRegistry —— 这就是「子 Agent 工具集隔离」的落点。执行 Agent 拿到的是采集器工具集，结果分析 Agent 拿到的是取证工具集，互不干扰。
- `claude_schema()` 一行把整个注册表转成 API 要的格式，Agent 循环里直接 `tools=registry.claude_schema()`。

> **踩坑故事（可以主动讲，显得真实）**：早期我把工具写成扁平的 `core/tools.py` 模块，后来建了 `core/tools/` 包想做注册表，结果 Python 优先导入**包**而不是**模块**，`DISPATCH` 符号找不到，自检直接报 `has no attribute 'DISPATCH'`。这次重构我一步到位删掉旧模块、只留包，彻底消除了命名遮蔽。这类「包 vs 模块」的坑很能体现你踩过真实的 Python 工程问题。

---
## 5. 代码精讲 ② 插件契约

**它解决什么**：定义「一个测试域要向平台提供什么」。内核只依赖这个抽象类，永远不认识声学或台灯的任何细节。这就是「通用」二字的代码保证。

### `core/plugins/base.py` —— TestPlugin ABC

```python
class TestPlugin(ABC):
    """一个测试域插件。子类实现 5 扩展点即可接入平台。"""
    name: str = ""          # 插件唯一标识
    device_desc: str = ""   # 被测设备描述，拼进各 Agent 的 system prompt

    # 扩展点 1：用例模板
    @abstractmethod
    def generate_cases(self, req: TestRequirement) -> list[TestCase]: ...

    # 扩展点 2：采集器（执行阶段确定性调用）
    @abstractmethod
    def collectors(self) -> list[Tool]: ...

    # 扩展点 3：指标
    @abstractmethod
    def metrics(self) -> list[MetricDef]: ...

    # 扩展点 4：研判规则（核心）
    @abstractmethod
    def analysis_prompt(self) -> str: ...      # 分层定位方法论 system prompt
    @abstractmethod
    def analysis_tools(self) -> list[Tool]: ...  # 结果分析 Agent 的取证工具
    @abstractmethod
    def checkpoints(self) -> list[Checkpoint]: ...
    @abstractmethod
    def knowledge_base(self) -> list[FaultPattern]: ...

    # 扩展点 5：报告
    @abstractmethod
    def report_template(self) -> str: ...
```

**讲解要点**：
- 用 `ABC` + `@abstractmethod`：新插件如果漏实现任何一个方法，**实例化时就报错**，不会等到运行时才炸。这是「让错误尽早暴露」。
- `analysis_prompt / analysis_tools / checkpoints / knowledge_base` 四个方法都归在扩展点 4「研判规则」下——因为**结果分析是平台的核心价值**，一个测试域最需要沉淀的就是它的排障方法论、检查点、故障知识库。

### `core/plugins/models.py` —— 段间流转的 artifact

5 个子 Agent 之间传的都是这几个 dataclass，与测试域无关：

```python
@dataclass
class TestRequirement:   # 需求分析产出
    raw: str; device: str = ""; test_type: str = ""
    metrics: list[str] = field(default_factory=list); acceptance: str = ""

@dataclass
class RawData:           # 执行产出
    case_id: str; collector: str; payload: dict = field(default_factory=dict)

@dataclass
class Diagnosis:         # 结果分析产出（核心）
    text: str; rounds: int = 0; tool_calls: list = field(default_factory=list)

@dataclass
class TestReport:        # 报告产出 + 全过程留痕
    plugin: str; requirement: TestRequirement
    cases: list = ...; raw_data: list = ...; diagnosis: Diagnosis | None = None
    report_md: str = ""; stage_errors: list = ...  # 降级时记录哪段出错
```

**讲解要点**：`stage_errors` 是降级设计——测试平台不能因为某一段崩了就整个哑掉，哪段失败就记下来、尽力往下跑，最终报告里如实标注。

---
## 6. 代码精讲 ③ Agent 基类与主控编排

这是整个平台的心脏。**手动工具循环**是面试最可能深挖的点，务必能默写逻辑。

### `core/agents/base.py` —— LLMAgent 手动工具循环

先看鉴权（这里有个真实细节）：

```python
def _build_client() -> anthropic.Anthropic:
    """按 config 组装客户端，兼容官方直连与公司网关两种鉴权。"""
    kwargs: dict[str, Any] = {}
    if config.auth_token:          # 公司网关：Bearer token
        kwargs["auth_token"] = config.auth_token
    elif config.api_key:           # 官方直连：api key
        kwargs["api_key"] = config.api_key
    if config.base_url:            # 公司网关自定义 base_url
        kwargs["base_url"] = config.base_url
    return anthropic.Anthropic(**kwargs)
```

> **真实细节**：公司走的是自建网关（讯飞 one.iflytek.com），用 `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL`，不是官方的 `ANTHROPIC_API_KEY`。我实测网关支持 adaptive thinking + tool_use，代码两种鉴权都兼容。这个能讲出来说明你真的跑通过、不是纸上谈兵。

核心循环：

```python
def run(self, task, on_event=None) -> dict:
    messages = [{"role": "user", "content": task}]
    tool_calls_log = []
    has_tools = len(self.registry) > 0
    rounds = 0

    while rounds < self.max_rounds:          # 防失控：最多 12 轮
        rounds += 1
        kwargs = dict(
            model=self.model, max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},    # Opus 4.8 自适应思考
            system=self.system_prompt, messages=messages,
        )
        if has_tools:
            kwargs["tools"] = self.registry.claude_schema()
        resp = self.client.messages.create(**kwargs)

        if has_tools and resp.stop_reason == "tool_use":
            # 把 assistant 的 tool_use 块原样加回消息历史
            messages.append({"role": "assistant", "content": resp.content})
            # 执行本轮所有工具，结果一次性回传
            tool_results = self._run_tool_uses(resp.content, tool_calls_log, emit)
            messages.append({"role": "user", "content": tool_results})
            continue                          # 进入下一轮

        # 不再调工具 = Agent 给出结论
        final_text = "".join(b.text for b in resp.content if b.type == "text")
        return {"text": final_text, "rounds": rounds, "tool_calls": tool_calls_log}
```

**面试默写级讲解**（这段一定要能讲）：
1. **循环条件**：`stop_reason == "tool_use"` 就继续，否则 Agent 已给结论、退出。
2. **消息历史怎么攒**：assistant 的 `tool_use` 块要**原样**（`resp.content`）加回历史，然后把工具结果作为一个 `role:"user"` 消息追加。这个「一问一答」的结构是 Messages API tool use 的硬性要求。
3. **为什么一次性回传本轮所有工具**：一轮里 Claude 可能同时要调多个工具（并行 tool_use），必须全部执行完、把所有 `tool_result` 放一个 user 消息里回传，不能分批。
4. **max_rounds 兜底**：防止工具循环失控烧钱，12 轮还没结论就人工介入。
5. **adaptive thinking**：Opus 4.8 的自适应思考，旧的 `budget_tokens` 写法会 400 报错——这是我踩过的坑。

```python
def _run_tool_uses(self, content, tool_calls_log, emit):
    results = []
    for block in content:
        if block.type != "tool_use":
            continue
        result = self.registry.execute(block.name, dict(block.input))
        tool_calls_log.append({"name": block.name, "input": dict(block.input), "result": result})
        results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(result)})
    return results
```

> `tool_use_id` 必须回填——它是这一次工具调用的唯一标识，API 靠它把结果对应回请求。

### `core/agents/coordinator.py` —— 主控编排

```python
class Coordinator:
    def __init__(self, plugin: TestPlugin) -> None:
        self.plugin = plugin
        self.requirement_agent = RequirementAgent(plugin)   # 5 个子 Agent
        self.case_agent = CaseAgent(plugin)                 # 都只依赖 plugin
        self.executor_agent = ExecutorAgent(plugin)
        self.analysis_agent = AnalysisAgent(plugin)
        self.report_agent = ReportAgent(plugin)

    def run(self, requirement: str, on_event=None) -> TestReport:
        errors = []
        # 每段都 try/except：失败记入 errors，尽力续跑（降级）
        try: req = self.requirement_agent.run(requirement, on_event)
        except Exception as e:
            errors.append(f"需求分析失败: {e}")
            req = TestRequirement(raw=requirement, device=self.plugin.device_desc)
        # ...用例设计→执行→结果分析→报告，同样的降级模式...
        return TestReport(plugin=self.plugin.name, requirement=req, ...,
                          diagnosis=diagnosis, stage_errors=errors)

    def analyze_only(self, task, on_event=None) -> Diagnosis:
        """只跑结果分析（等价 MVP 行为，便宜快，演示核心价值）。"""
        return self.analysis_agent.run(task, on_event=on_event)
```

**讲解要点**：
- **`Coordinator.__init__` 只吃一个 `plugin`**，5 个子 Agent 全从它构造。换插件 = 换 Coordinator 的构造参数，一行代码。这就是「换插件即换测试域」的字面实现。
- **`analyze_only`** 是我特意留的便宜入口：只跑皇冠明珠那一段，一次 LLM 会话，用于回归和演示核心价值，不用每次都烧完整 5 段。

---
## 7. 代码精讲 ④ 两个插件

「换插件即换测试域」不是口号，看两个插件的对照就懂了。**两个插件实现的是同一个契约**，Coordinator 对它们一视同仁。

### 声学插件（`core/plugins/acoustic/__init__.py`）

```python
class AcousticPlugin(TestPlugin):
    name = "acoustic"
    device_desc = "智能语音终端（灵犀屏/AIUniBOX），关注唤醒与声学链路"

    def collectors(self):     return _tools.ALL_TOOLS   # adb / logcat / serial
    def analysis_tools(self): return _tools.ALL_TOOLS
    def checkpoints(self):    return CHECKPOINTS         # 7 层检查点
    def knowledge_base(self): return FAULT_PATTERNS      # 故障模式库
    def analysis_prompt(self):return _ANALYSIS_PROMPT    # 分层定位方法论

default_registry.register(AcousticPlugin())  # import 即注册
```

它的 `analysis_prompt` 沉淀的就是我二面讲的 7 层链路方法论（节选）：

```
被测设备是智能语音终端。语音链路有 7 个检查点：
1.唤醒触发 KW_LOGIC  2.音频流开启 audio_hw_primary  ... 7.资源释放
关键区分：设备"真没醒"还是"链路故障"——必须用 serial_diagnose 看串口 RX 字节数 N：
  N>0 链路活（问题在驱动/应用），N=0 物理链路死，绝不能上报为唤醒率=0，要交硬件。
```

### AI 台灯插件（`core/plugins/lamp/__init__.py`）

**同样的方法名，完全不同的内容**：

```python
class LampPlugin(TestPlugin):
    name = "lamp"
    device_desc = "AI 台灯（LL10/LL20pro），关注 WiFi 吞吐稳定性与手机通话互通"

    def collectors(self):     return _tools.ALL_TOOLS   # iperf3 / autocall
    def analysis_tools(self): return _tools.ALL_TOOLS
    def checkpoints(self):    return CHECKPOINTS         # WiFi+通话 检查点
    def knowledge_base(self): return FAULT_PATTERNS      # WiFi/通话故障库
    def analysis_prompt(self):return _ANALYSIS_PROMPT    # WiFi/网络分层定位

default_registry.register(LampPlugin())
```

### 对照表（这张表最能讲清「通用」）

| | 声学插件 | 台灯插件 |
|---|---|---|
| 采集器 | adb / logcat / serial_diagnose | iperf3_throughput / call_interop |
| 检查点 | 唤醒→音频流→3A→采集→云端→语义→释放 | 关联→吞吐→稳定性→拨号→接通→上下行音频 |
| 核心判据 | 串口 RX 字节数区分真没醒/链路死 | 吞吐归零秒数 + 重传峰值 + reboot 轮次 |
| 故障层级 | 硬件/驱动/框架/应用/云端 | 网络/系统/框架/应用 |

**内核（Coordinator + 5 子 Agent + 工具系统）对这两列一行代码都不用改。** 这就是答「你这个通用性怎么证明」的杀手锏。

### 插件切换 demo（`examples/demo_plugin_swap.py` 的核心）

```python
JOBS = [
    ("acoustic", "唤醒测试连播100次部分未唤醒，分层定位..."),
    ("lamp",     "AI台灯语音交互卡顿，跑WiFi吞吐和通话互通，分层定位..."),
]
for name, task in JOBS:
    coord = Coordinator(default_registry.get(name))   # 唯一变化：插件名
    diag = coord.analyze_only(task)                    # 同一个方法
```

---
## 8. 三个真实数据源

这是这个项目最硬的地方：**没有一个数据是编的**。面试官问「你的 mock 数据哪来的」，你能答「都是我实测的」。

### ① 声学 logcat（真实故障模式）

mock 样本是真实的「唤醒成功但音频流打开失败」故障序列：
```
KW_LOGIC: wake up signal detected, score=0.82        ← 唤醒触发正常
audio_hw_primary: open pcm failed: Device or resource busy  ← 音频流卡住
DeviceAccessService: audio stream open give up after 3 retries
```
分析 Agent 的真实结论：**PCM 设备被上一轮会话占用未释放，连播时后一次唤醒抢占失败，被误报成「未唤醒」——应记为「唤醒成功但会话失败」**。这个「统计口径」洞察是资深工程师才有的。

### ② iperf3 WiFi 吞吐（8/19 实测 LL10）

数据源：`docs/0819对比测试/LL10/电脑收台灯发/`，600 秒双向压测真日志。解析器：

```python
def _parse_iperf3(raw: str) -> dict:
    """解析 iperf3 文本，核心指标：带宽/重传/拥塞窗口。"""
    interval_re = re.compile(
        r"sec\s+[\d.]+\s+\w+\s+([\d.]+)\s+([KMG]?)bits/sec\s+(\d+)\s+([\d.]+)\s+([KMG]?)Bytes"
    )
    samples = [...]   # 逐秒采样
    return {"avg_mbps":..., "min_mbps":..., "max_retr_per_sec":...,
            "total_retr":..., "stall_seconds": <吞吐归零秒数>, "verdict":...}
```

**要背下来的真实数字**（单测 `test_lamp_iperf.py` 锁死了这些值）：
- 平均带宽 **26.5 Mbps**、总重传 **2600 次**、单秒峰值重传 **88 次**
- 出现 **3 秒吞吐归零**（382~385s 拥塞崩溃，Cwnd 从 180KB 塌到 1.43KB）

分析 Agent 的真实洞察：**「平均达标（26.5≥20）掩盖了秒级断流，这才是偶发卡顿的根源」**——单位换算陷阱（Kbits vs Mbits）解析器也处理了。

### ③ autocall 通话长跑 CSV（我自己写的缺陷复现工具）

数据源：`autocall_tool_20260821/` 产出的 `rounds_<run_id>.csv`。解析器聚合出接通率/掉话/卡死/**自发重启**轮次：

```python
def _parse_call_csv(raw: str) -> dict:
    rows = list(csv.DictReader(io.StringIO(raw)))
    reboots = [int(r["round"]) for r in rows if r.get("reboot") == "1"]
    dropped = [int(r["round"]) for r in rows if "掉话" in (r.get("note") or "")]
    return {"rounds":..., "answer_rate":..., "reboot_rounds": reboots,
            "dropped_rounds": dropped, "peak_thread":...,   # 接通态实测≈91
            "verdict": _call_verdict(reboots, hangs, dropped, total)}
```

分析 Agent 的真实结论：**把「通话中自发重启」定级为 P0 设备缺陷、和 iperf3 的秒级断流（P2 网络）分开定级**，还建议用 autocall 长跑 ≥50 通复现二分定位。两条缺陷线分开推进——这正是成熟测试工程师的判断。

---
## 9. autocall 工具的硬核踩坑

`autocall.py` 是我为复现 AI 台灯「音视频通话中偶发整机重启」缺陷写的长跑工具。它本身就是**独立的加分项**——如果面试聊到「你做过什么有挑战的测试」，这些踩坑最能体现深度。每一条都是真实教训。

**① 日志块缓冲导致假阳性 → 改用 /proc 实时信号**
> `/tmp/log.txt` 被 libc 块缓冲，实测卡在 32768 字节边界、落后设备时钟 4 分钟。早期我用它判「喂狗断流 106s = 重启」，结果是假阳性，设备根本没重启。**教训：任何有缓冲的日志都不能做实时判定**。改用 `/proc` 和 `netstat` 的即时信号。

**② vchat 线程数是精确三态判据（实测标定）**
> 空闲 **53** / 响铃 **54** / 已接通 **91** 个线程；UDP 媒体端口数：空闲 0、通话 4。这是我用 `probe.py` 定标采出来的。比解析日志可靠得多——状态判定直接 `ls /proc/PID/task | wc -l`。（这个 91 就是台灯插件 mock CSV 里 `thr_peak` 的来源）

**③ 多设备环境用 MAC 锁定防漂移**
> 实验室有多台台灯，hostname 都叫 virgo、都跑 cmcc_vchat。只凭「有 cmcc_vchat 进程」认设备，第 37 通漂到了别台（.106），拿它的 PID/uptime 和 .108 比，**误报整机重启**，其实 .108 一直在通话。**教训：进程名不能做设备身份**。改用 `cat /sys/class/net/wlan0/address` 锁 MAC，换 IP 必须 MAC 匹配才认。真重启时 MAC 不变仍能找回同一台。

**④ 挂断键坐标随界面变**
> 响铃界面挂断键 (158,414)，通话中界面 (89,419)。用错坐标挂不断，通话一直挂着，导致后续每通都拨不出去。所以我合成了两个不同的 tap 事件 bin。

**⑤ 双通道交叉确认重启**
> 判「自发重启」用两个独立信号交叉确认：`uptime 归零` **或** `vchat PID 变`。单一信号容易误判，双通道才敢定级为真缺陷。

**面试怎么用这段**：这些踩坑证明你不是「跑跑脚本」，而是**和真实的、脏的、会骗你的系统较量过**——假阳性、设备漂移、缓冲滞后，全是资深测试才会遇到并解决的问题。把 autocall 接进 AI 平台后，这些方法论就变成了 Agent 能读懂的 CSV 判据。

---
## 10. 现场 Demo 脚本

如果能现场跑，按这个顺序（都在 mock 模式，不用连设备，走公司网关真实调 LLM）：

```bash
cd D:/junior-to-senior/projects/ai-agent-test-platform

# ① 核心价值：只跑结果分析 Agent（最快，~1 分钟）
PLATFORM_MOCK=1 .venv/Scripts/python.exe examples/demo.py

# ② 完整 5 段流水线：需求→用例→执行→分析→报告
PLATFORM_MOCK=1 .venv/Scripts/python.exe examples/demo_pipeline.py

# ③ 杀手锏：同一 run() 换插件，声学↔台灯
PLATFORM_MOCK=1 .venv/Scripts/python.exe examples/demo_plugin_swap.py

# ④ 单测全绿（26 项，秒级）
.venv/Scripts/python.exe -m pytest tests/ -q
```

**讲解节奏建议**：
- 先跑 ①，边跑边指着屏幕说「看，它先抓 logcat，发现音频流失败，再调串口排除硬件，再查进程——这就是分层定位」。
- 再跑 ③，强调「同样的代码，我只换了插件名，测试域从声学变成了 WiFi/通话」。
- 最后 `pytest` 收尾，证明工程完整性。

**真机演示**（周六实验室）：把 `PLATFORM_MOCK=1` 换成 `DEVICE_ADDR=<灯IP>:5555`，声学插件的 adb/logcat/serial 就打真机；台灯 iperf3 走 `adb shell iperf3 -c <server>`，通话跑 `autocall.py` 产 CSV 再喂进来。

---

## 11. 高频追问

**Q：这和 LangChain / AutoGPT 有什么区别？**
> 那些是通用对话/Agent 框架。我的平台是**面向测试领域**的：数据模型是测试 artifact（用例/原始数据/诊断/报告），5 个子 Agent 对应测试流水线，插件契约的 5 个扩展点是「测试域」的抽象。我没有用通用框架，是想精确控制每个子 Agent 的保真度（哪个用 LLM、哪个确定性），通用框架给不了这种控制粒度。

**Q：结果分析 Agent 会不会瞎编（幻觉）？**
> 三重约束：① system prompt 明确要求「主动调工具收集证据，不要凭空推断」；② 结论结构固定要求引用具体日志行/指标；③ 工具调用全程记录在 `tool_calls`，可追溯。而且 mock 模式可回放——同样输入能复现。真要上生产会再加人工审核和置信度评分。

**Q：成本多少？**
> 单次结果分析约 3~6 次工具调用、一次会话，成本几毛钱。对比人工分析日志 30~60 分钟，ROI 很高。而且我把烧钱的 LLM 只用在分析段，执行/用例都是确定性的，进一步压成本。

**Q：多个子 Agent 怎么通信？**
> 当前是进程内串行编排，Coordinator 通过 dataclass artifact 逐段传递。我参照了 KamaClaude（工业级 Agent harness）的设计——它把 subagent/tools 目录留成了空 blueprint，我等于把它只预留位置的多 Agent 模式落地实现了。如果要跨进程，会上 MCP / JSON-RPC，但内部函数调用对串行测试流程足够且更简单。

**Q：为什么执行 Agent 叫「Agent」但不调 LLM？**
> 命名上它是流水线的一个阶段（stage），职责是「遍历用例、调采集器、产出 RawData」。我刻意让它确定性——这是设计选择，不是偷懒。可以反问面试官：测试执行你希望它可复现还是每次随机？

**Q：如果新来一个测试域（比如摄像头），要改哪里？**
> 只写一个新插件类：实现 5 个扩展点（用例模板/采集器/指标/研判规则/报告），`default_registry.register()` 一行注册。内核（工具系统/Agent/Coordinator）零改动。这就是插件架构的价值。

---

## 12. 诚实边界

面试**主动**说清楚边界，比被戳穿强一百倍。这个项目吃过「过度包装」的亏（之前面试被质疑项目不成熟），所以口径要稳：

- **通话工具的真机执行**：`autocall.py` 是真的、跑过真机、复现过缺陷；但接进平台的是它的 **CSV 产物解析**。平台不在 mock 里现场拨电话——现场长跑用 autocall 本体，平台负责读它的结果做分析。**这么说**：「吞吐和通话都是真实工具的真实产物，平台做的是把它们变成 Agent 能分层研判的输入。」
- **多 Agent 是进程内串行**，不是分布式。别吹「分布式多 Agent」。
- **KamaClaude 我精读到 S0**，S1-S7 在学。别假装全懂——就说「我对照它的架构设计，实现了测试平台需要的部分」。
- **端到端跑通是 mock 模式**（工具回落样本，但 LLM 是真实调用公司网关）。真机验证是周六的计划。

**一句兜底话术**：「这是我实习方法论的工程化沉淀，核心链路真跑通、数据真实、单测覆盖；有些是为演示做的合理简化，我能说清哪些是真的、哪些是简化。」

---

## 附：最该背熟的 5 个数字/事实

1. **5 扩展点 ↔ 5 子 Agent**，结果分析是核心。
2. **iperf3：26.5Mbps 平均 / 88 峰值重传 / 3 秒断流**。
3. **vchat 线程三态：53 空闲 / 54 响铃 / 91 接通**。
4. **保真度取舍**：分析=真LLM多轮，执行/用例=确定性。
5. **换插件即换测试域**：`Coordinator(default_registry.get(name))` 一行。

