# 演示示例

## demo.py - 基础演示

展示结果分析 Agent 如何分层定位声学链路故障。

### 使用方法

#### 1. Mock 模式（在家测试，不连设备）

```bash
# Windows (Git Bash)
PLATFORM_MOCK=1 .venv/Scripts/python.exe examples/demo.py

# 或设置环境变量后运行
export PLATFORM_MOCK=1
python examples/demo.py
```

**Mock 模式特点**：
- 使用内置样本日志（包含典型故障模式）
- 不需要真实设备和 adb 连接
- 用于验证 Agent 分析逻辑和结论质量

#### 2. 真机模式（实验室/现场测试）

```bash
# 先连接无线 adb
adb connect 192.168.211.5:5555

# 推送串口测试工具（如果需要 serial_diagnose）
adb push uart_test /data/local/tmp/
adb shell chmod +x /data/local/tmp/uart_test

# 指定设备地址运行
DEVICE_ADDR=192.168.211.5:5555 python examples/demo.py
```

### 预期输出

```
╭─────────────────────────────────────────╮
│ AI Agent 测试平台 · 结果分析 Agent 演示 │
╰─────────────────────────────────────────╯
模型：claude-opus-4-8   模式：MOCK（样本日志）

╭─────────────── 排查任务 ───────────────╮
│ 对灵犀屏做唤醒测试时，一口气播放100次   │
│ 唤醒词，日志显示部分未唤醒，但不确定    │
│ 是设备真没醒还是链路故障。              │
│ 请分层定位根本原因，给出结论。          │
╰─────────────────────────────────────────╯

→ 调用工具 logcat_capture  {'tags': 'KW_LOGIC,audio_hw_primary,...'}
  ← success
→ 调用工具 serial_diagnose  {'check_type': 'wakeup'}
  ← success

╭───────────── 分析结论（共 2 轮，2 次工具调用）─────────────╮
│ ## 故障定位结论                                           │
│                                                           │
│ - **到达检查点**：走到了第 2 步（音频流开启）             │
│ - **故障层级**：框架层                                    │
│ - **根本原因**：音频设备被其他进程占用，打开 PCM 失败     │
│ - **关键证据**：                                          │
│   - `KW_LOGIC: wake up signal detected` 说明唤醒触发正常  │
│   - `audio_hw_primary: open pcm failed: Device or ...`   │
│   - 串口 RX=32 bytes 说明硬件链路正常，非物理故障         │
│ - **修复建议**：                                          │
│   1. 用 `ps -A | grep audio` 查找占用进程                │
│   2. kill 占用进程或重启音频服务                          │
│   3. 添加进程互斥检测避免冲突                             │
╰───────────────────────────────────────────────────────────╯
```

### 自定义测试场景

编辑 `demo.py` 中的 `TASK` 变量：

```python
TASK = (
    "灵犀屏连续运行 24 小时后，唤醒率从 95% 降到 60%，"
    "请分析是内存泄漏、串口故障还是云端超时。"
)
```

### 进度回调说明

`on_event(kind, payload)` 回调用于实时展示 Agent 工作流程：

- `kind="tool_call"`: Agent 决定调用某个工具
  - `payload`: `{"name": "工具名", "input": {...}}`
- `kind="tool_result"`: 工具执行完成
  - `payload`: `{"name": "工具名", "result": {...}}`
- `kind="final"`: Agent 给出最终结论
  - `payload`: 结论文本（Markdown）

### 故障排查

#### 401 Authentication Error
```
编辑 .env 文件，设置正确的 API key：
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
```

#### adb 相关错误（真机模式）
```bash
# 检查设备连接
adb devices

# 应该看到：
# 192.168.211.5:5555    device

# 如果显示 unauthorized，在设备上允许调试授权
# 如果显示 offline，重新连接：
adb disconnect
adb connect <IP>:5555
```

#### 串口诊断失败
```bash
# 确保 uart_test 已推送
adb shell ls -l /data/local/tmp/uart_test

# 确保有执行权限
adb shell chmod +x /data/local/tmp/uart_test

# 手动测试串口
adb shell /data/local/tmp/uart_test rxtx /dev/ttyS2
```

## 后续示例计划

- `demo_multi_scenario.py`: 批量测试多个故障场景
- `demo_realtime.py`: 实时监控模式（持续抓日志分析）
- `demo_report.py`: 生成结构化测试报告
