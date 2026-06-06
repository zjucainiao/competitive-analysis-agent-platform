"""RunState + reducer 单元测试。TDD Step 1: 先写失败测试。"""
from __future__ import annotations

from backend.orchestrator.run_state import RunState, NodeRun, merge_outputs, append_list


def test_merge_outputs_last_write_wins_and_keeps_others():
    assert merge_outputs({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}


def test_append_list_concatenates():
    assert append_list([1, 2], [3]) == [1, 2, 3]


def test_runstate_defaults():
    s = RunState(project_id="p", run_id="r", analysis_mode="competitive_compare", products=["Notion"])
    assert s.outputs == {} and s.history == [] and s.qa_round == 0 and s.aborted is False
