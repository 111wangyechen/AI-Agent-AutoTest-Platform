# Checkpoint/Resume 机制设计文档

## 设计目标

为 AI Agent 测试平台实现断点续跑机制，解决长流程测试（采集→分析→报告）因网络抖动、设备掉线、进程崩溃中断后需要从头开始的问题。

## 核心挑战

1. **checkpoint 存什么、怎么存**（体积 vs 恢复完整性）
2. **resume 怎么校验环境一致性**（漂移检测）
3. **检测到漂移后怎么办**（报警/重置/部分回滚）

---

## 一、Checkpoint 数据结构

### 存储内容（三类）

```python
@dataclass
class Checkpoint:
    """测试 checkpoint 数据结构"""
    
    # === 1. 执行进度索引 ===
    checkpoint_id: str           # checkpoint 唯一标识（时间戳）
    test_id: str                 # 测试任务 ID
    current_stage: str           # 当前阶段：requirement/case_gen/execute/analyze/report
    completed_stages: list[str]  # 已完成的阶段列表
    current_case_index: int      # 执行阶段：当前执行到第几条用例
    total_cases: int             # 总用例数
    
    # === 2. 关键中间产物 ===
    artifacts: dict[str, Any]    # 各阶段产出
    # {
    #   "requirement": TestRequirement,
    #   "cases": list[TestCase],
    #   "raw_data_paths": ["workspace/case_01.json", ...],
    #   "analysis": Diagnosis,
    # }
    
    # === 3. 环境快照（漂移检测锚点）===
    env_snapshot: EnvironmentSnapshot
    
    # === 元数据 ===
    created_at: str              # checkpoint 创建时间
    workspace_path: str          # workspace 根目录


@dataclass
class EnvironmentSnapshot:
    """环境快照 - 用于漂移检测"""
    
    # 设备状态（4个检测点）
    device_rom_version: str      # adb shell getprop ro.build.version
    device_serial: str           # 设备序列号
    device_ip: str               # 设备 IP（检测是否掉线重连后变化）
    device_baseline: dict        # {"cpu": "20%", "mem": "512MB", "temp": "35C"}
    
    # workspace 状态（3个检测点）
    workspace_hash: str          # workspace 目录 merkle hash
    collected_files: list[dict]  # [{"path": "...", "sha256": "..."}]
    checkpoint_integrity: str    # checkpoint 文件自身校验和
    
    # 测试环境（3个检测点）
    network_latency_ms: int      # ping 延迟（检测网络状态变化）
    cloud_api_latency_ms: int    # 云端 API 延迟
    test_config_hash: str        # 测试配置文件 hash（检测参数是否被修改）
```

### 存储格式与位置

```
workspace/
├── .checkpoints/
│   ├── ckpt_20260824_100523.json  # 按时间戳命名
│   ├── ckpt_20260824_101034.json
│   └── latest -> ckpt_20260824_101034.json  # 软链指向最新
├── artifacts/
│   ├── cases.json
│   ├── raw_data/
│   └── analysis.json
└── logs/
```

**打点时机**：
1. 每个阶段完成时自动打点
2. 执行阶段每 10 条用例打一次（避免单次执行太久中断损失大）

---

## 二、Resume 流程

```python
def resume_from_checkpoint(workspace_path: str) -> TestReport:
    """从 checkpoint 恢复执行"""
    
    # 1. 读取最新 checkpoint
    ckpt = load_latest_checkpoint(workspace_path)
    if not ckpt:
        raise NoCheckpointError("未找到 checkpoint，无法恢复")
    
    # 2. 环境一致性校验（漂移检测）
    drift = detect_drift(ckpt.env_snapshot, current_env=capture_current_env())
    
    if drift.detected:
        handle_drift(drift, ckpt)  # 见下文"漂移处理策略"
    
    # 3. 重建任务状态
    coordinator = Coordinator(plugin)
    coordinator.restore_state(ckpt)
    
    # 4. 接着跑（从中断处继续）
    return coordinator.continue_execution(
        start_from_stage=ckpt.current_stage,
        start_from_case=ckpt.current_case_index
    )
```

**重建状态逻辑**：
- 不恢复完整 agent 上下文（太大且不必要）
- 主控从 checkpoint 读到结构化任务状态
- 子 agent 每次全新启动，无需"记得上次"

---

## 三、漂移检测（10 个场景）

