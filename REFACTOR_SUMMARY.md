# 声学插件重构完成总结

## 重构目标

将单一声学插件重构为**基于设备的插件架构**，支持多款设备（灵犀屏、AI 台灯、联通中屏）的不同测试流程。

---

## 完成内容

### 1. 通用基类：`AcousticPluginBase`

**文件**：`core/plugins/acoustic/base.py`

**设计**：
- **通用能力**（所有设备共享）：
  - 分层定位方法论（7 层检查点）
  - 故障知识库
  - 分析工具（adb、logcat、串口诊断）
  - 报告模板

- **设备特定能力**（子类实现）：
  ```python
  @abstractmethod
  def audio_playback_tool(self) -> Tool
  def log_collector(self) -> Tool
  def audio_collector(self) -> Tool | None
  def execution_strategy(self) -> ExecutionStrategy
  ```

### 2. AI 台灯插件：`LampPlugin`

**文件**：`core/plugins/acoustic/lamp_plugin.py`

**设备特点**：
- 全量日志自动存储到 `/tmp/log.txt`（1MB 左右）
- 四路音频需要实时抓取，使用 `D:\录音工具\adb-fifo.exe`
- **音频抓取时独占 adb 连接** - 无法同时执行其他 adb 命令

**实现的工具**：
1. **`lamp_audio_playback`** - 音频播放
   - 推送音频文件到设备
   - 使用 aplay/stagefright 播放
   
2. **`lamp_log_from_storage`** - 日志采集
   - 从 `/tmp/log.txt` 读取全量日志
   
3. **`lamp_audio_capture_exclusive`** - 四路音频实时抓取
   - 使用录音工具（独占 adb）
   - 录音文件保存到 `D:\录音工具\`
   - **注意**：需要及时转移并改名

**执行策略**：`sequential_audio_first`
- 流程：播放音频 → (可选)抓取音频 → 读取日志

### 3. 共享资源

**文件重命名**：
- `tools.py` → `shared_tools.py`（通用分析工具）
- `knowledge.py` → `shared_knowledge.py`（故障知识库）

### 4. ExecutorAgent 增强

**文件**：`core/agents/subagents.py`

**新增执行策略支持**：
- `parallel` - 并行执行（灵犀屏）
- `sequential_audio_first` - 串行音频优先（AI 台灯）
- `sequential_log_first` - 串行日志优先（待定）

**AI 台灯执行流程**：
```
步骤 1: 播放音频 (lamp_audio_playback)
步骤 2: (可选) 抓取音频 (lamp_audio_capture_exclusive) - 独占 adb
步骤 3: 读取日志 (lamp_log_from_storage)
```

### 5. 测试脚本

**文件**：`test_executor.py`

**功能**：直接测试 AI 台灯的执行 Agent → 分析 Agent → 报告 Agent

**使用方式**：
```bash
DEVICE_ADDR=10.7.187.152:5555 .venv/Scripts/python.exe test_executor.py
```

---

## 文件结构

```
core/plugins/acoustic/
├── base.py                    # 通用基类 AcousticPluginBase
├── lamp_plugin.py             # AI 台灯插件 ✅
├── lingxi_plugin.py           # 灵犀屏插件（待实现）
├── unicom_plugin.py           # 联通中屏插件（待实现）
├── shared_tools.py            # 通用分析工具（adb、logcat、串口）
├── shared_knowledge.py        # 故障知识库（7 层检查点）
├── __init__.py               # 导入并注册插件
└── audio_playback.py         # 旧文件（待清理）
```

---

## 插件注册

```python
from core.plugins import default_registry

# 获取 AI 台灯插件
lamp_plugin = default_registry.get("acoustic_lamp")

# 创建协调器
coord = Coordinator(lamp_plugin)
```

---

## 待实现

### 灵犀屏插件（需要你提供信息）
需要了解：
1. **日志采集方式**：实时 logcat？还是从存储读取？
2. **音频获取方式**：八路音频如何采集？存储位置？
3. **是否独占 adb**：音频采集时能否同时抓日志？

### 联通中屏插件（需要你提供信息）
需要了解：
1. **日志采集方式**
2. **音频获取方式**
3. **执行策略**

---

## 下一步

等你准备好测试时，可以：

1. **测试 AI 台灯执行流程**：
   ```bash
   DEVICE_ADDR=10.7.187.152:5555 .venv/Scripts/python.exe test_executor.py
   ```

2. **提供灵犀屏和联通中屏的详细信息**，我来实现对应的插件

3. **测试完整的 5 阶段流程**（包括需求分析和用例设计）

---

## 关键设计点

1. **插件继承机制**：所有设备插件继承 `AcousticPluginBase`，复用分层定位方法论
2. **执行策略模式**：不同设备的执行流程差异通过策略模式解决
3. **工具注册机制**：每个设备插件只需实现 3-4 个工具，基类自动组装
4. **独占 adb 处理**：通过 `sequential_audio_first` 策略串行执行，避免冲突

---

## 重构前后对比

| 维度 | 重构前 | 重构后 |
|------|--------|--------|
| 插件数量 | 1 个（混杂多设备逻辑） | 1 个基类 + N 个设备插件 |
| 设备支持 | 硬编码 if-else | 插件化，新增设备只需实现基类接口 |
| 执行策略 | 固定流程 | 可配置（并行/串行） |
| 代码复用 | 低（重复代码多） | 高（基类提供通用能力） |
| 可维护性 | 差（单文件混乱） | 好（职责清晰） |

---

**重构完成！现在平台支持 AI 台灯的真实测试执行，等你测试验证。** 🎉
