import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.models import AgentRun, CaptureSnapshot, ProductItem, utc_now
from app.product_search import ProductSearch, candidate_rows, search_gate, search_url
from app.search_runtime import validate_alternatives
from app.search_schemas import (
    AdoptSearchCandidateArgs,
    AlternativeAssessment,
    SearchProductsArgs,
)
from tests.test_research_agent import (
    application,
    observations,
    seed,
    start,
    task_items,
    tool,
    wait_run,
)


def request(platform="jd"):
    return SearchProductsArgs(
        platform=platform, query="家用净水器", reason="查找符合硬条件的其他品牌"
    )


def test_queries_are_encoded_and_candidates_are_bounded_deduplicated_and_scoped():
    assert search_url("jd", "牛奶 & 1L").startswith("https://search.jd.com/Search?keyword=")
    assert "%26" in search_url("taobao", "牛奶 & 1L")
    rows = [
        {"url": "https://evil.test/item.htm?id=123", "title": "拒绝"},
        {"url": "https://item.jd.com@evil.test/1.html", "title": "拒绝"},
        {"url": "https://item.taobao.com/item.htm?id=123", "title": "另一平台"},
        {"url": "https://item.jd.com/1.html?tracking=private", "title": "商品一"},
        {"url": "http://item.jd.com/1.html?foo=bar", "title": "重复商品"},
        *[
            {"url": f"https://item.jd.com/{index}.html", "title": f"商品{index}"}
            for index in range(2, 12)
        ],
    ]
    result = candidate_rows(rows, "jd")
    assert len(result) == 5
    assert result[0]["url"] == "https://item.jd.com/1.html"
    assert len({entry["id"] for entry in result}) == 5


@pytest.mark.parametrize(
    "platform,url",
    [
        ("jd", "https://item.jd.com/100002.html"),
        ("taobao", "https://detail.tmall.com/item.htm?id=100002"),
    ],
)
def test_real_browser_reads_fixture_and_does_not_follow_candidate_links(tmp_path, platform, url):
    visited = []

    def fixture(route):
        visited.append(route.request.url)
        route.fulfill(
            content_type="text/html; charset=utf-8",
            body=f'<html><title>搜索结果</title><body><a href="{url}">品牌乙净水器</a>'
            '<a style="display:none" href="https://item.jd.com/3.html">隐藏商品</a></body></html>',
        )

    service = ProductSearch(
        Settings(_env_file=None, data_dir=tmp_path),
        route_fixture=fixture,
        resolver=lambda _: ["1.1.1.1"],
    )
    result = service.search(request(platform))
    assert result["status"] == "results"
    assert result["candidates"][0]["url"] == url
    assert all(url not in visited_url for visited_url in visited)
    assert result["observed_at"]


def test_login_or_verification_overlay_is_not_no_results(tmp_path):
    service = ProductSearch(
        Settings(_env_file=None, data_dir=tmp_path),
        resolver=lambda _: ["1.1.1.1"],
        route_fixture=lambda route: route.fulfill(
            content_type="text/html; charset=utf-8",
            body='<p>请完成验证</p><a href="https://item.jd.com/100002.html">商品</a>',
        ),
    )
    result = service.search(request())
    assert result["status"] == "blocked"
    assert result["candidates"] == []
    assert search_gate("https://passport.jd.com/login", "jd", "", "", 200) == "login_required"


def test_private_network_is_rejected_before_browser_launch(tmp_path):
    service = ProductSearch(
        Settings(_env_file=None, data_dir=tmp_path),
        resolver=lambda _: ["127.0.0.1"],
        route_fixture=lambda _: pytest.fail("network must not run"),
    )
    assert service.search(request())["error_code"] == "destination_blocked"