### 设计枚举的 10 个典型漂移场景

```python
class DriftType(Enum):
    # === 设备状态变化（4个）===
    DEVICE_VERSION_CHANGED = "设备 ROM 版本更新"
    DEVICE_REBOOT = "设备重启导致日志/缓存清空"
    DEVICE_IP_CHANGED = "设备掉线后重连但 IP 变了"
    DEVICE_HARDWARE_CHANGED = "设备硬件状态变化（电量/温度超基线）"
    
    # === workspace 文件变化（3个）===
    WORKSPACE_FILES_MODIFIED = "workspace 文件被外部删除/修改"
    AUDIO_FILE_CORRUPTED = "已采集的音频文件损坏"
    CHECKPOINT_TAMPERED = "checkpoint 文件本身被篡改"
    
    # === 测试环境变化（3个）===
    NETWORK_DEGRADED = "网络从通畅变成弱网"
    ACOUSTIC_ENV_CHANGED = "实验室声学环境变化（底噪变化）"
    TEST_CONFIG_MODIFIED = "测试配置被修改"


@dataclass
class DriftDetectionResult:
    """漂移检测结果"""
    detected: bool
    drift_types: list[DriftType]  # 检测到的漂移类型
    severity: str                  # "LOW" / "MEDIUM" / "HIGH"
    details: dict[str, Any]        # 详细信息


def detect_drift(
    snapshot: EnvironmentSnapshot,
    current_env: EnvironmentSnapshot
) -> DriftDetectionResult:
    """
    对比环境快照，检测 10 种漂移场景
    
    核心逻辑：逐项对比 checkpoint 时的环境快照和当前环境
    """
    drifts = []
    
    # === 检测 1: 设备版本变化 ===
    if snapshot.device_rom_version != current_env.device_rom_version:
        drifts.append(DriftType.DEVICE_VERSION_CHANGED)
    
    # === 检测 2: 设备重启（通过 uptime 或日志起始时间） ===
    if _detect_device_reboot(snapshot, current_env):
        drifts.append(DriftType.DEVICE_REBOOT)
    
    # === 检测 3: 设备 IP 变化 ===
    if snapshot.device_ip != current_env.device_ip:
        drifts.append(DriftType.DEVICE_IP_CHANGED)
    
    # === 检测 4: 硬件状态超基线 ===
    if _hardware_baseline_exceeded(snapshot.device_baseline, current_env.device_baseline):
        drifts.append(DriftType.DEVICE_HARDWARE_CHANGED)
    
    # === 检测 5: workspace 文件被修改 ===
    if snapshot.workspace_hash != current_env.workspace_hash:
        drifts.append(DriftType.WORKSPACE_FILES_MODIFIED)
    
    # === 检测 6: 音频文件损坏 ===
    for old_file in snapshot.collected_files:
        if not _verify_file_integrity(old_file):
            drifts.append(DriftType.AUDIO_FILE_CORRUPTED)
            break
    
    # === 检测 7: checkpoint 文件被篡改 ===
    if snapshot.checkpoint_integrity != _compute_checkpoint_hash():
        drifts.append(DriftType.CHECKPOINT_TAMPERED)
    
    # === 检测 8: 网络状态变化 ===
    if abs(snapshot.network_latency_ms - current_env.network_latency_ms) > 100:
        drifts.append(DriftType.NETWORK_DEGRADED)
    
    # === 检测 9: 声学环境变化（需要外部传感器，简化为时间判断）===
    # 如果中断超过 1 小时，认为环境可能变化（有人进出）
    if _elapsed_hours(snapshot) > 1:
        drifts.append(DriftType.ACOUSTIC_ENV_CHANGED)
    
    # === 检测 10: 测试配置被修改 ===
    if snapshot.test_config_hash != current_env.test_config_hash:
        drifts.append(DriftType.TEST_CONFIG_MODIFIED)
    
    # 判定严重程度
    severity = _classify_severity(drifts)
    
    return DriftDetectionResult(
        detected=len(drifts) > 0,
        drift_types=drifts,
        severity=severity,
        details=_build_drift_details(snapshot, current_env, drifts)
    )
```

### 严重程度分级

