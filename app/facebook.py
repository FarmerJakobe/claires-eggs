from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class FacebookSyncResult:
    status: str
    detail: str


def publish_post(post: dict, config: dict) -> FacebookSyncResult:
    mode = config.get("FACEBOOK_SYNC_MODE", "demo")

    if not post["publish_to_facebook"]:
        return FacebookSyncResult("not-requested", "Facebook sharing was not requested.")

    if mode == "demo":
        return FacebookSyncResult(
            "simulated",
            "Facebook post recorded in local demo mode.",
        )

    if mode == "manual":
        return FacebookSyncResult(
            "manual",
            "Manual Facebook copy is ready for posting.",
        )

    if mode in {"page", "live"}:
        return publish_post_to_page(post, config)

    return FacebookSyncResult(
        "queued",
        "Facebook publish requested, but a live integration is not configured.",
    )


def publish_post_to_page(post: dict, config: dict) -> FacebookSyncResult:
    page_id = config.get("FACEBOOK_PAGE_ID", "").strip()
    access_token = (
        config.get("FACEBOOK_PAGE_ACCESS_TOKEN")
        or config.get("FACEBOOK_ACCESS_TOKEN", "")
    ).strip()
    api_version = config.get("FACEBOOK_GRAPH_API_VERSION", "v23.0").strip() or "v23.0"

    if not page_id or not access_token:
        return FacebookSyncResult(
            "error",
            "Facebook Page ID or Page access token is missing.",
        )

    message = post.get("facebook_message", "").strip() or post.get("excerpt", "").strip()
    if not message:
        return FacebookSyncResult("error", "Facebook message text is required.")

    payload = {
        "message": message,
        "access_token": access_token,
    }

    link = build_post_link(post, config)
    if link:
        payload["link"] = link

    url = f"https://graph.facebook.com/{api_version}/{page_id}/feed"
    data = urlencode(payload).encode("utf-8")
    request = Request(url, data=data, method="POST")

    try:
        with urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = read_error_payload(exc)
        detail = (
            body.get("error", {}).get("message")
            or f"Facebook returned HTTP {exc.code}."
        )
        return FacebookSyncResult("error", detail)
    except URLError as exc:
        return FacebookSyncResult("error", f"Facebook request failed: {exc.reason}")

    post_id = body.get("id")
    if post_id:
        return FacebookSyncResult(
            "published",
            f"Published to Facebook Page post {post_id}.",
        )

    return FacebookSyncResult("error", "Facebook did not return a post ID.")


def build_post_link(post: dict, config: dict) -> str:
    site_url = config.get("SITE_URL", "").rstrip("/")
    slug = (post.get("slug") or "").strip()
    if not site_url or not slug or not post.get("is_published"):
        return ""
    return f"{site_url}/news/{slug}"


def read_error_payload(exc: HTTPError) -> dict:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except Exception:
        return {}
