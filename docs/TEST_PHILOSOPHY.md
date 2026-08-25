# 测试哲学 · 如何测试一个 AI 测试平台

> **核心挑战**：这是一个用 LLM Agent 做自动化测试的平台，那谁来测这个平台本身？  
> **答案**：把系统切成两半，**确定性部分用传统测试**，**非确定性部分用 golden set + schema 约束**。

---

## 一、为什么需要特殊的测试策略

传统软件测试的前提是"输入固定则输出固定"——给同样的输入，程序每次都返回同样的结果，所以可以断言 `assert result == expected`。

但 AI 测试平台有两类组件：

### 确定性部分
- **指标计算**（WUR/FAR/CER）- 纯函数，输入固定输出固定
- **日志解析**（`RX [32]: <wake_event>`）- 正则匹配，可复现
- **checkpoint 存取** - 文件 I/O，可单测
- **漂移检测** - 逐项对比，规则清晰
- **MCP tool 采集** - mock 掉真实设备，测封装逻辑

这些可以用 **传统单元测试 + 断言相等** 覆盖。

### 非确定性部分
- **LLM Agent 的推理** - 同一输入，措辞每次不同
- **根因分析结论** - 不能断言 `assert conclusion == "驱动层缺桥接"`（文字会变）
- **用例生成** - LLM 生成的测试用例每次可能不同
- **漂移研判** - PreflightAgent 结合语义做决策

这些**不能断言字符串全等**，需要特殊测法。

---

## 二、测试分层策略

```
                     AI 测试平台
    ┌────────────────────────────────────────────────────┐
    │                                                    │
    │  ┌──────────────────────────────────────────┐    │
    │  │  非确定性层（LLM Agent）                  │    │
    │  │  - 需求分析 Agent                         │    │
    │  │  - 用例设计 Agent                         │    │
    │  │  - 结果分析 Agent                         │    │
    │  │  - PreflightAgent（漂移研判）            │    │
    │  └──────────────────────────────────────────┘    │
    │           ▲                                       │
    │           │ 调用                                  │
    │           ▼                                       │
    │  ┌──────────────────────────────────────────┐    │
    │  │  确定性层（可单测）                      │    │
    │  │  - 指标计算（compute_wur/cer）            │    │
    │  │  - 日志解析（parse_serial_line）         │    │
    │  │  - checkpoint 存取（CheckpointStore）     │    │
    │  │  - 漂移检测（detect_drift）              │    │
    │  │  - MCP tool 封装（adb/logcat）            │    │
    │  └──────────────────────────────────────────┘    │
    │                                                    │
    └────────────────────────────────────────────────────┘
```

### 确定性层：传统单元测试

```python
# tests/test_checkpoint.py
def test_drift_detected_on_version_change():
    """故障注入：设备版本变了，必须检出漂移"""
    old = EnvironmentSnapshot(device_rom_version="1.0.3.a12", ...)
    new = EnvironmentSnapshot(device_rom_version="1.3.0", ...)
    
    result = detect_drift(old, new)
    
    assert result.detected is True
    assert DriftType.DEVICE_VERSION_CHANGED in {i.drift_type for i in result.items}
    assert result.severity == DriftSeverity.MEDIUM
```

**关键**：输入固定（两份快照）→ 输出固定（检测到版本漂移） → 断言相等。

### 非确定性层：golden set + schema 约束

```python
# tests/test_preflight_llm.py（真调 LLM 的验证脚本）
def scenario_version_change():
    """构造「设备版本变更」场景，让 LLM Agent 研判"""
    old_env = EnvironmentSnapshot(device_rom_version="0.9.1.old", ...)
    new_env = EnvironmentSnapshot(device_rom_version="1.0.3.a12", ...)
    ckpt = TestCheckpoint(env_snapshot=old_env, ...)
    
    # PreflightAgent 是真 LLM，会调 Claude API
    result = PreflightAgent(ckpt).assess()
    
    # 不断言措辞全等，只断言关键结论
    assert result["consistent"] is False           # 判定不一致
    assert result["decision"] in [                 # 决策合理
        DriftDecision.RERUN_STAGE,
        DriftDecision.ABORT_TO_HUMAN,
    ]
    assert len(result["reason"]) > 10              # 给了理由
```