```python
def _classify_severity(drifts: list[DriftType]) -> str:
    """根据漂移类型判定严重程度"""
    
    # HIGH: 根本性变化，必须转人工
    high_severity = {
        DriftType.DEVICE_VERSION_CHANGED,
        DriftType.TEST_CONFIG_MODIFIED,
        DriftType.CHECKPOINT_TAMPERED,
    }
    
    # MEDIUM: 影响较大，需要重跑当前阶段
    medium_severity = {
        DriftType.DEVICE_REBOOT,
        DriftType.DEVICE_IP_CHANGED,
        DriftType.AUDIO_FILE_CORRUPTED,
        DriftType.ACOUSTIC_ENV_CHANGED,
    }
    
    # LOW: 轻微变化，标注异常但继续
    # 其他所有类型
    
    if any(d in high_severity for d in drifts):
        return "HIGH"
    if any(d in medium_severity for d in drifts):
        return "MEDIUM"
    return "LOW"
```

---

## 四、漂移处理策略

```python
def handle_drift(drift: DriftDetectionResult, ckpt: Checkpoint):
    """根据漂移严重程度采取响应策略"""
    
    if drift.severity == "HIGH":
        # 重度：报警转人工，agent 不擅自决策
        raise DriftError(
            f"检测到严重环境漂移，无法自动恢复:\n"
            f"漂移类型: {[d.value for d in drift.drift_types]}\n"
            f"详情: {drift.details}\n"
            f"请人工检查环境后重新执行测试。"
        )
    
    elif drift.severity == "MEDIUM":
        # 中度：丢弃旧 checkpoint，从头重跑当前阶段
        logger.warning(
            f"检测到中度环境漂移: {drift.drift_types}\n"
            f"将丢弃 checkpoint，从 {ckpt.current_stage} 阶段开始重跑"
        )
        # 保留用例矩阵等可复用的产物
        preserved = _preserve_reusable_artifacts(ckpt)
        # 清空当前阶段的产物
        _clear_stage_artifacts(ckpt.current_stage)
        # 重置进度到当前阶段起点
        return restart_stage(ckpt.current_stage, preserved_artifacts=preserved)
    
    else:  # LOW
        # 轻度：标记异常但继续，结果标注"环境存疑"
        logger.info(
            f"检测到轻度环境漂移: {drift.drift_types}\n"
            f"将继续执行，但结果会标注环境存疑"
        )
        ckpt.metadata["drift_warning"] = drift.details
        return ckpt  # 继续使用现有 checkpoint
```

---

## 五、与 Coordinator 集成

### Coordinator 增强

```python
class Coordinator:
    def __init__(self, plugin: TestPlugin, workspace: str = None):
        self.plugin = plugin
        self.workspace = workspace or self._create_workspace()
        self.checkpoint_mgr = CheckpointManager(self.workspace)
        # ... 现有初始化 ...
    
    def run(self, requirement: str, on_event: OnEvent = None) -> TestReport:
        """执行完整流程，自动打 checkpoint"""
        
        # 尝试从 checkpoint 恢复
        if self.checkpoint_mgr.has_checkpoint():
            return self._resume_execution(requirement, on_event)
        
        # 新任务：正常执行并打 checkpoint
        return self._run_with_checkpoints(requirement, on_event)
    
    def _run_with_checkpoints(self, requirement: str, on_event: OnEvent) -> TestReport:
        """带 checkpoint 的执行流程"""
        
        # 保存初始环境快照
        env_snapshot = capture_environment_snapshot(self.plugin)
        
        try:
            # 1) 需求分析
            req = self.requirement_agent.run(requirement, on_event)
            self.checkpoint_mgr.save("requirement", req, env_snapshot)
            
            # 2) 用例设计
            cases = self.case_agent.run(req, on_event)
            self.checkpoint_mgr.save("case_gen", cases, env_snapshot)
            
            # 3) 执行采集（每 10 条打一次点）
            raw = self._execute_with_checkpoints(cases, env_snapshot, on_event)
            
            # 4) 结果分析
            diagnosis = self.analysis_agent.run(...)
            self.checkpoint_mgr.save("analyze", diagnosis, env_snapshot)
            
            # 5) 报告生成
            report_md = self.report_agent.run(...)
            self.checkpoint_mgr.save("report", report_md, env_snapshot)
            
            return TestReport(...)
        
        except Exception as e:
            # 异常时保存 checkpoint，方便恢复
            self.checkpoint_mgr.save_error_state(str(e), env_snapshot)
            raise
    
    def _execute_with_checkpoints(
        self, 
        cases: list[TestCase], 
        env_snapshot: EnvironmentSnapshot,
        on_event: OnEvent
    ) -> list[RawData]:
        """执行阶段：每 10 条用例打一次 checkpoint"""
        raw: list[RawData] = []
        
        for i, case in enumerate(cases):
            # 执行单条用例
            result = self.executor_agent._run_single_case(case, on_event)
            raw.append(result)
            
            # 每 10 条打一次点
            if (i + 1) % 10 == 0:
                self.checkpoint_mgr.save_progress(
                    stage="execute",
                    current_index=i + 1,
                    total=len(cases),
                    artifacts={"raw_data": raw},
                    env_snapshot=env_snapshot
                )
        
        return raw
    
    def _resume_execution(self, requirement: str, on_event: OnEvent) -> TestReport:
        """从 checkpoint 恢复执行"""
        
        # 1. 加载 checkpoint
        ckpt = self.checkpoint_mgr.load_latest()
        
        # 2. 漂移检测
        current_env = capture_environment_snapshot(self.plugin)
        drift = detect_drift(ckpt.env_snapshot, current_env)
        
        if drift.detected:
            handle_drift(drift, ckpt)  # 可能抛异常或返回新 checkpoint
        
        # 3. 根据已完成的阶段跳过
        if "requirement" not in ckpt.completed_stages:
            # ... 执行需求分析
            pass
        
        if "case_gen" not in ckpt.completed_stages:
            # ... 执行用例设计
            pass
        
        # 4. 从中断处继续执行
        if ckpt.current_stage == "execute":
            # 恢复已采集的数据
            raw = ckpt.artifacts.get("raw_data", [])
            # 从 current_case_index 继续
            remaining_cases = cases[ckpt.current_case_index:]
            for case in remaining_cases:
                result = self.executor_agent._run_single_case(case, on_event)
                raw.append(result)
        
        # ... 继续后续阶段 ...
```

