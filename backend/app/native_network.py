"""Filter a collector tab's native Chromium requests, including every redirect hop.

Replaying all resources with route.fetch changes how a signed-in marketplace page loads.
Fetch interception lets Chromium send the original request itself, while still checking
the destination before it is sent. The session belongs to one new collector tab only.
"""

from playwright.sync_api import Error as PlaywrightError


class NativeRequestGuard:
    def __init__(self, page, policy, navigation_check=lambda _url: None):
        self.session = page.context.new_cdp_session(page)
        self.policy = policy
        self.navigation_check = navigation_check
        self.navigation_gate = None
        self.frame_id = self.session.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
        self.session.on("Fetch.requestPaused", self._request)
        # Service-worker responses would bypass interception. This override applies only to
        # our tab; the shared browser's other tabs and persistent settings are untouched.
        self.session.send("Network.setBypassServiceWorker", {"bypass": True})
        self.session.send(
            "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
        )

    def _request(self, event):
        request_id = event["requestId"]
        url = event["request"]["url"]
        document = event["resourceType"] == "Document"
        main_document = document and event["frameId"] == self.frame_id
        try:
            allowed = self.policy.allows(url) and event["resourceType"] != "Media"
            # Child documents can move to separate Chromium targets, outside this session's
            # interception. Do not let an unguarded frame load in the shared browser.
            allowed = allowed and (not document or main_document)
            if main_document:
                gate = (
                    self.navigation_check(url)
                    if allowed
                    else ("destination_blocked", "页面跳转地址未通过允许域名及公网地址校验")
                )
                if gate:
                    self.navigation_gate = gate
                    allowed = False
            self.session.send(
                "Fetch.continueRequest" if allowed else "Fetch.failRequest",
                {"requestId": request_id}
                if allowed
                else {"requestId": request_id, "errorReason": "BlockedByClient"},
            )
        except Exception:
            # A policy/driver failure must never release an unchecked request. Closing the
            # owned tab or the worker deadline also releases any pending interception.
            try:
                self.session.send(
                    "Fetch.failRequest",
                    {"requestId": request_id, "errorReason": "BlockedByClient"},
                )
            except PlaywrightError:
                pass

    def close(self):
        try:
            self.session.detach()
        except PlaywrightError:
            pass
