#!/usr/bin/env python3
"""
case2 唤醒测试 - 3m 正对 安静环境

测试方案: D:\LL20\测试方案_台灯唤醒&识别率测试方案.xlsx
用例清单: D:\LL20\LL20唤醒&识别测试用例.xlsx
case2 参数: 音源距离 3m、角度 90°(正对)、音源声压级 65dB、环境噪声 45dB
唤醒词: 小白小白
唤醒次数: 300
音频文件: D:\LL20\唤醒音频-小白-300次.mp3
验收标准: 唤醒率 ≥ 90%，信噪比 20dB

流程：需求分析 → 用例设计 → 执行（~26分钟）→ 结果分析 → 报告生成
支持 checkpoint/resume：执行阶段跑完后 raw_data 落盘，分析/报告崩溃可 resume
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from datetime import datetime
from core import config

# 全局配置
WORKSPACE = r"D:\LL20\case2_workspace"
OUTPUT_DIR = r"D:\LL20"
AUDIO_FILE = r"D:\LL20\唤醒音频-小白-300次.mp3"
EXPECTED_COUNT = 300
DEVICE_ADDR = "10.7.187.152:5555"

# 灵敏度档位：较低 / 标准 / 较高。默认跑"标准"，其它档位改这里即可
SENSITIVITY = "标准"

# case2 元数据（透传到插件）
CASE2_META = {
    "case_id": "case2",
    "distance_m": 3.0,
    "angle_deg": 90,
    "environment": "安静环境",
    "source_spl_dba": 65,
    "ambient_noise_dba": 45,
    "snr_db": 20,
    "wakeword": "小白小白",
    "sensitivity": SENSITIVITY,
}


def main():
    print("=" * 70)
    print("AI Agent 测试平台 - case2 唤醒测试")
    print("=" * 70)
    print(f"用例: case2 (3m 正对 安静环境)")
    print(f"音频: {AUDIO_FILE}")
    print(f"预期: {EXPECTED_COUNT} 次，成功率 ≥ 90%")
    print(f"Workspace: {WORKSPACE}")
    print(f"设备: {DEVICE_ADDR}")
    print("=" * 70)
    print()

    # 设置设备地址
    config.device_addr = DEVICE_ADDR

    # 创建 workspace（checkpoint 会自动建 .checkpoints 子目录）
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)

    # 加载台灯插件
    import core.plugins.acoustic.lamp_plugin  # noqa: F401
    from core.plugins import default_registry

    plugin = default_registry.get("acoustic_lamp")
    if plugin is None:
        print("✗ 找不到 acoustic_lamp 插件，退出")
        return 1

    from core.agents import Coordinator

    coord = Coordinator(plugin, workspace_path=WORKSPACE)

    # 检查是否有断点可 resume
    from core.checkpoint import CheckpointStore
    store = CheckpointStore(WORKSPACE)
    latest = store.load_latest()
    if latest:
        print(f"发现 checkpoint: {latest.checkpoint_id}")
        print(f"  已完成阶段: {latest.completed_stages}")
        print(f"  当前阶段: {latest.current_stage}")
        user_choice = input("\n是否从断点恢复? [Y/n]: ").strip().lower()
        if user_choice in ("", "y", "yes"):
            print("\n从断点恢复执行...\n")
            return _run_resume(coord)

    print("开始全新执行...\n")
    return _run_full(coord)


def _run_full(coord: "Coordinator") -> int:
    """全新执行：走完 5 阶段流水线，支持中途 checkpoint"""
    from core.plugins.models import TestCase

    # 事件回调
    def on_event(kind: str, payload):
        if kind == "stage":
            print(f"\n{'='*70}")
            print(f"阶段: {payload['stage']}")
            print('='*70)
        elif kind == "exec":
            print(f"  → 执行用例 {payload['case']}")
        elif kind == "tool_result":
            status = payload["result"].get("status", "?")
            if status == "success" and "summary" in payload["result"]:
                s = payload["result"]["summary"]
                print(f"    ← 完成: {s.get('detected_count')}/{s.get('expected_count')} "
                      f"成功率 {s.get('success_rate')} 耗时 {s.get('total_time')}")
            elif status != "success":
                print(f"    ← 失败: {payload['result'].get('result', '?')}")

    # 构造需求（需求分析 Agent 用自然语言理解，这里包含 case2 关键信息）
    requirement = f"""