def test_explicit_empty_search_and_visible_login_frame_have_distinct_states(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    empty = ProductSearch(
        settings,
        resolver=lambda _: ["1.1.1.1"],
        route_fixture=lambda route: route.fulfill(
            content_type="text/html; charset=utf-8",
            body="<p>没有找到符合条件的商品</p>",
        ),
    )
    assert empty.search(request())["status"] == "no_results"
    login = ProductSearch(
        settings,
        resolver=lambda _: ["1.1.1.1"],
        route_fixture=lambda route: route.fulfill(
            content_type="text/html; charset=utf-8",
            body='<iframe src="https://login.taobao.com/member/login.jhtml"></iframe>'
            if "search.jd.com" in route.request.url
            else "<p>登录</p>",
        ),
    )
    assert login.search(request())["error_code"] == "login_required"
    assert (
        search_gate("https://cfe.m.jd.com/privatedomain/risk_handler/03101900/", "jd", "", "", None)
        == "verification_required"
    )


def add_brand_snapshot(client, item_id, brand="品牌乙"):
    with client.app.state.session_factory() as session:
        product = session.get(ProductItem, item_id)
        snapshot = CaptureSnapshot(
            item_id=item_id,
            platform="jd",
            adapter_id="test",
            adapter_version="1",
            status="partial",
            captured_at=utc_now(),
            page_url=product.url,
            idempotency_key=str(uuid4()),
            request_hash="synthetic",
            payload={
                "evidence": [
                    {
                        "evidence_id": "brand",
                        "field_path": "product.brand",
                        "source": "visible_dom",
                        "excerpt": brand,
                    },
                    {
                        "evidence_id": "spec",
                        "field_path": "specifications.capacity",
                        "source": "visible_dom",
                        "excerpt": "容量 1L",
                    },
                ]
            },
        )
        session.add(snapshot)
        session.commit()
        return snapshot.id


def fake_search(payload, **_):
    return ProductSearch.result(
        payload,
        "results",
        candidates=candidate_rows(
            [
                {"url": "https://item.jd.com/100002.html", "title": "品牌乙商品"},
            ],
            "jd",
        ),
    )


def test_agent_search_adopt_verify_and_report_with_durable_replay(tmp_path, configured):
    def model(messages, _):
        seen = observations(messages)
        original = task_items(messages)[0]["item_id"]
        if not seen:
            return tool("read_product_evidence", {"item_id": original})
        if len(seen) == 1:
            return tool(
                "set_search_requirements",
                {
                    "source_item_id": original,
                    "hard_conditions": [{"id": "c1", "description": "容量为1L"}],
                    "preferences": [],
                },
            )
        if len(seen) == 2:
            return tool("search_products", request().model_dump())
        if len(seen) == 3:
            return tool(
                "adopt_search_candidate",
                {
                    "search_id": seen[-1]["search_id"],
                    "candidate_id": seen[-1]["candidates"][0]["id"],
                    "reason": "核实详情",
                },
            )
        if len(seen) == 4:
            add_brand_snapshot(client, seen[-1]["item_id"])
            return tool("read_product_evidence", {"item_id": seen[-1]["item_id"]})
        source, candidate = seen[0], seen[4]
        return tool(
            "submit_report",
            {
                "outcome": "recommendation",
                "summary": "候选满足当前硬条件，价格仍待核验。",
                "findings": [
                    {
                        "text": "候选详情记载容量1L。",
                        "evidence_refs": [candidate["evidence"][1]["ref"]],
                    }
                ],
                "unknowns": ["尚未核验价格"],
                "alternatives": [
                    {
                        "item_id": candidate["item_id"],
                        "status": "qualified",
                        "brand_relation": "different",
                        "brand_evidence_refs": [
                            source["evidence"][0]["ref"],
                            candidate["evidence"][0]["ref"],
                        ],
                        "checks": [
                            {
                                "condition_id": "c1",
                                "result": "matched",
                                "explanation": "详情容量1L",
                                "evidence_refs": [candidate["evidence"][1]["ref"]],
                            }
                        ],
                        "tradeoffs": "价格未知",
                    }
                ],
            },
        )

    app = application(tmp_path / "search.sqlite3", model)
    app.state.research_runtime.searcher = SimpleNamespace(search=fake_search)
    with TestClient(app) as client:
        task_id = seed(client)
        original = client.get(f"/api/v1/tasks/{task_id}").json()["items"][0]["id"]
        add_brand_snapshot(client, original, "品牌甲")
        start(client, task_id)
        completed = wait_run(client, task_id, {"completed"})
        assert completed["report"]["alternatives"][0]["status"] == "qualified"
        assert completed["report"]["search_requirements"]["hard_conditions"][0]["id"] == "c1"
        assert len(client.get(f"/api/v1/tasks/{task_id}").json()["items"]) == 2
        assert sum(event["kind"] == "search" for event in completed["events"]) == 1
        assert any(
            event["data"].get("output", {}).get("status") == "review_required"
            for event in completed["events"]
        )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        "app.research_routes.get_settings",
        lambda: SimpleNamespace(llm_base_url="http://gateway.invalid/v1", llm_model="test-model"),
    )


