"""Durable search/adoption operations and evidence checks for alternatives."""

import hashlib
import time

from sqlalchemy import select, update

from app.capture_urls import product_url_identity
from app.models import AgentRun, ProductItem, Task, utc_now


def validate_alternatives(assessments, requirements, candidate_ids, refs, read_snapshots):
    if not requirements:
        return "请先登记替代搜索的硬条件。" if assessments else None
    source = requirements["source_item_id"]
    expected = {condition["id"] for condition in requirements["hard_conditions"]}
    ids = [entry.item_id for entry in assessments]
    if len(ids) != len(set(ids)) or set(ids) != set(candidate_ids):
        return "每个已加入的搜索候选必须恰好有一份核验结果，不能省略失败候选。"

    def belongs(reference, item_id, brand=False):
        evidence = refs.get(reference, {})
        return (
            evidence.get("item_id") == item_id
            and evidence.get("snapshot_id") == read_snapshots.get(item_id)
            and evidence.get("capture_status") in {"partial", "complete"}
            and evidence.get("source") not in {"navigation", "user_statement"}
            and (not brand or evidence.get("field_path") == "product.brand")
        )

    for assessment in assessments:
        checks = assessment.checks
        if len(checks) != len(expected) or {check.condition_id for check in checks} != expected:
            return "候选必须逐项核对全部硬条件；缺失项标为 unknown。"
        for check in checks:
            if any(not belongs(ref, assessment.item_id) for ref in check.evidence_refs):
                return "硬条件引用必须来自该候选本次读取的有效详情快照。"
            if check.result != "unknown" and not check.evidence_refs:
                return "确认满足或不满足硬条件时必须提供候选详情证据。"
        if any(
            not (belongs(ref, source, True) or belongs(ref, assessment.item_id, True))
            for ref in assessment.brand_evidence_refs
        ):
            return "品牌关系只能引用原商品与候选的有效品牌字段。"
        if assessment.brand_relation != "unknown" and not all(
            any(belongs(ref, item_id, True) for ref in assessment.brand_evidence_refs)
            for item_id in (source, assessment.item_id)
        ):
            return "判断同品牌或不同品牌需引用双方品牌字段，标题相似不作为证明。"
        qualified = assessment.brand_relation == "different" and all(
            check.result == "matched" for check in checks
        )
        rejected = assessment.brand_relation == "same" or any(
            check.result == "unmatched" for check in checks
        )
        expected_status = "qualified" if qualified else "rejected" if rejected else "needs_evidence"
        if assessment.status != expected_status:
            return "候选状态与逐项核验不一致；只有不同品牌且全部硬条件有证据满足才可列为合格。"
    return None


class SearchRuntimeMixin:
    def _search_products(self, run_id, call_id, payload, deadline):
        self._guard(run_id, deadline)
        event_id = "search-" + hashlib.sha256(f"{run_id}:{call_id}".encode()).hexdigest()
        with self.sessions() as session:
            run = session.get(AgentRun, run_id)
            events = [event for event in run.events if event["kind"] == "search"]
            previous = next((event for event in events if event["id"] == event_id), None)
            if previous and previous["data"]["input"] != payload.model_dump():
                return {"error": "同一搜索调用标识不能使用不同参数。"}
            if previous and previous["data"]["output"].get("status") != "opening":
                return previous["data"]["output"]
            if not previous and len(events) >= 4:
                return {"error": "本研究已达到四次搜索上限，请如实报告已检索范围。"}
        self._emit(
            run_id,
            event_id,
            "search",
            "正在搜索替代商品",
            {
                "tool": "search_products",
                "input": payload.model_dump(),
                "output": {"status": "opening"},
            },
        )
        started = time.monotonic()
        result = self.searcher.search(payload, check_cancel=lambda: self._guard(run_id, deadline))
        self._guard(run_id, deadline)
        result = {**result, "search_id": event_id}
        self._emit(
            run_id,
            event_id,
            "search",
            "搜索访问已结束",
            {
                "tool": "search_products",
                "input": payload.model_dump(),
                "output": result,
            },
            round((time.monotonic() - started) * 1000),
        )
        return result

    def _adopt_search_candidate(self, run_id, task_id, call_id, payload, deadline, original_ids):
        self._guard(run_id, deadline)
        event_id = "adopt-" + hashlib.sha256(f"{run_id}:{call_id}".encode()).hexdigest()
        with self.sessions() as session:
            # Obtain the write lock before testing run state; cancellation cannot race the insert.
            active = session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.task_id == task_id,
                    AgentRun.status == "running",
                )
                .values(updated_at=utc_now())
            )
            if active.rowcount != 1:
                return {"error": "运行已结束，未添加候选。"}
            run = session.get(AgentRun, run_id)
            adopted = [event for event in run.events if event["kind"] == "candidate"]
            previous = next((event for event in adopted if event["id"] == event_id), None)
            if previous:
                if previous["data"]["input"] != payload.model_dump():
                    return {"error": "同一候选调用标识不能使用不同参数。"}
                output = previous["data"]["output"]
                existing = session.get(ProductItem, output["item_id"])
                return (
                    output
                    if existing and existing.task_id == task_id
                    else {"error": "此前加入的候选已被移除。"}
                )
            adopted_ids = {event["data"]["output"]["item_id"] for event in adopted}
            search = next(
                (
                    event
                    for event in run.events
                    if event["kind"] == "search" and event["id"] == payload.search_id
                ),
                None,
            )
            candidate = next(
                (
                    entry
                    for entry in (search or {})
                    .get("data", {})
                    .get("output", {})
                    .get("candidates", [])
                    if entry["id"] == payload.candidate_id
                ),
                None,
            )
            if not candidate:
                return {"error": "候选不属于本次运行的实际搜索结果。"}
            identity = product_url_identity(candidate["url"])
            if not identity:
                return {"error": "候选不是可核实的商品详情链接。"}
            items = session.scalars(select(ProductItem).where(ProductItem.task_id == task_id)).all()
            existing = next(
                (
                    item
                    for item in items
                    if product_url_identity(item.url)
                    and (
                        product_url_identity(item.url).platform,
                        product_url_identity(item.url).product_id,
                    )
                    == (identity.platform, identity.product_id)
                ),
                None,
            )
            if existing and existing.id in original_ids:
                return {"error": "该链接已是原比较商品，不能作为新替代候选。"}
            if len(adopted_ids) >= 3 and (not existing or existing.id not in adopted_ids):
                return {"error": "本研究已达到三个候选核验上限。"}
            if not existing and len(items) >= 20:
                return {"error": "任务已达二十件商品上限，未添加候选。"}
            if not existing:
                existing = ProductItem(
                    task_id=task_id,
                    url=identity.safe_url,
                    page_title=candidate["title"],
                    source="web",
                    idempotency_key=event_id,
                    request_hash=hashlib.sha256(identity.safe_url.encode()).hexdigest(),
                )
                session.add(existing)
                session.flush()
            output = {
                "status": "adopted",
                "item_id": existing.id,
                "title": existing.page_title,
                "url": existing.url,
                "search_id": payload.search_id,
                "note": "仅加入待核实候选，请读取详情与品牌并逐项核验硬条件。",
            }
            run.events = [
                *run.events,
                {
                    "id": event_id,
                    "kind": "candidate",
                    "label": "已加入待核实候选",
                    "data": {
                        "tool": "adopt_search_candidate",
                        "input": payload.model_dump(),
                        "output": output,
                    },
                    "elapsed_ms": None,
                    "created_at": utc_now().isoformat(),
                },
            ]
            session.get(Task, task_id).updated_at = utc_now()
            session.commit()
            return output
