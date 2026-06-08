"""RunState + reducer 单元测试。TDD Step 1: 先写失败测试。"""
from __future__ import annotations

from backend.orchestrator.run_state import (
    RunState,
    NodeRun,
    merge_outputs,
    append_list,
    versioned_ref,
    split_versioned,
    latest_output,
    latest_outputs,
)


def test_merge_outputs_last_write_wins_and_keeps_others():
    assert merge_outputs({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}


def test_versioned_ref_and_split_roundtrip():
    assert versioned_ref("reporter", 1) == "reporter"
    assert versioned_ref("reporter", 2) == "reporter_v2"
    assert split_versioned("reporter") == ("reporter", 1)
    assert split_versioned("collect.飞书_v3") == ("collect.飞书", 3)


def test_latest_output_picks_highest_round():
    o = {"reporter": "v1", "reporter_v2": "v2", "reporter_v3": "v3", "analyst": "a"}
    assert latest_output(o, "reporter") == "v3"
    assert latest_output(o, "analyst") == "a"
    assert latest_output(o, "missing") is None


def test_latest_outputs_collapses_to_latest_per_base():
    o = {"extract.Notion": "e1", "extract.Notion_v2": "e2", "analyst": "a"}
    assert latest_outputs(o) == {"extract.Notion": "e2", "analyst": "a"}


def test_latest_output_resolves_product_named_like_version_suffix():
    """P2-VERSIONCONFLICT：产品名本身以 _v<数字> 结尾时其 round1 裸 key 仍能命中。

    仅靠 split_versioned 反解时，"collect.Acme_v2"(产品 Acme_v2 的 round1) 会被误解析成
    ("collect.Acme", 2)，导致 latest_output(.., "collect.Acme_v2") 拿不到它；精确匹配优先修复。
    """
    assert latest_output({"collect.Acme_v2": "round1"}, "collect.Acme_v2") == "round1"
    # 该产品确有 round2(collect.Acme_v2_v2)时取最新
    o2 = {"collect.Acme_v2": "r1", "collect.Acme_v2_v2": "r2"}
    assert latest_output(o2, "collect.Acme_v2") == "r2"


def test_append_list_concatenates():
    assert append_list([1, 2], [3]) == [1, 2, 3]


def test_runstate_defaults():
    s = RunState(project_id="p", run_id="r", analysis_mode="competitive_compare", products=["Notion"])
    assert s.outputs == {} and s.history == [] and s.qa_round == 0 and s.aborted is False