**关键**：
- 不断言 `reason == "固件升级导致唤醒行为变化"`（措辞会变）
- 只断言 **关键结论对不对**（不一致、决策在预期范围内、有理由）

---

##三、具体测试策略

### 1. 单元测试（确定性部分）

**测什么**：
- ✅ 指标计算（WUR/FAR/CER）- 边界值、空串、特殊字符
- ✅ 日志解析 - 各种格式的日志行、非法输入
- ✅ checkpoint 存取 - 存了能读出、损坏能降级、原子写
- ✅ 漂移检测 10 场景 - 每种场景单独注入、三级严重程度分类

**举例**：
```python
def test_cer_handles_empty_strings():
    """边界：空串与任意串的 CER = 1.0（全错）"""
    assert compute_cer("", "今天天气") == 1.0
    assert compute_cer("今天天气", "") == 1.0
```

```python
def test_checkpoint_corruption_falls_back():
    """故障注入：最新 checkpoint 损坏时，降级到上一个"""
    store.save(ckpt1)
    store.save(ckpt2)
    corrupt_file(ckpt2.path)  # 写坏最新文件
    
    loaded = store.load_latest()
    assert loaded.checkpoint_id == ckpt1.checkpoint_id  # 降级成功
```

### 2. schema 断言（非确定性部分）

LLM 输出是 JSON，用 Pydantic 校验结构：

```python
@dataclass
class DriftDetectionResult:
    detected: bool
    items: list[DriftItem]
    severity: DriftSeverity
    
    def to_dict(self) -> dict:
        return asdict(self)
```

```python
def test_preflight_output_always_valid_schema():
    """LLM 输出结构必须符合预期，跑多次抗随机性"""
    for _ in range(5):
        result = PreflightAgent(ckpt).assess()
        assert "consistent" in result
        assert "decision" in result
        assert "reason" in result
        # 有结论必有理由（防幻觉）
        if not result["consistent"]:
            assert len(result["reason"]) > 0
```

### 3. golden set 回归（未来计划）

```python
# tests/golden/灵犀屏-网卡虚接.json
{
  "evidence": {
    "logcat": "...",
    "serial": "RX [0]: // 长时间无数据"
  },
  "expected": {
    "layer": "硬件",
    "verdict": "网卡虚接导致接收链路死",
    "must_cite": ["RX [0]", "长时间无数据"]
  }
}
```

```python
def test_analyzer_on_golden_灵犀屏_网卡虚接():
    case = load_golden("灵犀屏-网卡虚接.json")
    result = AnalysisAgent(plugin).analyze(case["evidence"])
    
    # 不断言 root_cause 文本全等（措辞会变）
    assert result.layer == case["expected"]["layer"]  # 定位层对
    assert result.verdict == case["expected"]["verdict"]  # 判决对
    assert all(ref in result.raw for ref in case["expected"]["must_cite"])  # 引对证据
```

**状态**：框架设计完成，样本在积累中（需要真实 badcase）。

### 4. 真 LLM 验证（可选、非阻塞）

`test_preflight_llm.py` 是专门测 LLM 部分的脚本：
- 构造 3 种漂移场景
- 真调 Claude API
- 断言关键结论（不一致判定、决策合理、有理由）

**用法**：
```bash
PLATFORM_MOCK=1 python test_preflight_llm.py
```

**特点**：
- mock 模式（环境采集返回样本值，不连真机）
- 真调 LLM（走公司网关）
- 不放 pytest 主流程（太慢，可能失败）
- 作为手动/scheduled 验证

---

##四、故障注入测试

专门测降级路径和异常处理：

```python
def test_resume_with_all_checkpoints_corrupted():
    """所有 checkpoint 都损坏 → 明确报错，不静默"""
    store.save(ckpt1)
    store.save(ckpt2)
    corrupt_file(ckpt1.path)
    corrupt_file(ckpt2.path)
    
    with pytest.raises(RuntimeError, match="未找到可用 checkpoint"):
        Coordinator(plugin, workspace).resume()
```

