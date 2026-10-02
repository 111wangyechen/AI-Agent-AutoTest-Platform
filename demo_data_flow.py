"""
数据流追踪演示脚本 - Mock 版本
不涉及真机测试，纯展示平台内部数据流转过程

运行方式：
    python demo_data_flow.py

输出：
    在控制台打印 5 个阶段的数据结构（TestRequirement → TestCase → RawData → Diagnosis → TestReport）
"""
import io
import sys
import json
from dataclasses import asdict

# Windows 终端默认 gbk 编码，强制 stdout 用 utf-8 以支持中文和 emoji
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from core.plugins.models import TestRequirement, TestCase, RawData, Diagnosis, TestReport

def print_stage(stage_name: str, data, description: str = ""):
    """打印数据流转阶段"""
    print("\n" + "="*80)
    print(f"📦 数据流转阶段 {stage_name}")
    if description:
        print(f"   {description}")
    print("="*80)

    # 如果是dataclass，转为dict后打印
    if hasattr(data, '__dataclass_fields__'):
        data_dict = asdict(data)
        print(json.dumps(data_dict, indent=2, ensure_ascii=False))
    elif isinstance(data, list):
        print(f"\n✅ 共 {len(data)} 条数据")
        for i, item in enumerate(data):
            if hasattr(item, '__dataclass_fields__'):
                print(f"\n【第 {i+1} 条】 类型: {item.__class__.__name__}")
                print(json.dumps(asdict(item), indent=2, ensure_ascii=False))
            else:
                print(f"\n【第 {i+1} 条】 {item}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    """模拟完整数据流"""
    print("\n" + "="*80)
    print("AI Agent 自动化测试平台 - 数据流追踪演示")
    print("模拟场景: 台灯智能音箱唤醒测试")
    print("="*80)

    # ============================================================
    # 阶段1: 需求分析 Agent → TestRequirement
    # ============================================================
    user_input = "测试台灯智能音箱在距离1米、噪音60dB环境下的唤醒成功率，要求≥95%"

    requirement = TestRequirement(
        raw=user_input,
        device="LL10 台灯智能音箱",
        test_type="acoustic_wakeup",
        metrics=["唤醒成功率", "误唤醒率", "响应时延"],
        acceptance="唤醒成功率 ≥ 95%，误唤醒率 ≤ 1%，响应时延 ≤ 1.5s"
    )

    print_stage(
        "1️⃣ 需求分析 (RequirementAgent → TestRequirement)",
        requirement,
        "用户自然语言 → 结构化测试需求"
    )

    # ============================================================
    # 阶段2: 用例设计 Agent → TestCase[]
    # ============================================================
    cases = [
        TestCase(
            case_id="case_001",
            title="距离1米-噪音60dB-100次唤醒",
            params={
                "distance": "1m",
                "noise_level": "60dB",
                "wakeup_count": 100,
                "collector_args": {
                    "capture_logcat": True,
                    "capture_audio": False,  # 首轮不抓音频
                }
            },
            expected="唤醒成功率 ≥ 95%"
        ),
        TestCase(
            case_id="case_002",
            title="距离1米-噪音60dB-边界验证20次",
            params={
                "distance": "1m",
                "noise_level": "60dB",
                "wakeup_count": 20,
                "collector_args": {
                    "capture_logcat": True,
                    "capture_audio": False,
                }
            },
            expected="边界场景无误触发"
        ),
    ]

    print_stage(
        "2️⃣ 用例设计 (CaseAgent → TestCase[])",
        cases,
        "根据需求生成参数矩阵，覆盖正常+边界场景"
    )

    # ============================================================
    # 阶段3: 执行采集 Agent → RawData[]
    # ============================================================
    raw_data = [
        RawData(
            case_id="case_001",
            collector="lamp_wakeup_test",
            payload={
                "total_attempts": 100,
                "success_count": 87,
                "failed_count": 13,
                "success_rate": 0.87,
                "avg_response_time_ms": 1350,
                "logcat_extract": [
                    {"timestamp": "14:23:01.234", "tag": "WakeupEngine", "msg": "WAKEUP_TRIGGERED"},
                    {"timestamp": "14:23:01.890", "tag": "WakeupEngine", "msg": "WAKEUP_FAILED: audio_busy"},
                    {"timestamp": "14:23:05.123", "tag": "AudioService", "msg": "pcm_open failed: device busy"}
                ],
                "failure_patterns": {
                    "audio_busy": 8,
                    "vad_timeout": 3,
                    "keyword_mismatch": 2
                }
            }
        ),
        RawData(
            case_id="case_002",
            collector="lamp_wakeup_test",
            payload={
                "total_attempts": 20,
                "success_count": 17,
                "failed_count": 3,
                "success_rate": 0.85,
                "avg_response_time_ms": 1420,
                "logcat_extract": [
                    {"timestamp": "14:25:10.456", "tag": "WakeupEngine", "msg": "LISTEN_MSG_VAD_END"},
                    {"timestamp": "14:25:11.789", "tag": "ChatMode", "msg": "entering_chat_mode"},
                ]
            }
        ),
    ]

    print_stage(
        "3️⃣ 执行采集 (ExecutorAgent → RawData[])",
        raw_data,
        "调用插件工具采集日志、指标、音频等原始证据"
    )

    # ============================================================
    # 阶段4: 结果分析 Agent → Diagnosis
    # ============================================================
    diagnosis = Diagnosis(
        text="""## 分层诊断结论

### ❌ 测试结果: FAIL
- **实际唤醒成功率**: 87% (case_001) + 85% (case_002) 平均 86.7%
- **验收标准**: ≥ 95%
- **差距**: -8.3%

### 🔍 根因定位 (7层检查点分析)

#### 第4层 应用层故障 (主因)
- **症状**: 13次失败中8次为 `audio_busy` 错误
- **日志特征**: `pcm_open failed: device busy`, `LISTEN_MSG_VAD_END` 后进入闲聊模式
- **根因**: 闲聊模式占用麦克风后未及时释放，导致下次唤醒时音频设备busy，唤醒引擎无法打开录音通道
- **影响范围**: 约占总失败数的 61.5% (8/13)

#### 第3层 框架层问题 (次因)
- **症状**: 3次 `vad_timeout` 超时
- **可能原因**: VAD检测超时阈值设置过短，或环境噪音影响VAD端点检测

### 🎯 修复建议
1. **P0**: 修复闲聊模式退出时的音频设备释放逻辑 (预期提升 6-8%)
2. **P1**: 调整 VAD 超时阈值或优化端点检测算法 (预期提升 2-3%)

### ⚠️ 证据充足性评估
- ✅ 日志完整: 已采集 logcat 包含关键 tag
- ❌ 音频缺失: 未采集四通道音频，无法验证是否为硬件麦克风故障
- ⚠️  建议复测: 启用四通道音频采集，验证 audio_busy 是否为上层逻辑问题还是底层硬件问题
""",
        rounds=1,
        tool_calls=[
            {"tool": "query_golden_set", "args": {"tags": "唤醒,audio_busy"}, "result": "找到1条历史case"}
        ],
        # 复测决策字段
        need_reproduce=True,
        reproduce_hint="启用四通道音频采集，重点关注 audio_busy 错误发生时的硬件麦克风状态",
        target_evidence=["four_channel_audio", "full_serial_log"],
        # Golden-set 关联
        matched_bad_cases=["bc_20260825_六次唤醒后进入闲聊模式不响应"],
        is_new_bug=False,  # 历史已知问题
        bug_summary={}
    )

    print_stage(
        "4️⃣ 结果分析 (AnalysisAgent → Diagnosis)",
        diagnosis,
        "7层故障定位 + 匹配历史bad-case + 判断是否需要复测"
    )

    # ============================================================
    # 阶段4.5: 【自主复测循环】分析Agent认为证据不足 → 回到执行Agent
    # ============================================================
    print("\n" + "🔄"*40)
    print("⚡ 触发自主复测机制！")
    print("   原因: need_reproduce=True (证据不足)")
    print("   提示: ", diagnosis.reproduce_hint)
    print("   目标证据: ", diagnosis.target_evidence)
    print("🔄"*40)

    # 执行Agent根据hint修改测试用例，补采证据
    reproduce_raw = [
        RawData(
            case_id="case_001_reproduce_r1",
            collector="lamp_wakeup_test_with_audio",
            payload={
                "total_attempts": 20,  # 复测轮次减少用例数
                "success_count": 16,
                "failed_count": 4,
                "four_channel_audio_files": [
                    "audio_ch0_ref.pcm",  # 参考音(播放内容)
                    "audio_ch1_mic1.pcm", # 麦克风1
                    "audio_ch2_mic2.pcm", # 麦克风2
                    "audio_ch3_aec_out.pcm" # AEC输出
                ],
                "audio_analysis": {
                    "mic1_signal_present": True,
                    "mic2_signal_present": True,
                    "aec_working": True,
                    "hw_fault": False  # 硬件正常
                },
                "logcat_extract": [
                    {"timestamp": "15:01:23.456", "tag": "AudioHAL", "msg": "all channels active"},
                    {"timestamp": "15:01:24.789", "tag": "WakeupEngine", "msg": "audio_busy from upper layer"}
                ]
            }
        )
    ]

    print_stage(
        "4.5️⃣ 自主复测第1轮 (ExecutorAgent.reproduce() → RawData[])",
        reproduce_raw,
        "ExecutorAgent接收reproduce_hint，修改用例参数(启用audio采集)，补采证据"
    )

    # 重新分析（带着新证据）
    diagnosis_round2 = Diagnosis(
        text="""## 分层诊断结论 (复测后更新)

### ❌ 测试结果: FAIL (结论同初轮)
- **综合成功率**: (87+85+80)/3 = 84%
- **验收标准**: ≥ 95%

### 🔍 根因定位 (补充音频证据后确认)

#### 第4层 应用层故障 (已确认)
- ✅ **音频证据**: 四通道采集显示硬件麦克风正常工作
- ✅ **HAL层日志**: `all channels active` 说明底层正常
- ✅ **上层日志**: `audio_busy from upper layer` 确认为应用层逻辑问题
- **最终结论**: **闲聊模式未释放音频设备为根本原因**，非硬件故障

### 🎯 修复建议 (高置信度)
1. **P0 高优**: 修复 ChatMode 退出时调用 `AudioService.release()` 的时机
   - 预期提升: 6-8%
   - 修复位置: `app/chat/ChatModeManager.java:onExit()`

2. **P1**: VAD超时问题 (3/13) 建议单独排查

### ✅ 证据充足性: 已足够定位
""",
        rounds=2,
        tool_calls=[],
        need_reproduce=False,  # 证据已足够
        reproduce_hint="",
        target_evidence=[],
        matched_bad_cases=["bc_20260825_六次唤醒后进入闲聊模式不响应"],
        is_new_bug=False,
        bug_summary={}
    )

    print_stage(
        "4.6️⃣ 结果分析第2轮 (AnalysisAgent 用新证据重新分析)",
        diagnosis_round2,
        "补充音频证据后，确认为应用层问题，不再需要复测 (need_reproduce=False)"
    )

    # ============================================================
    # 阶段5: 报告生成 Agent → TestReport
    # ============================================================
    report = TestReport(
        plugin="acoustic_lamp",
        requirement=requirement,
        cases=cases,
        raw_data=raw_data + reproduce_raw,  # 包含初轮+复测轮证据
        diagnosis=diagnosis_round2,
        report_md="""# 测试报告

## 测试概要
- 设备: LL10 台灯智能音箱
- 测试类型: 唤醒成功率
- 测试时间: 2026-08-25 14:20-15:10
- 测试工程师: AI Agent 自动化平台

## 测试结果
❌ **FAIL** - 唤醒成功率 84% 未达标 (要求≥95%)

## 根因分析
应用层闲聊模式退出时未释放音频设备，导致后续唤醒失败 (占失败数61.5%)

## 证据链
- 初轮采集: logcat日志 (100+20次唤醒)
- 复测采集: 四通道音频 + 完整日志 (20次)
- 定位确认: 硬件正常，应用层逻辑问题

## 修复建议
见诊断结论 P0/P1 优先级

---
报告生成: AI ReportAgent v1.0
""",
        stage_errors=[]
    )

    print_stage(
        "5️⃣ 报告生成 (ReportAgent → TestReport)",
        report,
        "整合需求+用例+数据+诊断，生成完整Markdown报告"
    )

    # ============================================================
    # 总结
    # ============================================================
    print("\n" + "✅"*40)
    print("数据流转完成！")
    print("✅"*40)
    print("""
核心数据流:
    用户需求 (str)
      ↓ RequirementAgent
    TestRequirement (结构化需求)
      ↓ CaseAgent
    TestCase[] (用例矩阵)
      ↓ ExecutorAgent
    RawData[] (原始证据)
      ↓ AnalysisAgent
    Diagnosis (分层定位结论)
      ↓ (可选: need_reproduce=True 时)
    RawData[] (补采证据) → 重新AnalysisAgent
      ↓ ReportAgent
    TestReport (完整报告)

关键特性:
1. 每个阶段产出标准数据结构 (dataclass)
2. 分析Agent可自主决策是否复测 (need_reproduce字段)
3. 自主复测最多2轮 (MAX_REPRODUCE_ROUNDS=2)
4. 所有数据可序列化到workspace (checkpoint机制)
5. Golden-set自动记录新bug (is_new_bug=True时)
""")


if __name__ == "__main__":
    main()