def test_adoption_is_atomic_replayable_scoped_and_cancelled_run_cannot_write(tmp_path, configured):
    app = application(tmp_path / "adoption.sqlite3", lambda *_: None)
    runtime = app.state.research_runtime
    runtime.searcher = SimpleNamespace(search=fake_search)
    with TestClient(app) as client:
        task_id = seed(client)
        with runtime.sessions() as session:
            run = AgentRun(
                task_id=task_id,
                goal="替代",
                model="test",
                status="running",
                idempotency_key=str(uuid4()),
                request_hash="test",
            )
            session.add(run)
            session.commit()
            run_id = run.id
        deadline = time.monotonic() + 30
        searched = runtime._search_products(run_id, "search-call", request(), deadline)
        runtime.searcher.search = lambda *_args, **_kwargs: pytest.fail(
            "replayed search must not access network"
        )
        assert runtime._search_products(run_id, "search-call", request(), deadline) == searched
        args = AdoptSearchCandidateArgs(
            search_id=searched["search_id"],
            candidate_id=searched["candidates"][0]["id"],
            reason="核验候选",
        )
        first = runtime._adopt_search_candidate(run_id, task_id, "adopt-call", args, deadline, [])
        assert (
            runtime._adopt_search_candidate(run_id, task_id, "adopt-call", args, deadline, [])
            == first
        )
        with runtime.sessions() as session:
            assert (
                len(
                    session.scalars(select(ProductItem).where(ProductItem.task_id == task_id)).all()
                )
                == 2
            )
            run = session.get(AgentRun, run_id)
            assert len([event for event in run.events if event["kind"] == "candidate"]) == 1
        foreign = args.model_copy(update={"search_id": "search-" + "0" * 64})
        assert "error" in runtime._adopt_search_candidate(
            run_id, task_id, "other-call", foreign, deadline, []
        )
        runtime.searcher.search = fake_search
        for index in range(3):
            assert (
                runtime._search_products(run_id, f"next-{index}", request(), deadline)["status"]
                == "results"
            )
        assert "error" in runtime._search_products(run_id, "over-limit", request(), deadline)
        client.post(f"/api/v1/tasks/{task_id}/research/{run_id}/cancel")
        from app.research_agent import AgentStopped

        with pytest.raises(AgentStopped):
            runtime._adopt_search_candidate(run_id, task_id, "cancel-call", args, deadline, [])


