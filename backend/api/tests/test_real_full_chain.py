"""端到端真实 LLM 全链路测试。

走的是完整生产路径：
    POST /api/projects (real mode)
      → POST /api/projects/{id}/run
        → Orchestrator.run() 调度 5 个真 Agent + LangGraph 状态机
          → DeepSeek API + Tavily/Serper 真采集
      → 轮询 GET /api/projects/{id}/run-state 直到整体终态(done/failed/aborted)

显式 opt-in（双重门控）::

    RUN_REAL_LLM_TESTS=1 pytest backend/api/tests/test_real_full_chain.py -m e2e -v -s

必须 ``RUN_REAL_LLM_TESTS=1`` 且 ``DEEPSEEK_API_KEY`` 或 ``OPENAI_API_KEY``
至少有一个非空，否则整文件 skip；默认 ``pytest``（addopts ``-m "not e2e"``）反选本文件。
"""

from __future__ import annotations

import os
import time

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from backend.api import create_app

# 显式开启真实链路时才读 .env 补 LLM key；模块级无条件 load_dotenv 会在收集
# 阶段把开发者 .env 的 POSTGRES_DSN / REDIS_URL 泄漏进进程环境，
# 破坏 storage 测试「无环境变量自动 skip」的约定。override=False：不覆盖已导出变量。
if os.getenv("RUN_REAL_LLM_TESTS") == "1":
    load_dotenv(override=False)


def _has_any_llm_key() -> bool:
    return any(
        os.getenv(k)
        for k in ("DOUBAO_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    )


def _real_llm_disabled() -> bool:
    if os.getenv("RUN_REAL_LLM_TESTS") != "1":
        return True
    return not _has_any_llm_key()


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        _real_llm_disabled(),
        reason=(
            "real full-chain test: set RUN_REAL_LLM_TESTS=1 + "
            "DOUBAO_API_KEY (or DEEPSEEK / OPENAI) to enable"
        ),
    ),
]


@pytest.fixture
def client() -> TestClient:
    """构造真实 LLM app。

    - max_parallel=8：让 collect/extract 在 multi-product + _v2 派生时并行跑得开
    - 让 FeedbackRouter 默认 max_rounds=3 即可（test 内只验证至少一次跑通）
    """
    app = create_app(mode="memory", max_parallel=8)
    with TestClient(app) as c:
        yield c


_TIMEOUT_SECONDS = 2400.0  # 40 min：Extractor 单次 ≤ 10min × 2 attempts，加反馈环


