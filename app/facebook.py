from __future__ import annotations

from dataclasses import dataclass


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

    return FacebookSyncResult(
        "queued",
        "Facebook publish requested, but a live integration is not configured.",
    )
