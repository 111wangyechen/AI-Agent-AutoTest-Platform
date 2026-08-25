"""Golden-set 单元测试：存取、检索、索引重建"""

import tempfile
from pathlib import Path

from core.golden_set import BadCase, BadCaseStatus, GoldenSet


def test_badcase_roundtrip():
    """BadCase 序列化与反序列化"""
    case = BadCase.new(
        title="test case",
        device_model="test-device",
        plugin="test-plugin",
        tags=["tag1", "tag2"],
        symptom="test symptom",
        root_cause="test root",
    )
    assert case.case_id.startswith("bc_")
    assert case.status == BadCaseStatus.HYPOTHESIS
    d = case.to_dict()
    assert d["title"] == "test case"
    case2 = BadCase.from_dict(d)
    assert case2.case_id == case.case_id
    assert case2.status == BadCaseStatus.HYPOTHESIS


def test_golden_set_add_and_query():
    """添加 bad-case 并检索"""
    with tempfile.TemporaryDirectory() as tmp:
        gs = GoldenSet(root_path=tmp)
        case = BadCase.new(
            title="唤醒失败",
            device_model="AI台灯",
            plugin="acoustic_lamp",
            tags=["唤醒", "失败"],
            log_signatures=["pcm_busy"],
            symptom="唤醒率 0%",
            root_cause="设备忙",
        )
        case_id = gs.add(case)
        assert case_id == case.case_id

        # 按 tag 查询
        results = gs.query(tags=["唤醒"])
        assert len(results) == 1
        assert results[0]["case_id"] == case_id
        assert results[0]["score"] == 2  # 1 tag × 2分

        # 按 log_signature 查询
        results2 = gs.query(log_signatures=["pcm_busy"])
        assert len(results2) == 1
        assert results2[0]["score"] == 3  # 1 sig × 3分

        # tag + sig 累加分数
        results3 = gs.query(tags=["唤醒"], log_signatures=["pcm_busy"])
        assert len(results3) == 1
        assert results3[0]["score"] == 5  # 2+3

        # 精确过滤 device_model
        results4 = gs.query(device_model="AI台灯", tags=["唤醒"])
        assert len(results4) == 1
        results5 = gs.query(device_model="其它设备", tags=["唤醒"])
        assert len(results5) == 0


def test_golden_set_rebuild_index():
    """索引重建（从 JSON 全量重建）"""
    with tempfile.TemporaryDirectory() as tmp:
        gs = GoldenSet(root_path=tmp)
        case1 = BadCase.new(title="case1", tags=["tag1"])
        case2 = BadCase.new(title="case2", tags=["tag2"])
        gs.add(case1)
        gs.add(case2)

        # 手动删除索引数据库
        index_db = Path(tmp) / "index.db"
        index_db.unlink()
        assert not index_db.exists()

        # 重建
        count = gs.rebuild_index()
        assert count == 2
        assert index_db.exists()

        # 检索验证
        results = gs.query(tags=["tag1"])
        assert len(results) == 1
        assert results[0]["title"] == "case1"


def test_golden_set_record_occurrence():
    """累加观察次数"""
    with tempfile.TemporaryDirectory() as tmp:
        gs = GoldenSet(root_path=tmp)
        case = BadCase.new(title="test", seen_count=1)
        gs.add(case)

        # 再次观察
        updated = gs.record_occurrence(case.case_id)
        assert updated is not None
        assert updated.seen_count == 2

        # 重新加载验证持久化
        case2 = gs.get(case.case_id)
        assert case2.seen_count == 2


def test_golden_set_query_summary():
    """query_summary 返回 Markdown 摘要"""
    with tempfile.TemporaryDirectory() as tmp:
        gs = GoldenSet(root_path=tmp)
        case = BadCase.new(title="test bug", symptom="症状", root_cause="根因", tags=["test"])
        gs.add(case)

        summary = gs.query_summary(tags=["test"])
        assert "找到 1 条" in summary
        assert "test bug" in summary
        assert "症状" in summary
        assert "根因" in summary


def test_golden_set_no_match_returns_empty():
    """无匹配返回空结果"""
    with tempfile.TemporaryDirectory() as tmp:
        gs = GoldenSet(root_path=tmp)
        results = gs.query(tags=["不存在的tag"])
        assert len(results) == 0
        summary = gs.query_summary(tags=["不存在"])
        assert "未找到" in summary


def test_golden_set_mark_status():
    """更新 case 状态"""
    with tempfile.TemporaryDirectory() as tmp:
        gs = GoldenSet(root_path=tmp)
        case = BadCase.new(title="test", status=BadCaseStatus.HYPOTHESIS)
        gs.add(case)

        # 标记为已确认
        updated = gs.mark_status(case.case_id, BadCaseStatus.CONFIRMED)
        assert updated.status == BadCaseStatus.CONFIRMED

        # 标记为已修复
        updated2 = gs.mark_status(case.case_id, BadCaseStatus.RESOLVED, fixed_version="v1.2.3")
        assert updated2.status == BadCaseStatus.RESOLVED
        assert updated2.fixed_version == "v1.2.3"
