# 测试实践指南

> 配套阅读：[TEST_PHILOSOPHY.md](TEST_PHILOSOPHY.md)（为什么这样测）  
> 本文讲**怎么写、怎么跑**测试。

---

## 一、快速开始

### 跑全部单测（mock 模式，秒级）

```bash
cd projects/ai-agent-test-platform

# mock 模式：不连真机、不调 LLM，纯确定性逻辑
PLATFORM_MOCK=1 .venv/Scripts/python.exe -m pytest tests/ -v
```

预期输出：
```
tests/test_checkpoint.py .............. ....       [ 35%]
tests/test_executor_dispatch.py ........          [ 50%]
tests/test_lamp_iperf.py .........                [ 67%]
tests/test_plugins.py .........                   [ 84%]
tests/test_registry.py ........                   [100%]

===================== 53 passed in 1.25s =====================
```

### 跑单个测试文件

```bash
PLATFORM_MOCK=1 .venv/Scripts/python.exe -m pytest tests/test_checkpoint.py -v
```

### 跑单个测试函数

```bash
PLATFORM_MOCK=1 .venv/Scripts/python.exe -m pytest tests/test_checkpoint.py::test_drift_detected_on_version_change -v
```

### 真调 LLM 验证（可选，分钟级）

```bash
# 验证 PreflightAgent 真的调通了 LLM 且功能正确
PLATFORM_MOCK=1 .venv/Scripts/python.exe test_preflight_llm.py
```

---

## 二、测试目录结构

```
tests/
├── conftest.py                  # pytest 配置：强制 mock 模式、加 sys.path
├── test_registry.py             # 工具注册表（8 项）
├── test_plugins.py              # 插件契约（9 项）
├── test_lamp_iperf.py           # iperf3 解析器（9 项）
├── test_checkpoint.py           # checkpoint/resume + 漂移检测（19 项）
└── test_executor_dispatch.py    # 执行器派发逻辑（8 项）

test_preflight_llm.py            # 真调 LLM 验证脚本（不在 pytest 主流程）
```

### conftest.py 的作用

```python
"""pytest 配置：把项目根加入 sys.path，并强制 mock 模式。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("PLATFORM_MOCK", "1")  # 默认 mock，不连真机不调 LLM
sys.path.insert(0, str(Path(__file__).parent.parent))
```

**关键**：所有 pytest 测试默认在 mock 模式跑，保证：
- 不需要连真机
- 不需要调 LLM（不烧 token、不受网络影响）
- 秒级完成、可复现

---

## 三、如何写确定性测试

### 模式 1：纯函数测试（断言相等）

```python
def test_cer_basic():
    """字错误率计算：全对/漏字/全错"""
    assert compute_cer("今天天气", "今天天气") == 0.0    # 全对
    assert compute_cer("今天气", "今天天气") == 0.25     # 漏一字
    assert compute_cer("", "今天天气") == 1.0           # 全错
```

### 模式 2：参数化测试（一次测多个用例）

```python
import pytest

@pytest.mark.parametrize("name", ["acoustic_lamp", "lamp"])
def test_plugin_implements_contract(name):
    """两个插件都完整实现契约"""
    p = default_registry.get(name)
    assert isinstance(p, TestPlugin)
    assert all(isinstance(t, Tool) for t in p.collectors())
```

### 模式 3：fixture（共享测试前置）

```python
@pytest.fixture
def workspace():
    """每个测试独立的临时 workspace"""
    return tempfile.mkdtemp()

def test_store_roundtrip(workspace):
    store = CheckpointStore(workspace)
    # ... 用 workspace
```

### 模式 4：故障注入（测降级路径）

```python
def test_corrupted_latest_falls_back(workspace):
    """最新 checkpoint 损坏时，降级读上一个"""
    store = CheckpointStore(workspace)
    store.save(_make_ckpt(workspace, "ckpt_1", ["requirement"]))
    store.save(_make_ckpt(workspace, "ckpt_2", ["requirement", "case_gen"]))
    
    # 故意写坏最新文件
    (Path(workspace) / ".checkpoints" / "ckpt_2.json").write_text("{ broken")
    
    loaded = store.load_latest()
    assert loaded.checkpoint_id == "ckpt_1"  # 降级成功
```

---

## 四、如何测 LLM Agent（非确定性）

### 原则：断言关键结论，不断言措辞