对 AI 台灯（LL20）执行 case2 唤醒测试：
- 测试条件：音源距离 3m、正对（90°）、安静环境（环境噪声 45dB、音源 65dB、SNR 20dB）
- 唤醒词：小白小白
- 唤醒灵敏度档位：{SENSITIVITY}（可选：较低/标准/较高）
- 音频文件：{AUDIO_FILE}
- 预期唤醒 {EXPECTED_COUNT} 次，验收标准：唤醒率 ≥ 90%
"""

    # 跑完整流水线（Coordinator 自动在每阶段打 checkpoint）
    try:
        report = coord.run(requirement, on_event=on_event)
    except KeyboardInterrupt:
        print("\n\n用户中断。checkpoint 已保存，可用 resume 恢复。")
        return 130
    except Exception as e:
        print(f"\n\n执行异常: {e}")
        print("checkpoint 已保存（如有），可尝试 resume 恢复。")
        return 1

    # 保存报告
    return _save_report(report, is_resume=False)


def _run_resume(coord: "Coordinator") -> int:
    """从 checkpoint 恢复执行（含 PreflightAgent 漂移研判）"""
    def on_event(kind: str, payload):
        if kind == "stage":
            print(f"\n{'='*70}")
            print(f"阶段: {payload['stage']}")
            print('='*70)
        elif kind == "preflight":
            print(f"\n漂移研判: {payload.get('decision', '?')}")
            print(f"  依据: {payload.get('reason', '?')}")
            if payload.get("affected_stages"):
                print(f"  受影响阶段: {payload['affected_stages']}")
        elif kind == "tool_result" and "summary" in payload.get("result", {}):
            s = payload["result"]["summary"]
            print(f"    ← {s.get('detected_count')}/{s.get('expected_count')} "
                  f"{s.get('success_rate')} {s.get('total_time')}")

    try:
        report = coord.resume(on_event=on_event, run_preflight=True)
    except RuntimeError as e:
        print(f"\nresume 失败: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n\n用户再次中断。")
        return 130
    except Exception as e:
        print(f"\n\nresume 异常: {e}")
        return 1

    return _save_report(report, is_resume=True)


def _save_report(report: "TestReport", is_resume: bool) -> int:
    """保存完整报告到 D:\LL20"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = Path(OUTPUT_DIR) / f"case2_report_{timestamp}.md"

    print("\n" + "=" * 70)
    print("测试完成，生成报告...")
    print("=" * 70)

    content_parts = []
    content_parts.append("# AI Agent 测试平台 - case2 唤醒测试报告\n")
    content_parts.append(f"**生成时间**: {datetime.now().isoformat()}\n")
    content_parts.append(f"**恢复模式**: {'是 (resume)' if is_resume else '否 (全新执行)'}\n")
    content_parts.append(f"**插件**: {report.plugin}\n")
    content_parts.append(f"**设备**: {report.requirement.device}\n\n")

    if report.stage_errors:
        content_parts.append("## ⚠️ 流程错误\n\n")
        for err in report.stage_errors:
            content_parts.append(f"- {err}\n")
        content_parts.append("\n")

    content_parts.append("## 测试用例\n\n")
    for case in report.cases:
        content_parts.append(f"### [{case.case_id}] {case.title}\n")
        content_parts.append(f"- **预期**: {case.expected}\n")
        if case.params.get("collector_args", {}).get("case_meta"):
            meta = case.params["collector_args"]["case_meta"]
            content_parts.append(f"- **条件**: {meta}\n")
        content_parts.append("\n")

    content_parts.append("## 执行结果\n\n")
    for raw in report.raw_data:
        content_parts.append(f"### {raw.case_id} ({raw.collector})\n\n")
        if isinstance(raw.payload, dict):
            if "summary" in raw.payload:
                s = raw.payload["summary"]
                if s.get("sensitivity"):
                    content_parts.append(f"- 灵敏度档位: **{s['sensitivity']}**\n")
                content_parts.append(f"- 检测次数: {s.get('detected_count')}/{s.get('expected_count')}\n")
                content_parts.append(f"- 成功率: {s.get('success_rate')}\n")
                content_parts.append(f"- 耗时: {s.get('total_time')}\n")
                if s.get("avg_ncm"):
                    content_parts.append(f"- 平均置信度: {s['avg_ncm']}\n")
                if s.get("early_stop_reason"):
                    content_parts.append(f"- 提前结束: {s['early_stop_reason']}\n")
            if raw.payload.get("issues"):
                content_parts.append(f"\n**潜在问题**:\n")
                for issue in raw.payload["issues"]:
                    content_parts.append(f"- {issue}\n")
        content_parts.append("\n")

    if report.diagnosis:
        content_parts.append("## 结果分析\n\n")
        content_parts.append(f"**分析轮数**: {report.diagnosis.rounds}  \n")
        content_parts.append(f"**工具调用**: {len(report.diagnosis.tool_calls)} 次\n\n")
        content_parts.append(report.diagnosis.text)
        content_parts.append("\n\n")

    if report.report_md:
        content_parts.append("## 最终报告\n\n")
        content_parts.append(report.report_md)
        content_parts.append("\n")

    output_file.write_text("".join(content_parts), encoding="utf-8")

    print(f"\n✓ 报告已保存: {output_file}")
    print("\n关键结果:")
    for raw in report.raw_data:
        if isinstance(raw.payload, dict) and "summary" in raw.payload:
            s = raw.payload["summary"]
            print(f"  {raw.case_id}: {s.get('detected_count')}/{s.get('expected_count')} "
                  f"({s.get('success_rate')})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