def test_search_candidates_survive_pause_restart_and_resume_without_researching(
    tmp_path, configured
):
    def model(messages, _):
        seen = observations(messages)
        if not seen:
            return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
        if len(seen) == 1:
            return tool(
                "set_search_requirements",
                {
                    "source_item_id": task_items(messages)[0]["item_id"],
                    "hard_conditions": [{"id": "c1", "description": "容量1L"}],
                },
            )
        if len(seen) == 2:
            return tool("search_products", request().model_dump())
        if len(seen) == 3:
            return tool(
                "adopt_search_candidate",
                {
                    "search_id": seen[2]["search_id"],
                    "candidate_id": seen[2]["candidates"][0]["id"],
                    "reason": "核对容量",
                },
            )
        if len(seen) == 4:
            return tool("ask_user", {"question": "是否仍以1L为硬条件？", "reason": "合成恢复验收"})
        if len(seen) == 5:
            add_brand_snapshot(client, seen[3]["item_id"])
            return tool("read_product_evidence", {"item_id": seen[3]["item_id"]})
        candidate = seen[5]
        return tool(
            "submit_report",
            {
                "outcome": "needs_evidence",
                "summary": "容量可核实，但原商品品牌未知。",
                "findings": [
                    {"text": "候选容量1L", "evidence_refs": [candidate["evidence"][1]["ref"]]}
                ],
                "unknowns": ["品牌关系未知"],
                "alternatives": [
                    {
                        "item_id": candidate["item_id"],
                        "status": "needs_evidence",
                        "brand_relation": "unknown",
                        "checks": [
                            {
                                "condition_id": "c1",
                                "result": "matched",
                                "explanation": "容量1L",
                                "evidence_refs": [candidate["evidence"][1]["ref"]],
                            }
                        ],
                        "tradeoffs": "暂不可认定跨品牌替代",
                    }
                ],
            },
        )

    path = tmp_path / "resume-search.sqlite3"
    first = application(path, model)
    first.state.research_runtime.searcher = SimpleNamespace(search=fake_search)
    with TestClient(first) as client:
        task_id = seed(client)
        start(client, task_id)
        paused = wait_run(client, task_id, {"needs_input"})
    second = application(path, model)
    second.state.research_runtime.searcher = SimpleNamespace(
        search=lambda *_args, **_kwargs: pytest.fail("must reuse search history after restart")
    )
    with TestClient(second) as client:
        response = client.post(
            f"/api/v1/tasks/{task_id}/research/{paused['id']}/resume",
            json={"answer": "仍以1L为硬条件"},
        )
        assert response.status_code == 202
        completed = wait_run(client, task_id, {"completed"})
        assert completed["report"]["alternatives"][0]["status"] == "needs_evidence"
        assert len(client.get(f"/api/v1/tasks/{task_id}").json()["items"]) == 2
        assert sum(event["kind"] == "search" for event in completed["events"]) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "search_ref",
        "wrong_item",
        "old_snapshot",
        "blocked",
        "missing_brand",
        "unknown_condition",
        "missing_candidate",
    ],
)
def test_report_rejects_unsupported_alternative_claims(bad):
    refs = {
        "source:brand": {
            "item_id": "source",
            "snapshot_id": "s1",
            "capture_status": "partial",
            "field_path": "product.brand",
            "source": "visible_dom",
        },
        "candidate:brand": {
            "item_id": "candidate",
            "snapshot_id": "s2",
            "capture_status": "partial",
            "field_path": "product.brand",
            "source": "visible_dom",
        },
        "candidate:spec": {
            "item_id": "candidate",
            "snapshot_id": "s2",
            "capture_status": "partial",
            "field_path": "specifications",
            "source": "visible_dom",
        },
    }
    assessment = AlternativeAssessment(
        item_id="candidate",
        status="qualified",
        brand_relation="different",
        brand_evidence_refs=["source:brand", "candidate:brand"],
        checks=[
            {
                "condition_id": "c1",
                "result": "matched",
                "explanation": "满足",
                "evidence_refs": ["candidate:spec"],
            }
        ],
        tradeoffs="价格未知",
    )
    requirements = {"source_item_id": "source", "hard_conditions": [{"id": "c1"}]}
    if bad == "search_ref":
        assessment.checks[0].evidence_refs = ["search:candidate"]
    elif bad == "wrong_item":
        refs["candidate:spec"]["item_id"] = "other"
    elif bad == "old_snapshot":
        refs["candidate:spec"]["snapshot_id"] = "old"
    elif bad == "blocked":
        refs["candidate:spec"]["capture_status"] = "blocked"
    elif bad == "missing_brand":
        assessment.brand_evidence_refs = ["candidate:brand"]
    elif bad == "unknown_condition":
        assessment.checks[0].result = "unknown"
    assert validate_alternatives(
        [] if bad == "missing_candidate" else [assessment],
        requirements,
        ["candidate"],
        refs,
        {"source": "s1", "candidate": "s2"},
    )
