"""Private subprocess entry point. Never print page content, session state or exceptions."""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from pydantic import ValidationError

from app.background_collector import BackgroundCollector, CollectionResult, unavailable_snapshot
from app.capture_urls import product_url_identity
from app.collection_process import MAX_REQUEST_BYTES, MAX_RESULT_BYTES
from app.config import Settings
from app.schemas import CaptureSnapshotSubmit


def failure_output(identity, code: str, note: str) -> dict:
    return {
        "snapshot": unavailable_snapshot(identity, code, note, failed=True).model_dump(mode="json"),
        "error_code": code,
        "message": note,
        "detail_read_pending": False,
    }


def validation_note(exc: ValidationError) -> str:
    # Locations/types identify the broken contract without returning raw inputs, validator
    # context, URLs, account text or the exception message. Unknown extra keys are redacted.
    schema = CaptureSnapshotSubmit.model_json_schema()
    definitions = [schema, *schema.get("$defs", {}).values()]
    known = {name for definition in definitions for name in definition.get("properties", {})}
    errors = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:6]:
        location = ".".join(
            str(part) if isinstance(part, int) or part in known else "field"
            for part in error["loc"]
        )
        errors.append(f"{location or 'snapshot'}:{error['type']}")
    return "采集结果未通过字段校验（" + "；".join(errors) + "），未保存为有效商品资料。"


def collect_output(payload: dict, settings: Settings, identity) -> dict:
    try:
        result: CollectionResult = BackgroundCollector(settings)._collect(
            identity,
            payload["url"],
            time.monotonic() + settings.browser_timeout_seconds,
            lambda: None,
            payload.get("variant_request"),
        )
        output = {
            "snapshot": result.snapshot.model_dump(mode="json") if result.snapshot else None,
            "error_code": result.error_code,
            "message": result.message,
            "price_screenshot_png": result.price_screenshot_png,
            "detail_read_pending": result.detail_read_pending,
        }
        if len(json.dumps(output, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
            return failure_output(
                identity, "oversized_snapshot", "采集结果超过传输上限，未保存为有效快照。"
            )
        return output
    except ValidationError as exc:
        return failure_output(identity, "invalid_snapshot", validation_note(exc))
    except PlaywrightTimeout:
        return failure_output(identity, "browser_timeout", "浏览器操作达到期限，未取得有效快照。")
    except PlaywrightError:
        return failure_output(
            identity, "browser_error", "浏览器页面访问或读取失败，未取得有效快照。"
        )
    except Exception as exc:
        # An exception string can contain merchant content, session URLs or private paths.
        # Only the Python exception class is a safe diagnostic for unexpected failures.
        return failure_output(
            identity, "worker_exception", f"采集内部异常（{type(exc).__name__}），未取得有效快照。"
        )


def main():
    request = Path(sys.argv[1])
    if request.stat().st_size > MAX_REQUEST_BYTES:
        raise SystemExit(2)
    payload = json.loads(request.read_text(encoding="utf-8"))
    settings = Settings(_env_file=None, **payload["settings"])
    if payload.get("operation") == "search":
        from app.product_search import ProductSearch
        from app.search_schemas import SearchProductsArgs

        output = ProductSearch(settings)._search(
            SearchProductsArgs.model_validate(payload["request"]),
            time.monotonic() + settings.browser_timeout_seconds,
            lambda: None,
        )
        Path(payload["output_path"]).write_text(
            json.dumps(output, ensure_ascii=False), encoding="utf-8"
        )
        return
    identity = product_url_identity(payload["url"])
    if not identity:
        raise SystemExit(2)
    output = collect_output(payload, settings, identity)
    path = Path(payload["output_path"])
    path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