@pytest.mark.slow
def test_real_full_chain_end_to_end(client: TestClient) -> None:
    """1 个 target + 1 个竞品（控制成本），跑真实链路 → 验证 5 个 Agent 都产出 + 报告非空。"""
    payload = {
        "project_name": "Real full-chain e2e",
        "owner": "test_user",
        "target_product": "Notion",
        "competitors": ["Asana"],
        "industry": "collaboration_saas",
        "report_template_id": "standard_v1",
    }
    r = client.post("/api/projects", json=payload)
    assert r.status_code == 201, r.text
    project = r.json()
    pid = project["project_id"]
    assert project["mode"] == "real"
    assert project["collect_constraints"]["fallback_to_mock"] is False

    r = client.post(f"/api/projects/{pid}/run")
    assert r.status_code == 202, r.text
    assert r.json()["thread_id"] == pid

    deadline = time.time() + _TIMEOUT_SECONDS
    last_state: dict | None = None
    last_log_time = 0.0
    while time.time() < deadline:
        sr = client.get(f"/api/projects/{pid}/run-state")
        assert sr.status_code == 200
        last_state = sr.json()
        status = last_state.get("status")
        verdicts = last_state.get("verdicts") or []
        # 整体终态：done / failed / aborted
        if status in {"done", "failed", "aborted"}:
            break
        # 每 30 秒输出一次进度，方便观察长跑卡在哪
        now = time.time()
        if now - last_log_time > 30.0:
            last_log_time = now
            done_outputs = list((last_state.get("outputs") or {}).keys())
            print(
                f"[t+{int(now - (deadline - _TIMEOUT_SECONDS))}s] "
                f"status={status} verdicts={len(verdicts)} outputs={done_outputs}",
                flush=True,
            )
        time.sleep(3.0)
    else:
        pytest.fail(
            f"real chain timeout after {_TIMEOUT_SECONDS}s; "
            f"status={(last_state or {}).get('status')} "
            f"outputs={list((last_state or {}).get('outputs', {}).keys())}"
        )

    outputs = last_state["outputs"]
    verdicts = last_state["verdicts"]

    # 1. 5 个 Agent 类阶段至少各有一次完整 output(原生 run_ref 命名,大小写/产品后缀无关)
    for stage in ["collect", "extract", "analyst", "reporter", "qa"]:
        assert any(
            k == stage or k.startswith(f"{stage}.") or k.startswith(f"{stage}_v")
            for k in outputs
        ), f"stage {stage} produced no output (real chain failed at {stage})"

    # 2. Analyst 真出了 claim
    analyst_out = outputs["analyst"]
    analyst_status = analyst_out.get("status")
    print("\n=== ANALYST DEBUG ===")
    print(f"  status: {analyst_status}")
    print(f"  confidence: {analyst_out.get('confidence')}")
    print(f"  errors[:3]: {analyst_out.get('errors', [])[:3]}")
    print(f"  self_critique: {(analyst_out.get('self_critique') or '')[:200]}")
    print(f"  has 'result' key: {'result' in analyst_out}")
    if analyst_status == "failed":
        pytest.fail(
            f"analyst status=failed; errors={analyst_out.get('errors')}"
        )
    result = analyst_out.get("result")
    assert result, f"analyst missing 'result' field; output keys={list(analyst_out.keys())}"
    total_claims = sum(
        len(d.get("claims") or []) for d in result.get("dimensions", {}).values()
    )
    assert total_claims > 0, f"analyst returned 0 claims; result={result}"

    # 3. Reporter 出了 section（最终版本）
    final_reporter_key = "reporter"
    versioned = sorted(
        (k for k in outputs if k.startswith("reporter_v")),
        reverse=True,
    )
    if versioned:
        final_reporter_key = versioned[0]
    reporter_out = outputs[final_reporter_key]
    if reporter_out.get("status") == "failed":
        pytest.fail(f"{final_reporter_key} failed: {reporter_out.get('errors')}")
    draft = reporter_out.get("draft") or {}
    assert draft.get("sections"), f"real report has no sections; draft keys={list(draft.keys())}"

    # 4. QA 真做了判定
    assert verdicts, "no QA verdict persisted"
    assert verdicts[0]["dimension_results"], "QA produced no dimension results"

    # 5. 早期节点（非 _v 派生）不允许 failed
    early_failed = [
        k for k, o in outputs.items()
        if o.get("status") == "failed" and "_v" not in k
    ]
    assert not early_failed, f"early nodes failed: {early_failed}"

    # 6. 报告几行预览（看到才放心）
    print(f"\n=== real report preview ({final_reporter_key}) ===")
    print(f"sections: {len(draft.get('sections', []))}")
    print(f"summary: {(draft.get('summary') or '')[:200]}")

    # 7. QA verdict 全量诊断（找 reject 真凶）
    last_v = verdicts[-1]
    print("\n=== QA verdict (final) ===")
    print(f"  overall_status: {last_v.get('overall_status')}")
    print(f"  blocking: {last_v.get('blocking')}")
    print("  dimensions:")
    for dim, res in (last_v.get("dimension_results") or {}).items():
        marker = "✓" if res.get("pass") else "✗"
        print(f"    {marker} {dim}: score={res.get('score'):.2f}  notes={(res.get('notes') or '')[:120]}")
    print(f"  issues ({len(last_v.get('issues') or [])}):")
    for iss in (last_v.get("issues") or [])[:8]:
        print(f"    [{iss.get('severity')}] {iss.get('dimension')} → {iss.get('target_agent')}: {(iss.get('problem') or '')[:120]}")
    print(f"  routing: {[(r.get('target_agent'), (r.get('reason') or '')[:60]) for r in (last_v.get('routing') or [])]}")
