#!/usr/bin/env python3
"""S3 upload helper for Hermes (stage 9).

Uploads an image (local file OR remote URL) to an S3-compatible bucket and
returns a public URL. Used so a user's own photo, sent to the agent, gets a
stable public link (e.g. to reuse in a social post as a `role:"raw"` image).

Dependency-light: stdlib only (urllib + hashlib/hmac). Implements AWS
Signature v4 for a single PUT object request — works with Beget Cloud Storage
and any other S3-compatible provider. No boto3 required.

Credentials come from the environment (see s3.txt / .env):
  S3_ENDPOINT          e.g. https://s3.ru1.storage.beget.cloud
  S3_REGION            e.g. ru-central-1
  S3_BUCKET            bucket name
  S3_ACCESS_KEY        access key id
  S3_SECRET_KEY        secret access key
  S3_PUBLIC_BASE_URL   optional; base for the returned link
                       (default: S3_ENDPOINT). Public URL is
                       {public_base}/{bucket}/{key} (path-style).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


class S3Error(RuntimeError):
    pass


# ── config from env ──────────────────────────────────────────────────────

def _env(name: str, *, required: bool = True, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise S3Error(f"Set {name} before using S3 upload (see s3.txt / .env)")
    return value or ""


def endpoint() -> str:
    return _env("S3_ENDPOINT").rstrip("/")


def region() -> str:
    return _env("S3_REGION", required=False, default="us-east-1")


def bucket() -> str:
    return _env("S3_BUCKET")


def public_base() -> str:
    return (os.environ.get("S3_PUBLIC_BASE_URL") or endpoint()).rstrip("/")


# ── signature v4 ─────────────────────────────────────────────────────────

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, datestamp: str, reg: str, service: str) -> bytes:
    k_date = _hmac(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _hmac(k_date, reg)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def _encode_key(key: str) -> str:
    # URI-encode each path segment, keep the slashes.
    return "/".join(quote(seg, safe="") for seg in key.split("/"))


def put_object(key: str, body: bytes, content_type: str, *, acl: str | None) -> str:
    """PUT an object with SigV4 and return its public URL."""
    access_key = _env("S3_ACCESS_KEY")
    secret_key = _env("S3_SECRET_KEY")
    service = "s3"
    reg = region()

    host = urlsplit(endpoint()).netloc
    canonical_uri = f"/{bucket()}/{_encode_key(key)}"
    put_url = f"{endpoint()}/{bucket()}/{_encode_key(key)}"

    now = dt.datetime.now(dt.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = _sha256_hex(body)

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amzdate,
    }
    if content_type:
        headers["content-type"] = content_type
    if acl:
        headers["x-amz-acl"] = acl

    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )

    scope = f"{datestamp}/{reg}/{service}/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amzdate, scope, _sha256_hex(canonical_request.encode("utf-8"))]
    )
    signature = hmac.new(
        _signing_key(secret_key, datestamp, reg, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    req_headers = dict(headers)
    req_headers["Authorization"] = authorization
    # urllib lowercases nothing but sends what we give; content-length auto-set.
    request = Request(put_url, data=body, method="PUT", headers=req_headers)
    try:
        with urlopen(request, timeout=60) as resp:
            resp.read()
            status = resp.status
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise S3Error(f"S3 PUT failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise S3Error(f"S3 PUT connection error: {exc.reason}") from exc

    if status not in (200, 201):
        raise S3Error(f"S3 PUT unexpected status {status}")
    return f"{public_base()}/{bucket()}/{_encode_key(key)}"


# ── source loading ───────────────────────────────────────────────────────

def _load_source(file_path: str | None, url: str | None) -> tuple[bytes, str, str]:
    """Return (body, ext, content_type) from a local file or a remote URL."""
    if file_path:
        with open(file_path, "rb") as fh:
            body = fh.read()
        ext = os.path.splitext(file_path)[1].lstrip(".").lower()
        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        return body, ext, ctype
    if url:
        try:
            with urlopen(Request(url, headers={"User-Agent": "hermes-s3/1.0"}), timeout=60) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()
        except (HTTPError, URLError) as exc:
            raise S3Error(f"Could not download source URL: {exc}") from exc
        ext = os.path.splitext(urlsplit(url).path)[1].lstrip(".").lower()
        if not ext and ctype:
            ext = (mimetypes.guess_extension(ctype) or "").lstrip(".")
        if not ctype:
            ctype = mimetypes.guess_type("x." + ext)[0] if ext else "application/octet-stream"
        return body, ext, ctype or "application/octet-stream"
    raise S3Error("Provide --file or --url")


def _make_key(prefix: str, ext: str, explicit: str | None) -> str:
    if explicit:
        return explicit.lstrip("/")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f".{ext}" if ext else ""
    return f"{prefix.strip('/')}/{stamp}_{uuid.uuid4().hex[:8]}{suffix}"


# ── CLI ──────────────────────────────────────────────────────────────────

def cmd_upload(args: argparse.Namespace) -> None:
    body, ext, ctype = _load_source(args.file, args.url)
    if args.content_type:
        ctype = args.content_type
    key = _make_key(args.prefix, ext, args.key)
    acl = None if args.no_acl else args.acl
    url = put_object(key, body, ctype, acl=acl)
    print(json.dumps(
        {"success": True, "url": url, "key": key, "bytes": len(body), "content_type": ctype},
        ensure_ascii=False, indent=2,
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload an image to S3 and return a public URL.")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("upload", help="Upload a local file or a remote URL to S3.")
    src = up.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Local file path to upload.")
    src.add_argument("--url", help="Remote image URL to fetch and upload.")
    up.add_argument("--key", help="Explicit object key. Default: <prefix>/<date>_<uuid>.<ext>.")
    up.add_argument("--prefix", default="user-uploads", help="Key prefix (default: user-uploads).")
    up.add_argument("--content-type", help="Override content type (else guessed).")
    up.add_argument("--acl", default="public-read", help="Object ACL (default: public-read).")
    up.add_argument("--no-acl", action="store_true", help="Do not send an ACL header (bucket already public).")
    up.set_defaults(func=cmd_upload)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except S3Error as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
