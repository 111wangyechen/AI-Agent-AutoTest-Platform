# AI Agent 自动化测试平台

[![Tests](https://github.com/YOUR_USERNAME/ai-agent-test-platform/actions/workflows/test.yml/badge.svg)](https://github.com/YOUR_USERNAME/ai-agent-test-platform/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **核心价值**：把资深测试工程师"分析日志 → 分层定位 → 给根因结论"的认知过程 Agent 化

**定位**：基于多 AI Agent 协作的 AIOT 设备声学与功能自动化测试平台  
**业务场景**：科大讯飞实习期间负责的 AIOT 设备（智慧主机、AI 台灯）测试自动化  
**当前状态**：✅ 通用内核 + 主控/5 子 Agent 编排已落地；✅ 双插件（声学 + AI 台灯）验证「换插件即换测试域」；✅ **53 项单测**（mock 秒级）+ 真 LLM 验证脚本 + Checkpoint/Resume + Agent 驱动漂移研判

---

## 🚀 快速开始

### 前置要求
- Python 3.12+
- Anthropic API Key（从 https://console.anthropic.com/settings/keys 获取）
- （真机测试）Android 设备 + adb

### 安装运行

```bash
# 1. 进入项目目录
cd projects/ai-agent-test-platform

# 2. 配置 API Key（编辑 .env 文件）
ANTHROPIC_API_KEY=sk-ant-api03-你的真实key

# 3. Mock 模式测试（推荐先运行）
PLATFORM_MOCK=1 .venv/Scripts/python.exe examples/demo.py
```

**预期输出**：Agent 自主调用 2-4 次工具，给出分层故障定位结论

详细说明见 [QUICKSTART.md](QUICKSTART.md)

---

## 📦 当前实现（通用内核 + 双插件）

### ✅ 通用内核（与测试域无关）

- **工具系统** (`core/tools/`)：`Tool` 抽象 + `ToolRegistry`，把「给 Claude 的 schema」和「执行函数」绑定，按名字派发。
- **插件契约** (`core/plugins/`)：`TestPlugin` ABC 定义 5 个扩展点（用例模板/采集器/指标/研判规则/报告）+ dataclass 数据模型 + 注册表。
- **Agent 编排** (`core/agents/`)：`LLMAgent` 基类（Opus 4.8 + adaptive thinking + 手动工具循环）→ 5 子 Agent → `Coordinator` 主控串行编排，artifact 逐段传递，某段失败降级续跑。

**5 扩展点 ↔ 5 子 Agent 一一对应**：
| 扩展点 | 子 Agent | 保真度 |
|---|---|---|
| 用例模板 | 用例设计 | 确定性（可复现） |
| 采集器 | 执行 | 确定性（不烧 LLM） |
| 研判规则 | **结果分析** ⭐ | 真 LLM 多轮工具循环（皇冠明珠） |
| 报告 | 报告生成 | 真 LLM 单轮 |
| — | 需求分析 | 真 LLM 单轮 |

### ✅ 两个场景插件（证明「换插件即换测试域」）

**声学插件** (`core/plugins/acoustic/`)：智能语音终端唤醒/声学链路。3 工具（adb/logcat/serial）+ 7 层检查点 + 故障知识库。分层定位方法论：串口 RX 字节数区分"真没醒"vs"链路故障"。

**AI 台灯插件** (`core/plugins/lamp/`)：WiFi 吞吐 + 通话稳定性。两个采集器都解析真实工具产物：
- `iperf3_throughput`：**真解析 8/19 真实 LL10 数据**（平均 26.5Mbps、峰值重传 88/s、3 秒拥塞断流）
- `call_interop`：**解析 autocall 长跑工具的 rounds CSV**（cmcc_vchat "通话中自发重启"缺陷复现工具，聚合接通率/掉话/卡死/自发重启轮次）

**同一个 `Coordinator.run()`，只换插件，测试域即切换，内核零改动。**

### 📊 验证结果

```
✓ 53 项单测通过（工具系统/插件契约/iperf3 解析器/checkpoint/漂移检测/执行器派发）
✓ PreflightAgent 真调 LLM 三场景验证 3/3 通过
✓ demo.py 回归：分析结论等价 MVP（定位到 PCM 资源竞争）
✓ demo_plugin_swap.py：声学↔台灯切换，同一 run()
✓ iperf3 解析器数字校验：26.5Mbps / 2600 重传 / 88 峰值 / 3 秒断流
```

### 🧪 测试策略（"测试工具本身怎么测"）

这是一个 AI 测试平台——**谁来测这个平台本身**？答案：**把系统切成两半，分开测**。

| 层 | 组件 | 测法 |
|----|------|------|
| **确定性层** | 指标计算 / 日志解析 / checkpoint 存取 / 漂移检测 / 执行器派发 | 传统单元测试 + `assert ==`（mock 模式秒级） |
| **非确定性层** | LLM Agent 推理 / 用例生成 / 根因分析 / 漂移研判 | golden set + schema 断言 + **不断言措辞，只断言关键结论** |
| **降级路径** | checkpoint 损坏 / 缺参数 / 网络掉线 | 故障注入测试 |
| **真 LLM 验证** | PreflightAgent 三种漂移场景 | 独立脚本 `test_preflight_llm.py`，不阻塞 CI |

**详细文档**：
- 📖 [测试哲学](docs/TEST_PHILOSOPHY.md) - 为什么这样测（对应面试稿"测试工具的测试"）
- 📖 [测试实践指南](docs/TESTING_GUIDE.md) - 怎么写、怎么跑
- 📖 [Checkpoint/Resume 设计](docs/CHECKPOINT_DESIGN.md) - 断点续跑 + 漂移研判架构

**跑测试**：
```bash
# mock 模式（秒级，CI 用）
PLATFORM_MOCK=1 pytest tests/ -v

# 真调 LLM 验证 PreflightAgent（分钟级，可选）
PLATFORM_MOCK=1 python test_preflight_llm.py
```

**CI/CD**：
- ✅ 每次 push/PR 自动跑 pytest（`.github/workflows/test.yml`），Python 3.11/3.12 双版本
- ✅ 手动/scheduled 跑真 LLM 验证（`.github/workflows/llm-verify.yml`），不阻塞 PR
- ✅ 覆盖率报告作为 artifact 保留

### 🗂️ 项目结构

```
core/
├── config.py                # 环境配置（网关鉴权/设备/mock）
├── tools/                   # 通用内核·工具系统
│   ├── base.py              # Tool 抽象
│   └── registry.py          # ToolRegistry
├── plugins/                 # 通用内核·插件系统
│   ├── base.py              # TestPlugin 契约（5 扩展点）
│   ├── models.py            # dataclass 数据模型 + artifact
│   ├── registry.py          # PluginRegistry
│   ├── acoustic/            # 声学插件（tools/knowledge/__init__）
│   └── lamp/                # AI 台灯插件（iperf3 + autocall CSV 双真解析）
└── agents/                  # 通用内核·Agent 系统
    ├── base.py              # LLMAgent 基类
    ├── subagents.py         # 5 子 Agent
    └── coordinator.py       # 主控编排
examples/
├── demo.py                  # 仅结果分析（便宜，回归用）
├── demo_pipeline.py         # 完整 5 段流水线
└── demo_plugin_swap.py      # 插件切换（核心卖点）
tests/                       # 23 项单测
```

---

## 架构设计

### 整体架构：主控 Agent + 5 个子 Agent

```
                    ┌──────────────────┐
                    │  主控 Agent       │
                    │  (Coordinator)   │
                    └────────┬─────────┘
                             │ 派发任务、汇总结果
            ┌────────────────┼────────────────┐
            │                │                │
    ┌───────▼───────┐ ┌─────▼──────┐ ┌──────▼───────┐
    │ 需求分析 Agent │ │用例设计Agent│ │ 执行 Agent    │
    └───────────────┘ └─────────────┘ └──────┬───────┘
            │                │                │
            └────────────────┼────────────────┘
                             │
                    ┌────────▼─────────┐
                    │ 结果分析 Agent    │ ← 核心价值
                    │ (分层故障定位)    │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ 报告生成 Agent    │
                    └──────────────────┘
```

**工具注册表**（所有 Agent 共享）：
```
┌────────────────────────────────────────┐
│         Tool Registry                  │
├────────────┬───────────┬───────────────┤
│ adb 工具    │ 串口工具   │ 音频采集/分析  │
│ 日志解析    │ 系统信息   │ 故障知识库     │
└────────────┴───────────┴───────────────┘
```

### 多 Agent 协作机制（对照 KamaClaude S7 学习）

| 设计点 | KamaClaude 实现 | 本平台实现 | 设计原因 |
|--------|----------------|-----------|----------|
| **多 Agent 派生** | `core/subagent/spawn_agent` | 主控派生 5 个子 Agent | **核心架构，必须实现** |
| **上下文隔离** | 每个子 Agent 独立上下文 | 复用机制，各子 Agent 独立 | 避免执行阶段污染分析阶段 |
| **工具系统** | `core/tools/registry.py` | 复用架构，注册采集/分析工具 | 核心能力，必须实现 |
| **任务编排** | 顺序/并行调度 | 主控按测试流程顺序派发 | 测试是串行流程 |
| **结果汇总** | 父 Agent 接收子 Agent 输出 | 主控汇总 5 个子 Agent 结果 | 生成完整测试报告 |
| **协议层** | JSON-RPC over TCP | 内部函数调用 + 外部 MCP | 简化内部通信 |
| **权限系统** | `core/permissions/` 审批流 | 暂不实现 | 测试工具危险操作少 |

---

## 5 个子 Agent 详细设计

### 1. 需求分析 Agent
**职责**：解析测试需求，识别设备类型、测试场景、关键指标  
**输入**：测试需求描述（自然语言）  
**输出**：结构化测试需求（设备型号、测试类型、验收标准）  
**工具集**：
- 需求文档解析
- 历史用例检索
- 设备信息查询（adb）

**System Prompt 核心**：
> 你是测试需求分析专家。给定测试需求描述，识别：① 设备型号和硬件配置；② 测试类型（声学/性能/功能）；③ 关键指标和验收标准；④ 测试环境要求。

---

### 2. 用例设计 Agent
**职责**：基于需求生成测试用例矩阵  
**输入**：结构化测试需求  
**输出**：测试用例列表（参数组合、预期结果）  
**工具集**：
- 用例模板库
- 测试矩阵计算（正交表/全排列）
- 边界值分析

**System Prompt 核心**：
> 你是测试用例设计专家。基于需求生成覆盖完整的测试矩阵。对于声学测试，变量包括：声源距离、角度、信噪比、噪声类型。使用正交表优化用例数量。

---

### 3. 执行 Agent
**职责**：自动化执行测试用例，采集原始数据  
**输入**：测试用例列表  
**输出**：原始测试数据（日志、音频、指标）  
**工具集**（用户明确）：
- **adb 命令**
- **串口通信**
- **八路音频采集**
- **音频分析工具**
- **日志获取**

**System Prompt 核心**：
> 你是测试执行引擎。按用例顺序执行，采集原始数据。每 10 条用例设置 Checkpoint。如果连续失败超过阈值，立即终止并保存现场。

---

### 4. 结果分析 Agent ⭐️ **核心价值**
**职责**：分层故障定位，推理根因，给出修复建议  
**输入**：原始测试数据 + 故障知识库  
**输出**：诊断报告（根因 + 证据链 + 修复建议）  
**工具集**（用户明确，比执行 Agent 多了专门排查工具）：
- **adb 命令**
- **串口专门排查工具** ← 后续作业详细说明
- **八路音频采集**
- **音频分析工具**
- **日志获取**
- **故障知识库检索**（30+ 故障模式）

**System Prompt 核心**：
> 你是故障诊断专家。分层分析：① 硬件层（芯片、麦克风）；② 驱动层（ALSA、HAL）；③ 中间件层（3A 算法、音频流）；④ 云端层（识别、语义）。对每层进行健康检查，定位故障区间，匹配知识库推理根因。

**核心价值体现**：
- **分层诊断逻辑**：从硬件到云端的 7 层检查点（对应你二面讲的链路）
- **知识库驱动**：30+ 故障模式（症状 + 日志特征 + 根因 + 修复建议）
- **自动取证**：通过串口排查工具深入硬件层，比人工快 10 倍

---

### 5. 报告生成 Agent
**职责**：生成可读性强的测试报告  
**输入**：测试用例 + 原始数据 + 诊断报告  
**输出**：Markdown/HTML 测试报告  
**工具集**：
- Markdown 生成
- 图表绘制（指标趋势、故障分布）
- 报告模板渲染

**System Prompt 核心**：
> 你是测试报告专家。生成结构化报告：① 执行摘要（通过率、关键问题）；② 详细结果（每条用例）；③ 根因分析（分层诊断结果）；④ 修复建议。

---

## 项目结构

```
ai-agent-test-platform/
├── core/
│   ├── tools/                          # 工具系统（模块 C）
│   │   ├── base.py                     # Tool 基类
│   │   ├── registry.py                 # 工具注册表
│   │   └── builtin/                    # 内置工具
│   │       ├── adb_tool.py             # adb 命令
│   │       ├── serial_tool.py          # 串口通信
│   │       ├── serial_diagnose_tool.py # 串口专门排查工具
│   │       ├── audio_capture_tool.py   # 八路音频采集
│   │       ├── audio_analysis_tool.py  # 音频分析
│   │       ├── log_parser_tool.py      # 日志获取与解析
│   │       └── knowledge_search_tool.py # 故障知识库检索
│   ├── agent/                          # Agent 系统（模块 E + F）
│   │   ├── base.py                     # Agent 基类
│   │   ├── coordinator.py              # 主控 Agent（模块 F）
│   │   └── subagents/                  # 5 个子 Agent
│   │       ├── requirement_analyzer.py # 需求分析
│   │       ├── testcase_designer.py    # 用例设计
│   │       ├── executor.py             # 执行
│   │       ├── result_analyzer.py      # 结果分析（核心）
│   │       └── report_generator.py     # 报告生成
│   ├── spawn.py                        # 子 Agent 派生机制（模块 E）
│   ├── context.py                      # 上下文管理（模块 B）
│   ├── session.py                      # 会话持久化（模块 B）
│   └── knowledge/                      # 故障知识库
│       ├── fault_patterns.json         # 30+ 故障模式
│       └── checkpoints.json            # 7 层检查点定义
├── tests/
│   ├── test_registry.py                # 工具注册表测试
│   ├── test_tools.py                   # 工具执行测试
│   ├── test_spawn.py                   # 子 Agent 派生测试
│   ├── test_subagents.py               # 各子 Agent 单元测试
│   └── integration/
│       └── test_e2e_acoustic.py        # 端到端声学测试
├── examples/
│   └── acoustic_test_demo.py           # 声学测试完整示例（面试 demo）
├── docs/
│   ├── ARCHITECTURE.md                 # 架构设计文档（面试材料）
│   ├── SUBAGENT_DESIGN.md              # 5 个子 Agent 详细设计
│   └── FAULT_PATTERNS.md               # 故障模式说明
├── pyproject.toml                      # 依赖管理（uv）
└── README.md                           # 本文件
```

---

## 实现路线图（调整后）

### 阶段 1：工具系统基础（对应学习模块 C）
**目标**：实现工具注册、调用、结果返回的完整链路

- [ ] `tools/base.py`：定义 `Tool` 基类
- [ ] `tools/registry.py`：工具注册表
- [ ] 实现 **7 个内置工具**（adb / 串口 / 串口排查 / 音频采集 / 音频分析 / 日志解析 / 知识库检索）
- [ ] 测试：所有工具注册成功、调用正常

**验收**：能注册 7 个工具，调用成功，pytest 全绿

---

### 阶段 2：协议对齐（对应学习模块 A）
**目标**：搞清楚"平台内部用什么协议"，避免 MCP 误区

- [ ] 明确：平台内部 Agent ↔ 工具 = 函数调用
- [ ] 设计：如果后期要接外部工具（如 Appium），才用 MCP
- [ ] 文档：在 `ARCHITECTURE.md` 写清楚协议分层

**验收**：能讲清楚"我的平台哪层用什么协议，为什么"

---

### 阶段 3：多 Agent 派生机制（对应学习模块 E）⭐️ **关键**
**目标**：实现主控派生子 Agent、上下文隔离、结果回传

- [ ] `spawn.py`：子 Agent 派生函数（学习 KamaClaude S7）
- [ ] `agent/base.py`：Agent 基类（独立上下文、工具集）
- [ ] 测试：主控能派生子 Agent，各自独立运行

**验收**：主控派生 1 个子 Agent，子 Agent 执行任务返回结果，上下文互不污染

---

### 阶段 4：上下文与会话（对应学习模块 B）
**目标**：实现测试会话的持久化、上下文组装

- [ ] `session.py`：创建会话、保存状态、恢复会话
- [ ] `context.py`：组装 system prompt + 工具列表 + 历史消息
- [ ] Checkpoint：每个测试步骤保存快照

**验收**：测试中断后能恢复、上下文能正确传递

---

### 阶段 5：实现 5 个子 Agent（核心业务逻辑）
**目标**：实现 5 个子 Agent 的业务逻辑

- [ ] `subagents/requirement_analyzer.py`：需求分析
- [ ] `subagents/testcase_designer.py`：用例设计
- [ ] `subagents/executor.py`：执行
- [ ] `subagents/result_analyzer.py`：**结果分析（核心，重点实现）**
- [ ] `subagents/report_generator.py`：报告生成
- [ ] 测试：每个子 Agent 单独测试通过

**验收**：5 个子 Agent 各自能独立运行，输入输出符合设计

---

### 阶段 6：主控编排（对应学习模块 F）
**目标**：实现主控 Agent 调度 5 个子 Agent 的完整流程

- [ ] `agent/coordinator.py`：主控 Agent
- [ ] 任务分发：按测试流程顺序派发任务给 5 个子 Agent
- [ ] 结果汇总：收集 5 个子 Agent 的输出，生成最终报告
- [ ] 错误处理：某个子 Agent 失败时的降级策略

**验收**：端到端跑通一个声学测试用例，从需求到报告全自动

---

### 阶段 7：完善与 Demo（面试准备）
- [ ] 完整示例：`examples/acoustic_test_demo.py`
- [ ] 架构文档：`docs/ARCHITECTURE.md` + `SUBAGENT_DESIGN.md`
- [ ] README 补充：使用说明、设计亮点、性能指标
- [ ] 录制 Demo 视频：5 分钟展示平台运行（面试可放）

---

- **语言**：Python 3.11+
- **AI SDK**：Anthropic SDK (Claude API)
- **工具层**：adb / 串口 pyserial / 日志正则解析
- **测试框架**：pytest
- **数据持久化**：JSON 文件（session/结果）

---

## 项目结构

```
ai-agent-test-platform/
├── core/
│   ├── agent.py              # Agent 主循环（学习自 KamaClaude loop.py）
│   ├── tools/
│   │   ├── registry.py       # 工具注册表（对照 S3）
│   │   ├── base.py           # 工具基类（定义接口）
│   │   └── builtin/          # 内置工具
│   │       ├── adb_tool.py       # adb 命令采集
│   │       ├── log_parser.py     # 日志解析
│   │       └── audio_capture.py  # 音频采集
│   ├── context.py            # 上下文组装（对照 S3/S6）
│   ├── session.py            # 会话管理（简化版 S4）
│   └── knowledge/            # 故障知识库
│       └── fault_patterns.json   # 30+ 故障模式
├── tests/
│   ├── test_registry.py      # 工具注册表测试
│   ├── test_tools.py         # 工具执行测试
│   └── integration/          # 端到端测试
├── examples/
│   └── acoustic_test_demo.py # 声学测试示例（面试 demo）
├── docs/
│   ├── ARCHITECTURE.md       # 架构设计文档（面试材料）
│   └── FAULT_PATTERNS.md     # 故障模式说明
├── pyproject.toml            # 依赖管理（uv）
└── README.md                 # 本文件
```

---

## 实现路线图

### 阶段 1：工具系统基础（对应学习模块 C）
**目标**：实现工具注册、调用、结果返回的完整链路

- [ ] `tools/base.py`：定义 `Tool` 基类（name / description / schema / execute）
- [ ] `tools/registry.py`：工具注册表（register / get / list_tools）
- [ ] `tools/builtin/adb_tool.py`：实现第一个采集工具
- [ ] 测试：注册 3 个工具，调用成功，结果符合 schema

**验收**：能注册工具、调用工具、拿到结果，打印工具列表

---

### 阶段 2：协议对齐（对应学习模块 A）
**目标**：搞清楚"平台内部用什么协议"，避免 MCP 误区

- [ ] 明确：平台内部 = 函数调用（不是 JSON-RPC，不是 MCP）
- [ ] 设计：如果后期要接外部工具（如 Appium），才用 MCP
- [ ] 文档：在 `ARCHITECTURE.md` 写清楚协议分层

**验收**：能讲清楚"我的平台哪层用什么协议，为什么"

---

### 阶段 3：上下文与会话（对应学习模块 B）
**目标**：实现测试会话的持久化、上下文组装

- [ ] `session.py`：创建会话、保存状态、恢复会话
- [ ] `context.py`：组装 system prompt + 工具列表 + 历史消息
- [ ] 简化版 checkpoint：每个测试步骤保存快照（JSON）

**验收**：测试中断后能恢复、上下文能正确传递

---

### 阶段 4：Agent 主循环（对应学习模块 F）
**目标**：实现完整的 Agent 推理循环

- [ ] `agent.py`：初始化 → 组装上下文 → 调用模型 → 解析工具调用 → 执行 → 回填结果 → 循环
- [ ] 测试：给定测试需求，Agent 能自动调用工具、分析日志、输出结论

**验收**：端到端跑通一个声学测试用例

---

### 阶段 5：知识库与诊断（业务核心）
**目标**：实现基于知识库的智能故障诊断

- [ ] `knowledge/fault_patterns.json`：录入 30+ 故障模式（从你二面的材料提取）
- [ ] 诊断逻辑：匹配日志特征 → 检索知识库 → AI 推理根因
- [ ] 测试：给定故障日志，能输出根因 + 修复建议

**验收**：诊断准确率 > 80%（在已知故障集上）

---

### 阶段 6：完善与 Demo（面试准备）
- [ ] 完整示例：`examples/acoustic_test_demo.py`（从需求到报告）
- [ ] 架构文档：`docs/ARCHITECTURE.md`（讲设计权衡的材料）
- [ ] README 补充：使用说明、设计亮点、性能指标
- [ ] 录制 Demo 视频：3 分钟展示平台运行（面试可放）

---

## 面试话术准备

### Q: 为什么要自己做 Agent 平台，不用 LangChain？
**A**：LangChain 是通用对话框架，测试场景有特殊需求：① 需要 checkpoint/resume 保证长流程测试可恢复；② 需要工具执行失败时的多级 retry；③ 需要分层故障定位的推理链。我对照 KamaClaude（工业级 Agent harness）学习架构，只实现测试平台需要的部分，避免通用框架的冗余。

### Q: 你说的"主控 + 5 子 Agent"在代码里怎么体现？
**A**：初期是单 Agent 架构（阶段 1-5），5 子 Agent 是规划中的扩展。当前 Agent 内部按"需求理解 → 用例生成 → 执行 → 分析 → 报告"五阶段编排，每个阶段调用不同的工具集和 prompt。如果后期要做真正的多 Agent，会参考 KamaClaude S7 的 `spawn_agent` 机制实现派生与隔离。（诚实口径，不过度吹嘘）

### Q: 状态漂移识别率 100% 怎么实现的？
**A**：每个测试步骤保存 checkpoint（当前设备状态 + 日志缓冲区 + 测试变量），恢复时对比 checkpoint 和实际设备状态，如果发现硬件重启、固件版本变化、进程异常退出等 10 种漂移特征，会触发警报并终止测试。100% 是在已定义的漂移类型上，而不是所有可能的异常——这是诚实的工程指标表达。

---

## 开发规范

- **测试先行**：每个模块写完立即写测试，保证覆盖率 > 80%
- **代码 review**：每次作业交给老师 review，修改后再合并
- **文档同步**：架构变更立即更新 `ARCHITECTURE.md`
- **版本控制**：每个阶段完成后打 tag（`v0.1-tools`, `v0.2-context` ...）

---

**当前状态**：项目创建，准备开始阶段 1（工具系统）  
**下一步**：学习 KamaClaude S3 工具系统 → 实现 `tools/base.py` + `registry.py` → 作业：注册 3 个采集工具
