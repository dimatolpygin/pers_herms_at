#!/usr/bin/env python3
"""PostMyPost publishing helper for Hermes (stage 5).

Dependency-light (stdlib urllib only). Implements the PostMyPost 3-step flow:
  1. POST /upload/init  {project_id, url}     -> upload id
  2. GET  /upload/status?id=...               -> file_id (status 1 = success)
  3. POST /publications  {..., details:[...]} -> publication

Safety: publications are created as DRAFT (publication_status=4) by default.
Actually queuing a post to real social networks (publication_status=5) requires
the explicit --confirm-publish flag, so a live post can never happen by accident.

Credentials come from the environment:
  POSTMYPOST_TOKEN        Bearer token
  POSTMYPOST_PROJECT_ID   project id (overridable per call with --project-id)
  POSTMYPOST_BASE_URL     optional, default https://api.postmypost.io/v4.1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.postmypost.io/v4.1"

# PostMyPost publication_status enum.
STATUS_DRAFT = 4                # saved as draft, NOT published
STATUS_PENDING_PUBLICATION = 5  # queued to actually publish
CREATE_STATUSES = {4, 5, 10, 11, 12}

# publication_type enum.
TYPE_POST = 1
TYPE_STORY = 2
TYPE_REELS = 4

# chanel_id -> human network name (for readable account listings).
CHANNEL_NAMES = {1: "instagram", 2: "vk", 6: "telegram", 7: "pinterest"}

# upload/status enum.
UPLOAD_OK = 1
UPLOAD_ERROR = 2


class PostMyPostError(RuntimeError):
    pass


def base_url() -> str:
    return (os.environ.get("POSTMYPOST_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def token() -> str:
    value = os.environ.get("POSTMYPOST_TOKEN")
    if not value:
        raise PostMyPostError("Set POSTMYPOST_TOKEN before calling PostMyPost")
    return value


def project_id(explicit: int | None = None) -> int:
    if explicit:
        return int(explicit)
    value = os.environ.get("POSTMYPOST_PROJECT_ID")
    if not value:
        raise PostMyPostError("Set POSTMYPOST_PROJECT_ID or pass --project-id")
    return int(value)


def now_iso(tz_hours: int = 3) -> str:
    tz = timezone(timedelta(hours=tz_hours))
    return datetime.now(tz).replace(microsecond=0).isoformat()


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def http_json(method: str, path: str, *, body: dict | None = None, timeout: int = 30) -> Any:
    url = f"{base_url()}{path}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token()}",
        "User-Agent": "HermesPostMyPost/1.0",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PostMyPostError(f"HTTP {exc.code} from {method} {path}: {detail[:600]}") from exc
    except URLError as exc:
        raise PostMyPostError(f"Network error for {method} {path}: {exc.reason}") from exc
    if not raw.strip():
        return None
    return json.loads(raw)


def first_record(payload: Any, context: str) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise PostMyPostError(f"{context}: API returned an empty list")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise PostMyPostError(f"{context}: unexpected API response {payload!r}")
    return payload


# --- Accounts -------------------------------------------------------------

def get_accounts(pid: int, timeout: int) -> list[dict[str, Any]]:
    payload = http_json("GET", f"/accounts?project_id={pid}", timeout=timeout)
    accounts = payload if isinstance(payload, list) else payload.get("data") or []
    result = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        result.append(
            {
                "id": acc.get("id"),
                "network": CHANNEL_NAMES.get(acc.get("chanel_id"), f"chanel_{acc.get('chanel_id')}"),
                "chanel_id": acc.get("chanel_id"),
                "name": acc.get("name"),
                "login": acc.get("login"),
                "connection_status": acc.get("connection_status"),
            }
        )
    return result


# --- Upload ---------------------------------------------------------------

def upload_image(url: str, pid: int, *, poll_interval: float, timeout_seconds: int, request_timeout: int) -> dict[str, Any]:
    init = first_record(
        http_json("POST", "/upload/init", body={"project_id": pid, "url": url}, timeout=request_timeout),
        "upload/init",
    )
    upload_id = init.get("id")
    if not upload_id:
        raise PostMyPostError(f"upload/init returned no id: {init!r}")

    deadline = time.monotonic() + timeout_seconds
    last = init
    while time.monotonic() < deadline:
        last = first_record(
            http_json("GET", f"/upload/status?id={upload_id}", timeout=request_timeout),
            "upload/status",
        )
        status = last.get("status")
        if status == UPLOAD_OK and last.get("file_id"):
            return {"ok": True, "upload_id": upload_id, "file_id": last["file_id"], "url": url}
        if status == UPLOAD_ERROR:
            raise PostMyPostError(f"upload failed for {url}: {last!r}")
        time.sleep(poll_interval)
    raise PostMyPostError(f"Timed out waiting for upload {upload_id} ({url}); last status {last.get('status')}")


# --- Publications ---------------------------------------------------------

def create_publication(
    pid: int,
    *,
    account_ids: list[int],
    content: str | None,
    title: str | None,
    link: str | None,
    file_ids: list[int],
    post_at: str,
    status: int,
    publication_type: int,
    request_timeout: int,
) -> dict[str, Any]:
    if not account_ids:
        raise PostMyPostError("account_ids is required (where to publish)")
    if status not in CREATE_STATUSES:
        raise PostMyPostError(f"publication_status must be one of {sorted(CREATE_STATUSES)} (4=draft, 5=publish)")
    detail: dict[str, Any] = {"publication_type": publication_type}
    if file_ids:
        detail["file_ids"] = file_ids
    if content:
        detail["content"] = content
    if title:
        detail["title"] = title
    if link:
        detail["link"] = link
    body = {
        "project_id": pid,
        "post_at": post_at,
        "account_ids": account_ids,
        "publication_status": status,
        "details": [detail],
    }
    payload = http_json("POST", "/publications", body=body, timeout=request_timeout)
    record = first_record(payload, "publications")
    return {
        "ok": True,
        "id": record.get("id"),
        "publication_status": record.get("publication_status"),
        "post_at": record.get("post_at"),
        "account_ids": record.get("account_ids"),
        "is_draft": record.get("publication_status") == STATUS_DRAFT,
    }


def delete_publication(pid: int, publication_id: int, account_ids: list[int], delete_option: int, timeout: int) -> dict[str, Any]:
    if not account_ids:
        raise PostMyPostError("account_ids is required to delete a publication")
    # PostMyPost expects flat query params (Yii): delete_option + account_ids + project_id.
    params = [("delete_option", delete_option), ("project_id", pid)]
    params.extend(("account_ids", aid) for aid in account_ids)
    query = urlencode(params)
    try:
        http_json("DELETE", f"/publications/{publication_id}?{query}", timeout=timeout)
    except PostMyPostError as exc:
        # PMP returns a response-validation quirk (422 on publication_status) even
        # when the delete succeeded; treat that specific case as success.
        if "Response validation error" not in str(exc):
            raise
    return {"ok": True, "deleted": publication_id}


# --- CLI ------------------------------------------------------------------

def cmd_accounts(args: argparse.Namespace) -> int:
    print_json(get_accounts(project_id(args.project_id), args.request_timeout))
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    print_json(
        upload_image(
            args.url,
            project_id(args.project_id),
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout_seconds,
            request_timeout=args.request_timeout,
        )
    )
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    pid = project_id(args.project_id)
    status = args.status
    if status == STATUS_PENDING_PUBLICATION and not args.confirm_publish:
        raise PostMyPostError(
            "Refusing to queue a LIVE publication (status 5) without --confirm-publish. "
            "Use the default status 4 (draft) for tests."
        )

    file_ids = list(args.file_ids or [])
    for url in args.image_urls or []:
        uploaded = upload_image(
            url,
            pid,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout_seconds,
            request_timeout=args.request_timeout,
        )
        file_ids.append(uploaded["file_id"])

    result = create_publication(
        pid,
        account_ids=args.account_ids,
        content=args.content,
        title=args.title,
        link=args.link,
        file_ids=file_ids,
        post_at=args.post_at or now_iso(),
        status=status,
        publication_type=args.type,
        request_timeout=args.request_timeout,
    )
    result["uploaded_file_ids"] = file_ids
    print_json(result)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    print_json(
        delete_publication(
            project_id(args.project_id),
            args.publication_id,
            args.account_ids,
            args.delete_option,
            args.request_timeout,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes PostMyPost publishing helper")
    parser.add_argument("--project-id", type=int, default=None, help="override POSTMYPOST_PROJECT_ID")
    parser.add_argument("--request-timeout", type=int, default=30)
    sub = parser.add_subparsers(dest="command", required=True)

    acc = sub.add_parser("accounts", help="List connected social accounts and their ids")
    acc.set_defaults(func=cmd_accounts)

    up = sub.add_parser("upload", help="Upload an image by URL and return its file_id")
    up.add_argument("--url", required=True)
    up.add_argument("--poll-interval", type=float, default=3.0)
    up.add_argument("--timeout-seconds", type=int, default=120)
    up.set_defaults(func=cmd_upload)

    pub = sub.add_parser("publish", help="Create a publication (draft by default)")
    pub.add_argument("--account-id", dest="account_ids", action="append", type=int, required=True,
                     help="target account id (repeat for several)")
    pub.add_argument("--content", help="post text")
    pub.add_argument("--title", help="post title")
    pub.add_argument("--link", help="external link")
    pub.add_argument("--image-url", dest="image_urls", action="append",
                     help="image URL to upload and attach (repeat for several)")
    pub.add_argument("--file-id", dest="file_ids", action="append", type=int,
                     help="already-uploaded file id (repeat for several)")
    pub.add_argument("--post-at", help="ISO datetime, default now (+03:00)")
    pub.add_argument("--status", type=int, default=STATUS_DRAFT,
                     help="publication_status: 4=draft (default), 5=publish (needs --confirm-publish)")
    pub.add_argument("--type", type=int, default=TYPE_POST, help="publication_type: 1=post, 2=story, 4=reels")
    pub.add_argument("--confirm-publish", action="store_true",
                     help="required to actually queue a LIVE post (status 5)")
    pub.add_argument("--poll-interval", type=float, default=3.0)
    pub.add_argument("--timeout-seconds", type=int, default=120)
    pub.set_defaults(func=cmd_publish)

    dele = sub.add_parser("delete", help="Delete/cancel a publication (also removes drafts)")
    dele.add_argument("--publication-id", type=int, required=True)
    dele.add_argument("--account-id", dest="account_ids", action="append", type=int, required=True,
                      help="account id(s) the publication targets (repeat for several)")
    dele.add_argument("--delete-option", type=int, default=1,
                      help="1 = remove from PostMyPost (drafts/scheduled); other options remove from the network too")
    dele.set_defaults(func=cmd_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
