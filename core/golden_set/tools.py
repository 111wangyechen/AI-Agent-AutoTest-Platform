"""
Golden-set · Agent 工具封装

把 GoldenSet API 封装成 Tool，供分析 Agent 调用。
"""

from __future__ import annotations

from typing import Any

from core.golden_set import GoldenSet
from core.tools import Tool


def query_golden_set(
    device_model: str = "",
    plugin: str = "",
    tags: str = "",
    log_signatures: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """
    检索 Golden-set 中的历史 bad-case，按相似度返回候选列表。

    用法：分析时发现唤醒率低/识别失败等异常现象，先查 Golden-set 是否有相似 case。
    命中则可直接引用现有根因/修复建议，避免重复分析。

    Args:
        device_model: 设备型号（如 "AI台灯·LL20"），精确匹配
        plugin: 插件名（如 "acoustic_lamp"），精确匹配
        tags: 逗号分隔的标签（如 "唤醒,闲聊模式"），命中越多分数越高
        log_signatures: 逗号分隔的日志关键字（如 "LISTEN_MSG_VAD_END,DeviceAccessService"）
        limit: 最多返回几条

    Returns:
        {
          "found": <int>,
          "summary": "<Markdown摘要，直接给 LLM 读>",
          "cases": [{"case_id", "score", "title", "status", "symptom", "root_cause", ...}, ...]
        }
    """
    gs = GoldenSet()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    sig_list = [s.strip() for s in log_signatures.split(",") if s.strip()] if log_signatures else None

    results = gs.query(
        device_model=device_model,
        plugin=plugin,
        tags=tag_list,
        log_signatures=sig_list,
        limit=limit,
    )
    return {
        "found": len(results),
        "summary": gs.query_summary(
            device_model=device_model, plugin=plugin,
            tags=tag_list, log_signatures=sig_list, limit=limit,
        ),
        "cases": results,
    }


QUERY_GOLDEN_SET_TOOL = Tool(
    name="query_golden_set",
    description=(
        "查询 Golden-set 历史 bad-case 知识库，按设备/tag/日志签名找相似 case。"
        "命中后可直接引用现有根因/修复，避免重复分析。用于：发现唤醒率低、识别失败、"
        "周期性漏检等异常现象时，先查是否已知 bug 再深入分析。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "device_model": {
                "type": "string",
                "description": "设备型号（如 'AI台灯·LL20'），可选精确过滤",
            },
            "plugin": {
                "type": "string",
                "description": "插件名（如 'acoustic_lamp'），可选精确过滤",
            },
            "tags": {
                "type": "string",
                "description": "逗号分隔的症状标签（如 '唤醒,闲聊模式,不应期'），命中越多分数越高",
            },
            "log_signatures": {
                "type": "string",
                "description": "逗号分隔的日志关键字（如 'LISTEN_MSG_VAD_END,pcm_busy'），精确匹配",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回几条，默认 5",
                "default": 5,
            },
        },
        "required": [],
    },
    func=query_golden_set,
)
