# AI Agent 测试平台 - 下一步行动指南

## ✅ 今天已完成 (8/21)

1. **修复 Import Bug**: 删除了 `core/tools/` 冲突目录，`DISPATCH`/`execute`/`CLAUDE_TOOLS` 现在正确导出
2. **工具 Mock 测试通过**: 三个工具在 mock 模式下全部正常工作
3. **环境文件创建**: `.env` 已创建（需要填入真实 API key）

## 🔴 立即行动：添加 API Key

编辑 `projects/ai-agent-test-platform/.env` 文件：

```bash
# 把 sk-ant-your-key-here 替换成你的真实 key
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
```

从这里获取 API key: https://console.anthropic.com/settings/keys

## 📋 今天剩余任务

### 1. 验证 Agent 分析质量（Mock 模式）

```bash
# 进入项目目录
cd D:\junior-to-senior\projects\ai-agent-test-platform

# Mock 模式运行（不连设备，用样本日志）
PLATFORM_MOCK=1 .venv/Scripts/python.exe examples/demo.py
```

**预期效果**：
- Agent 会调用 2-4 次工具（logcat_capture → serial_diagnose → 可能再次 logcat）
- 最终给出结构化结论：故障层级、根本原因、关键证据、修复建议
- 验证 Agent 是否正确使用"分层定位"方法论

**如果结论质量不佳**，可以调整 `core/agent.py` 的 `SYSTEM_PROMPT`。

### 2. 准备周六实验室演示场景

创建更多测试场景（可选，如果今天有时间）：

```python
# 在 examples/ 下创建新场景
# 场景 1: 网络超时导致云端识别失败
# 场景 2: 麦克风被占用导致音频流打不开
# 场景 3: 串口物理链路故障
```

## 🔬 周六实验室计划 (8/23)

### 准备工作

1. **连接灵犀屏到实验室 WiFi**
2. **获取灵犀屏 IP 地址**：
   ```bash
   adb shell ip addr show wlan0
   # 或在设备上查看 WiFi 设置
   ```
3. **连接无线 adb**：
   ```bash
   adb connect <IP>:5555
   ```

### 真机测试流程

```bash
# 1. 设置设备地址
export DEVICE_ADDR=192.168.211.5:5555

# 2. 推送串口测试工具（如果需要 serial_diagnose）
adb push uart_test /data/local/tmp/
adb shell chmod +x /data/local/tmp/uart_test

# 3. 运行真机测试
python examples/demo.py

# 4. 录制演示视频（面试用）
# - 展示 Agent 自主调用工具的过程
# - 展示最终分析结论的准确性
```

### 验证关键点

- [ ] Agent 能正确调用 adb_tool 查询设备信息
- [ ] logcat_capture 能抓到真实故障日志
- [ ] serial_diagnose 判据准确（RX 字节数 > 0 = 链路活）
- [ ] 分析结论与实际故障原因匹配

## 🚀 后续开发方向（面试前优化）

### P0 优先级（必须完成）
1. **多场景测试**：至少准备 3 个真实故障场景，证明 Agent 泛化能力
2. **演示视频**：录制 2-3 分钟视频，展示从任务描述到结论输出的完整流程
3. **README 完善**：添加架构图、使用示例、技术亮点说明

### P1 优先级（加分项）
1. **用例生成 Agent**：从需求文档自动生成测试用例（展示多 Agent 协同）
2. **报告生成 Agent**：把分析结论转成结构化测试报告（JSON/Markdown）
3. **主控编排层**：实现 Harness Agent 协调多个子 Agent

### P2 优先级（如果时间充足）
1. **插件系统**：把声学测试封装成插件，展示"通用内核+场景插件"架构
2. **Web UI**：简单的 Web 界面展示测试结果
3. **持久化存储**：保存测试历史和分析记录

## 📊 面试准备 Checklist

- [ ] 能流畅演示完整测试流程（从任务到结论）
- [ ] 能解释"分层定位"方法论的技术价值
- [ ] 能说明 Agent 如何自主决策工具调用顺序
- [ ] 能对比"传统自动化"vs"AI Agent 自动化"的差异
- [ ] 准备好应对追问：
  - "如果 Agent 分析错了怎么办？" → 人工审核机制、置信度评分
  - "成本和效率如何？" → 对比人工分析日志的时间
  - "如何保证可复现？" → 工具调用记录、deterministic mock

## 🔧 故障排查

### 如果遇到 API 调用失败
- 检查 `.env` 文件中的 `ANTHROPIC_API_KEY` 是否正确
- 检查网络连接（实验室可能需要代理）
- 查看错误码：401=认证失败，429=速率限制，500=服务端错误

### 如果 Mock 模式正常但真机失败
- 先单独测试 adb 连接：`adb -s <IP>:5555 shell getprop`
- 检查设备是否授权：`adb devices` 应该显示 "device" 而不是 "unauthorized"
- 串口工具确保已推送到正确路径：`adb shell ls -l /data/local/tmp/uart_test`

### 如果 Agent 分析质量不佳
- 调整 `SYSTEM_PROMPT` 增加更多上下文
- 增加 mock 样本的丰富度（更多故障模式）
- 考虑增加 `max_rounds` 限制（当前 12 轮）

## 📝 开发日志

- 2026-08-21: 修复 tools 导入 bug，工具 mock 测试通过
- 2026-08-23 (计划): 实验室真机验证
- 2026-08-27 (目标): 面试前完成至少 3 个场景演示

---

**当前状态**: ✅ 核心功能已实现，等待 API key 验证和真机测试