```python
def test_executor_reports_missing_audio_file_explicitly():
    """
    执行阶段缺参数 → 显式错误记录，不静默跳过。
    静默跳过最难查（"测试跑完了但没听到声音"）。
    """
    raw = executor.run([case_without_audio_file])
    assert any(r.payload["status"] == "error" for r in raw)
    assert any("audio_file" in r.payload.get("result", "") for r in raw)
```

---

## 五、测试覆盖的边界（诚实表达）

### 已覆盖
- ✅ 指标计算 - 单测 100%（空串、边界、特殊字符）
- ✅ checkpoint 存取 - 单测覆盖（存取、损坏降级、原子写、rotate）
- ✅ 漂移检测 10 场景 - 每种单独注入、三级严重程度
- ✅ executor 派发逻辑 - 回归测试（防止静默跳过播放步骤）
- ✅ PreflightAgent - 真 LLM 验证（3 场景）

### 未完全覆盖
- ⚠️ 真实设备采集 - 需要连真机，CI 上跑不了
- ⚠️ 真实 LLM 的完整 5 段流水线 - 太慢（分钟级），不适合 CI
- ⚠️ golden set 自动回归 - 框架搭了，样本规模还小
- ⚠️ 并发采集 - 当前是串行，真并行需要真机测

### 兜底策略
- 真机测试 → 本地手动验证 + 实验室现场验证
- LLM 输出质量 → schema 约束 + 人工采样复核
- 网络抖动/设备掉线 → 故障注入单测 + 现场观察

---

## 六、为什么这样测是合理的

### 传统测试的局限
如果强行断言 LLM 输出字符串全等：
```python
# ❌ 错误做法
result = agent.analyze(badcase)
assert result.root_cause == "驱动层缺少 ALSA 桥接导致音频流无法进入框架"
```
问题：
- LLM 每次措辞不同
- 升级模型（Opus 4.8 → 4.9）措辞变了，测试就挂了
- 实际根因判断对了，但因为措辞不同被判 FAIL

### 本方案的优势
```python
# ✅ 正确做法
result = agent.analyze(badcase)
assert result.layer == "驱动"                # 定位层对
assert result.verdict in VALID_VERDICTS      # 判决在合理范围
assert "ALSA" in result.raw or "桥接" in result.raw  # 关键词出现
```
好处：
- 抗 LLM 随机性 - 措辞变了不影响
- 抗模型升级 - 只要结论对，通过
- 真正测核心能力 - "定位对不对"，不是"措辞像不像"

---

## 七、与测试岗面试的对应

| 面试常见追问 | 本文档的答案 |
|-------------|-------------|
| "你这平台自己测过吗？" | 有 53 个单测，mock 模式下秒级跑完，确定性部分（指标/解析/checkpoint）有单测保障 |
| "怎么测 AI 部分？" | golden set 思路（框架搭了，样本在攒）+ schema 断言 + 真 LLM 验证脚本 |
| "测试覆盖率多少？" | 不追求数字。确定性层有单测，AI 层靠 schema 约束 + 人工复核兜底 |
| "golden set 有多少条？" | 样本集在积累，灵犀屏网卡虚接、框架层缺桥接等真实案例是种子。诚实说：回归框架搭了，规模还小 |
| "怎么保证 golden set 的期望答案对？" | 期望答案是**人工确认过的真实结论**（我实际定位到的、修复验证过的）。新案例进 golden set 前必须人工确认 |
| "CI 怎么配置？" | GitHub Actions，每次 push 跑 pytest，mock 模式不需要真机/LLM（可选跑真 LLM 验证） |

---

## 八、下一步计划

1. **扩充 golden set** - 攒够 20+ 真实 badcase，覆盖硬件/驱动/框架/应用各层
2. **增加真机集成测试** - scheduled 任务，夜间跑一次完整流水线
3. **LLM 输出质量监控** - 采样日志，人工复核，发现系统性错误补进 golden set
4. **覆盖率报告** - pytest-cov 生成报告（不追求 100%，但知道哪里没覆盖）

---

**金句**：
> **能确定的断言相等，不确定的断言关键结论。**  
> 测试 AI 系统，不是测它的措辞，是测它的判断。