---

## 六、测试策略

### 确定性部分（pytest 单测）

```python
def test_checkpoint_save_and_load():
    """测试 checkpoint 存取"""
    mgr = CheckpointManager(tmp_workspace)
    mgr.save("case_gen", sample_cases, sample_env)
    
    ckpt = mgr.load_latest()
    assert ckpt.current_stage == "case_gen"
    assert len(ckpt.artifacts["cases"]) == len(sample_cases)


def test_resume_skips_completed_stages():
    """测试 resume 跳过已完成阶段"""
    mgr.save("case_gen", cases)
    mgr.save("execute", raw_data)
    
    ckpt = mgr.load_latest()
    assert "case_gen" in ckpt.completed_stages
    assert "execute" in ckpt.completed_stages
    # resume 应该从 analyze 开始


def test_drift_detection_on_version_change():
    """故障注入：模拟设备版本变化"""
    old_env = EnvironmentSnapshot(device_rom_version="v1.2.3", ...)
    new_env = EnvironmentSnapshot(device_rom_version="v1.3.0", ...)
    
    drift = detect_drift(old_env, new_env)
    assert drift.detected is True
    assert DriftType.DEVICE_VERSION_CHANGED in drift.drift_types
    assert drift.severity == "HIGH"


def test_checkpoint_file_corruption_fallback():
    """测试 checkpoint 文件损坏时降级到上一个"""
    mgr.save("stage1", data1)
    mgr.save("stage2", data2)
    
    # 人为损坏最新的 checkpoint
    corrupt_latest_checkpoint()
    
    # 应该降级到上一个
    ckpt = mgr.load_latest_with_fallback()
    assert ckpt.current_stage == "stage1"
```

---

## 七、实现状态与诚实边界

✅ **已完成（设计层面）**：
- Checkpoint 数据结构定义
- 10 种漂移场景枚举
- 漂移检测逻辑设计
- 三级响应策略
- 与 Coordinator 集成方案

🚧 **进行中（代码实现）**：
- CheckpointManager 类实现
- capture_environment_snapshot() 实现
- detect_drift() 具体检测函数
- Coordinator 的 checkpoint 集成

📋 **待验证**：
- 10 种场景的故障注入测试
- "100% 识别率"的实测验证（目前是设计目标）

---

**重要**：100% 识别率是**设计目标**（10 种场景都有对应检测规则），不是实测数据。真实验证需要模拟这 10 种场景跑测试，这是验证计划里的 TODO。
