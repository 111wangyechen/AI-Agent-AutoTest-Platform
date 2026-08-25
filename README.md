# AI Agent 自动化测试平台

[![Tests](https://github.com/111wangyechen/AI-Agent-AutoTest-Platform/actions/workflows/test.yml/badge.svg)](https://github.com/111wangyechen/AI-Agent-AutoTest-Platform/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 基于多 AI Agent 协作的 AIoT 设备自动化测试平台

## 概述

AI Agent 自动化测试平台是一个通用的测试框架，通过 AI Agent 协作实现从需求分析到测试报告生成的全流程自动化。平台采用插件化架构，支持不同测试领域的快速扩展。

**核心特性**：
- 🤖 **多 Agent 协作**：5 个专业化 Agent（需求分析、用例设计、执行、结果分析、报告生成）分工协作
- 🔌 **插件化架构**：通过插件扩展支持不同测试域，换插件即换测试场景
- 🔄 **智能复测机制**：分析 Agent 判断证据不足时自动触发复测补采
- 📚 **Golden-set 知识库**：沉淀历史 bug，支持相似问题快速匹配
- 💾 **断点续跑**：Checkpoint 机制支持测试中断后从断点恢复
- 🔍 **状态漂移检测**：自动识别设备状态变化，保证测试环境一致性

**当前状态**：
- ✅ 通用内核 + 5-Agent 流水线架构
- ✅ 声学测试插件（智能语音设备唤醒测试）
- ✅ AI 台灯插件（WiFi 吞吐 + 通话稳定性）
- ✅ 60 项单元测试全部通过
- ✅ CI/CD 自动化流水线

---

## 快速开始

### 前置要求
- Python 3.11+
- Anthropic API Key（从 https://console.anthropic.com/settings/keys 获取）
- （可选）Android 设备 + adb（用于真机测试）

### 安装运行

```bash
# 1. 克隆项目
git clone https://github.com/111wangyechen/AI-Agent-AutoTest-Platform.git
cd AI-Agent-AutoTest-Platform

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key（创建 .env 文件）
echo "ANTHROPIC_API_KEY=sk-ant-api03-你的真实key" > .env

# 4. Mock 模式测试（推荐）
PLATFORM_MOCK=1 python examples/demo.py
```

**预期输出**：Agent 自主调用 2-4 次工具，给出分层故障定位结论

详细说明见 [QUICKSTART.md](QUICKSTART.md)

---

## 架构设计

### 整体架构

```
                    ┌──────────────────┐
                    │  主控 Agent       │
                    │  (Coordinator)   │
                    └────────┬─────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
    ┌───────▼───────┐ ┌─────▼──────┐ ┌──────▼───────┐
    │ 需求分析 Agent │ │用例设计Agent│ │ 执行 Agent    │
    └───────────────┘ └─────────────┘ └──────┬───────┘
            │                │                │
            └────────────────┼────────────────┘
                      ┌──────▼───────┐
                      │ 结果分析 Agent │ ← 核心：分层故障定位
                      └──────┬───────┘
                             │
                      ┌──────▼───────┐
                      │ 报告生成 Agent │
                      └──────────────┘
```

### 核心组件

**1. 通用内核（与测试域无关）**
- **工具系统** (`core/tools/`)：Tool 抽象 + ToolRegistry，按名字派发工具调用
- **插件系统** (`core/plugins/`)：TestPlugin 定义 5 个扩展点（用例模板/采集器/指标/分析工具/报告模板）
- **Agent 系统** (`core/agents/`)：LLMAgent 基类 + 5 个子 Agent + Coordinator 主控编排
- **Golden-set** (`core/golden_set/`)：bad-case 知识库（JSON 存储 + SQLite 索引）

**2. 插件示例**
- **声学插件** (`core/plugins/acoustic/`)：智能语音设备唤醒率测试、7 层检查点分层定位
- **AI 台灯插件** (`core/plugins/acoustic/lamp_plugin.py`)：WiFi 吞吐测试、通话稳定性测试

**3. 智能复测机制**

当结果分析 Agent 判断证据不足以定论时，会自动触发复测：

```python
# 分析 Agent 输出复测决策
{
  "need_reproduce": true,
  "reproduce_hint": "抓取四路音频 + 全量日志",
  "target_evidence": ["four_channel_audio", "full_serial_log"]
}

# Coordinator 自动回到执行阶段，按 hint 补采证据
# 最多复测 2 轮，每轮证据独立保存
```

**4. Golden-set 知识库**

沉淀真实测试遇到的 bug，下次遇到相似问题快速匹配：

```python
# 分析 Agent 可调用工具查询历史 bad-case
query_golden_set(
    device_model="AI台灯·LL20",
    tags=["唤醒", "闲聊模式"],
    log_signatures=["LISTEN_MSG_VAD_END", "pcm_busy"]
)
# 返回：相似度排序的历史 case + 根因 + 修复建议
```

---

## 项目结构

```
ai-agent-test-platform/
├── core/
│   ├── config.py                # 环境配置
│   ├── tools/                   # 工具系统
│   │   ├── base.py              # Tool 抽象
│   │   └── registry.py          # ToolRegistry
│   ├── plugins/                 # 插件系统
│   │   ├── base.py              # TestPlugin 契约
│   │   ├── models.py            # 数据模型
│   │   ├── registry.py          # 插件注册表
│   │   └── acoustic/            # 声学测试插件
│   │       ├── base.py          # 声学插件基类
│   │       ├── lamp_plugin.py   # AI 台灯插件
│   │       ├── shared_tools.py  # 通用分析工具
│   │       └── shared_knowledge.py  # 故障知识库
│   ├── golden_set/              # bad-case 知识库
│   │   ├── models.py            # BadCase 数据模型
│   │   ├── store.py             # JSON 存储
│   │   ├── index.py             # SQLite 索引
│   │   ├── api.py               # 统一 API
│   │   └── tools.py             # Agent 工具封装
│   └── agents/                  # Agent 系统
│       ├── base.py              # LLMAgent 基类
│       ├── subagents.py         # 5 个子 Agent
│       └── coordinator.py       # 主控编排
├── golden_set/                  # Golden-set 数据目录
│   └── bad_cases/               # 历史 bad-case（JSON）
├── examples/
│   ├── demo.py                  # 结果分析示例
│   ├── demo_pipeline.py         # 完整流水线示例
│   └── demo_plugin_swap.py      # 插件切换示例
├── tests/                       # 60 项单元测试
│   ├── test_golden_set.py       # Golden-set 测试
│   ├── test_checkpoint.py       # Checkpoint 测试
│   ├── test_plugins.py          # 插件测试
│   └── ...
├── .github/workflows/           # CI/CD
│   ├── test.yml                 # 主测试流水线
│   └── llm-verify.yml           # LLM 验证流水线
├── requirements.txt             # 依赖列表
└── README.md                    # 本文档
```

---

## 测试与验证

### 单元测试

```bash
# Mock 模式（秒级，CI 用）
PLATFORM_MOCK=1 pytest tests/ -v

# 查看覆盖率
PLATFORM_MOCK=1 pytest tests/ --cov=core --cov-report=html
```

**测试覆盖**：
- ✅ 60 项单元测试全部通过
- ✅ 工具系统、插件契约、Golden-set、Checkpoint、执行器派发
- ✅ Python 3.11 + 3.12 双版本验证

### CI/CD

GitHub Actions 自动运行：
- **主测试流水线** (`.github/workflows/test.yml`)：每次 push/PR 自动触发，mock 模式秒级反馈
- **LLM 验证流水线** (`.github/workflows/llm-verify.yml`)：手动/定时触发，真调 LLM 验证

---

## 使用示例

### 示例 1：声学测试（唤醒率）

```python
from core.agents import Coordinator
from core.plugins import default_registry

# 获取声学插件
plugin = default_registry.get("acoustic_lamp")

# 创建主控
coordinator = Coordinator(plugin=plugin, workspace_path="./workspace")

# 运行测试
report = coordinator.run(
    requirement="测试 AI 台灯唤醒率，播放 300 次唤醒词，验收标准 ≥90%"
)

print(report.report_md)  # Markdown 测试报告
```

### 示例 2：插件切换

```python
# 切换到 AI 台灯插件
lamp_plugin = default_registry.get("acoustic_lamp")
coordinator = Coordinator(plugin=lamp_plugin)
report = coordinator.run("测试 WiFi 吞吐性能")

# 同一个 Coordinator，只换插件，测试域即切换
```

### 示例 3：Golden-set 查询

```python
from core.golden_set import GoldenSet

gs = GoldenSet()

# 查询相似历史 bug
results = gs.query(
    device_model="AI台灯·LL20",
    tags=["唤醒", "闲聊模式"],
    log_signatures=["pcm_busy"]
)

# 打印相似度最高的 case
if results:
    top = results[0]
    print(f"相似度: {top['score']}")
    print(f"根因: {top['root_cause']}")
    print(f"修复建议: {top['fix_suggestion']}")
```

---

## 扩展开发

### 添加新插件

1. 继承 `TestPlugin` 基类
2. 实现 5 个扩展点：
   - `generate_cases()` - 用例模板
   - `collectors()` - 采集器注册
   - `metrics()` - 指标定义
   - `analysis_tools()` - 分析工具集
   - `report_template()` - 报告模板

3. 注册插件：
```python
from core.plugins import default_registry

class MyPlugin(TestPlugin):
    name = "my_plugin"
    device_desc = "我的测试设备"
    # ... 实现 5 个扩展点

default_registry.register(MyPlugin())
```

详细说明见 [PLUGIN_DEV_GUIDE.md](docs/PLUGIN_DEV_GUIDE.md)

---

## 技术栈

- **语言**：Python 3.11+
- **AI 模型**：Claude Opus 4.8 (Anthropic)
- **数据存储**：JSON + SQLite
- **测试框架**：pytest
- **CI/CD**：GitHub Actions
- **依赖管理**：pip

---

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

## 联系方式

项目维护者：[@111wangyechen](https://github.com/111wangyechen)

如有问题或建议，欢迎提 [Issue](https://github.com/111wangyechen/AI-Agent-AutoTest-Platform/issues)
