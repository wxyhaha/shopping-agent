"""Private subprocess entry point. Never print page content, session state or exceptions."""

import json
import sys
import time
from pathlib import Path

from app.background_collector import BackgroundCollector
from app.capture_urls import product_url_identity
from app.collection_process import MAX_REQUEST_BYTES
from app.config import Settings


def main():
    request = Path(sys.argv[1])
    if request.stat().st_size > MAX_REQUEST_BYTES:
        raise SystemExit(2)
    payload = json.loads(request.read_text(encoding="utf-8"))
    settings = Settings(_env_file=None, **payload["settings"])
    identity = product_url_identity(payload["url"])
    if not identity:
        raise SystemExit(2)
    collector = BackgroundCollector(settings)
    result = collector._collect(
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
    }
    path = Path(payload["output_path"])
    path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