```python
# ❌ 不要这样（措辞会变）
assert result["reason"] == "固件升级导致唤醒行为变化"

# ✅ 要这样（断言关键结论）
assert result["consistent"] is False              # 判定对
assert result["decision"] == DriftDecision.RERUN_STAGE  # 决策对
assert len(result["reason"]) > 10                 # 有理由
```

### 范例：test_preflight_llm.py

三种场景测 PreflightAgent：

```python
def scenario_version_change(log):
    """场景：设备版本变更，让 LLM 研判"""
    base = _current_baseline()
    old = EnvironmentSnapshot(**base.to_dict())
    old.device_rom_version = "0.9.1.old"  # 构造版本差异
    ckpt = _make_checkpoint(old)

    result = PreflightAgent(ckpt).assess()  # 真调 LLM

    # 断言关键结论
    _report(log, result,
            expect_consistent=False,
            expect_decisions=[DriftDecision.RERUN_STAGE,
                              DriftDecision.ABORT_TO_HUMAN])
```

### 为什么 LLM 测试不放 pytest 主流程

- **慢**：每次调 LLM 几秒到几十秒，一套下来分钟级
- **不稳定**：网络抖动、API 限流可能失败
- **烧 token**：CI 上频繁跑成本高

所以：
- pytest 主流程用 mock（秒级、稳定、免费）
- 真 LLM 验证作为独立脚本，手动/scheduled 跑

---

## 五、mock 模式怎么工作

### config.mock 开关

```python
# core/config.py
class Config:
    mock: bool = os.getenv("PLATFORM_MOCK", "0") == "1"
    
    @property
    def has_device(self) -> bool:
        return bool(self.device_addr) and not self.mock
```

### 工具在 mock 下回落样本

```python
# core/plugins/acoustic/lamp_plugin.py
def lamp_wakeup_test(audio_file, expected_count=300):
    if config.mock or not config.has_device:
        # mock 模式：返回模拟测试结果
        return {
            "status": "success",
            "mode": "mock",
            "summary": {
                "detected_count": 270,  # 模拟 90% 唤醒率
                "success_rate": "90.0%",
            },
        }
    # 真机模式：执行真实测试
    ...
```

### 环境采集在 mock 下返回基线

```python
# core/checkpoint/environment.py
def read_device_version() -> str:
    if config.mock or not config.has_device:
        return "1.0.3.a12"  # mock 固定值
    # 真机：adb 读取
    ...
```

**关键**：mock 返回的是**可预测的固定值**，这样测试才能断言。

---

## 六、覆盖率报告（可选）

```bash
# 安装 pytest-cov（需要网络）
uv pip install pytest-cov

# 生成覆盖率报告
PLATFORM_MOCK=1 .venv/Scripts/python.exe -m pytest tests/ --cov=core --cov-report=term-missing
```

**注意**：不追求 100% 覆盖率。目标是：
- 确定性核心逻辑（指标/解析/checkpoint/漂移）有单测
- 知道哪些没覆盖（真机采集、完整 LLM 流水线）
- 未覆盖的有兜底策略（人工验证、故障注入）

---

## 七、常见问题

### Q: 测试报 UnicodeEncodeError？
Windows 控制台默认 GBK 编码，打印中文/特殊字符可能报错。解决：
```python
# 脚本开头强制 UTF-8
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
```

### Q: 真调 LLM 报 401？
检查 `.env` 里的鉴权：
```bash
ANTHROPIC_AUTH_TOKEN=sk-...        # 公司网关
ANTHROPIC_BASE_URL=https://...     # 网关地址
```

### Q: 测试改了工具名后挂了？
这正是 `test_executor_dispatch.py` 要防的——不要用工具名子串匹配找工具，用角色（`audio_playback_tool()` / `log_collector()`）。

### Q: 怎么跑真机测试？
```bash
# 连真机（不加 PLATFORM_MOCK）
DEVICE_ADDR=10.7.187.152:5555 .venv/Scripts/python.exe test_executor.py
```

---

## 八、添加新测试的检查清单

写新功能时，配套测试要考虑：

- [ ] **正常路径** - 输入合法，输出符合预期
- [ ] **边界值** - 空串、0、最大值、特殊字符
- [ ] **异常输入** - 非法参数、缺字段、类型错误
- [ ] **降级路径** - 依赖失败时能优雅降级（不崩溃）
- [ ] **如果是 LLM 部分** - 断言关键结论，不断言措辞
- [ ] **mock 可跑** - 不依赖真机/真 LLM 也能跑

---

**记住**：测试不是为了覆盖率数字，是为了**下次改代码时有信心**。
