#!/usr/bin/env python3
"""Prepare social content drafts for Hermes.

The helper intentionally stays dependency-light. Kie.ai calls use urllib from
the standard library. Postgres writes use psycopg/psycopg2 only when one of
those drivers is already installed; otherwise the helper writes a local JSONL
draft so the Telegram flow can still be tested before DB setup.
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
PLACEHOLDER_RE = re.compile(r"\b(lorem|todo|placeholder)\b|заглушк|рыба текста", re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

KIE_TEXT_MODEL = "gpt-image-2-text-to-image"
KIE_EDIT_MODEL = "gpt-image-2-image-to-image"
KIE_CREATE_URL = "https://api.kie.ai/api/v1/jobs/createTask"
KIE_RECORD_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"


class ContentStudioError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = CONTROL_CHARS.sub("", text)
    return SPACE_RE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_artifact_dir() -> Path:
    explicit = os.environ.get("CONTENT_STUDIO_ARTIFACT_DIR")
    if explicit:
        return Path(explicit)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "hermes" / "artifacts" / "content-studio"
    return Path.home() / ".hermes" / "artifacts" / "content-studio"


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ContentStudioError("Spec JSON must be an object")
    return data


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def response_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise ContentStudioError("API returned an empty list")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ContentStudioError("API returned a non-object response")
    code = payload.get("code")
    if code not in (None, 200, "200"):
        msg = payload.get("msg") or payload.get("message") or "unknown API error"
        raise ContentStudioError(f"API error {code}: {msg}")
    return payload


def http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "HermesContentStudio/1.0",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ContentStudioError(f"HTTP {exc.code} from {url}: {detail[:500]}") from exc
    except URLError as exc:
        raise ContentStudioError(f"Network error for {url}: {exc.reason}") from exc
    return response_payload(json.loads(raw))


def kie_token() -> str:
    token = os.environ.get("KIE_AI_API_KEY") or os.environ.get("KIE_API_KEY") or os.environ.get("KIE_TOKEN")
    if not token:
        raise ContentStudioError("Set KIE_AI_API_KEY or KIE_API_KEY before calling kie.ai")
    return token


def kie_create_task(
    prompt: str,
    aspect_ratio: str,
    resolution: str | None,
    timeout: int,
    *,
    model: str = KIE_TEXT_MODEL,
    input_urls: list[str] | None = None,
) -> str:
    input_payload: dict[str, Any] = {
        "prompt": clean_text(prompt),
        "aspect_ratio": aspect_ratio,
    }
    if resolution:
        input_payload["resolution"] = resolution
    if input_urls:
        input_payload["input_urls"] = input_urls
    payload = http_json(
        "POST",
        KIE_CREATE_URL,
        token=kie_token(),
        body={"model": model, "input": input_payload},
        timeout=timeout,
    )
    data = payload.get("data") or {}
    task_id = data.get("taskId") or data.get("recordId")
    if not task_id:
        raise ContentStudioError("kie.ai createTask response did not include taskId")
    return str(task_id)


def kie_record_info(task_id: str, timeout: int) -> dict[str, Any]:
    payload = http_json("GET", f"{KIE_RECORD_URL}?taskId={task_id}", token=kie_token(), timeout=timeout)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ContentStudioError("kie.ai recordInfo response did not include data")
    return data


def result_urls(record: dict[str, Any]) -> list[str]:
    raw = record.get("resultJson") or {}
    if isinstance(raw, str):
        raw = json.loads(raw) if raw.strip() else {}
    if not isinstance(raw, dict):
        return []
    urls = raw.get("resultUrls") or raw.get("urls") or raw.get("images") or []
    return [str(url) for url in as_list(urls) if clean_text(url)]


def safe_image_name(url: str, fallback: str = "kie_image") -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    stem = re.sub(r"[^0-9A-Za-zа-яё._-]+", "_", Path(name).stem, flags=re.IGNORECASE).strip("._-")
    ext = Path(name).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        ext = ".png"
    return f"{stem or fallback}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"


def download_image(url: str, out_dir: Path, filename: str | None = None, timeout: int = 45) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "HermesContentStudio/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read()
            content_type = response.headers.get_content_type()
    except HTTPError as exc:
        raise ContentStudioError(f"Image download failed with HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise ContentStudioError(f"Image download failed for {url}: {exc.reason}") from exc
    if not content:
        raise ContentStudioError("Downloaded image is empty")
    if filename:
        out_file = out_dir / filename
    else:
        out_file = out_dir / safe_image_name(url)
    if out_file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        guessed = mimetypes.guess_extension(content_type) or ".png"
        out_file = out_file.with_suffix(guessed)
    out_file.write_bytes(content)
    return out_file


def generate_image(
    prompt: str,
    *,
    aspect_ratio: str,
    resolution: str | None,
    poll_interval: float,
    timeout_seconds: int,
    request_timeout: int,
    download: bool,
    mock_url: str | None,
    input_urls: list[str] | None = None,
) -> dict[str, Any]:
    prompt = clean_text(prompt)
    if not prompt:
        raise ContentStudioError("Image prompt is required")
    input_urls = [clean_text(u) for u in as_list(input_urls) if clean_text(u)]
    model = KIE_EDIT_MODEL if input_urls else KIE_TEXT_MODEL
    task_id = None
    if mock_url:
        urls = [mock_url]
        state = "mock"
    else:
        task_id = kie_create_task(
            prompt,
            aspect_ratio,
            resolution,
            request_timeout,
            model=model,
            input_urls=input_urls or None,
        )
        deadline = time.monotonic() + timeout_seconds
        last_record: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_record = kie_record_info(task_id, request_timeout)
            state = clean_text(last_record.get("state")).lower()
            if state in {"success", "succeeded", "completed", "complete"}:
                urls = result_urls(last_record)
                if not urls:
                    raise ContentStudioError("kie.ai task succeeded but returned no resultUrls")
                break
            if state in {"failed", "fail", "error"}:
                fail_msg = last_record.get("failMsg") or last_record.get("message") or "generation failed"
                raise ContentStudioError(f"kie.ai task failed: {fail_msg}")
            time.sleep(poll_interval)
        else:
            raise ContentStudioError(f"Timed out waiting for kie.ai task {task_id}")

    image_path = None
    if download:
        image_path = str(download_image(urls[0], default_artifact_dir() / "images").resolve())
    return {
        "ok": True,
        "task_id": task_id,
        "state": state,
        "model": model,
        "input_urls": input_urls,
        "image_url": urls[0],
        "result_urls": urls,
        "image_path": image_path,
    }


def meta_content(markup: str, name: str) -> str:
    patterns = [
        rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, markup, re.IGNORECASE)
        if match:
            return clean_text(html.unescape(match.group(1)))
    return ""


IMAGE_URL_RE = re.compile(r"https://[^\"'\s)<>]+\.(?:jpg|jpeg|png|webp)", re.IGNORECASE)
NON_PRODUCT_IMAGE_RE = re.compile(r"favicon|logo|sprite|/icon|placeholder|avatar|emoji", re.IGNORECASE)


def page_image_urls(markup: str, og_image: str, max_images: int) -> list[str]:
    """Return likely product photos from a page, og:image first.

    Tuned for Tilda: uploaded product photos live under ``/stor`` paths on
    ``tildacdn``, while UI chrome (favicons, logos) lives under ``/tild``.
    For non-Tilda pages we fall back to og:image plus filename heuristics.
    """
    candidates: list[str] = []
    if og_image:
        candidates.append(og_image)
    candidates.extend(IMAGE_URL_RE.findall(markup))

    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        candidate = clean_text(html.unescape(raw))
        if not candidate or candidate in seen:
            continue
        low = candidate.lower()
        if "tildacdn" in low and "/stor" not in low:
            # tildacdn asset that is not an uploaded product photo (UI chrome)
            continue
        if NON_PRODUCT_IMAGE_RE.search(low):
            continue
        seen.add(candidate)
        out.append(candidate)
        if len(out) >= max_images:
            break
    return out


def extract_url(url: str, timeout: int, max_chars: int, max_images: int = 6) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 HermesContentStudio/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            raw = response.read(2_500_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise ContentStudioError(f"URL fetch failed with HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise ContentStudioError(f"URL fetch failed for {url}: {exc.reason}") from exc
    if "html" not in content_type and "text" not in content_type:
        raise ContentStudioError(f"Unsupported content type: {content_type}")
    markup = raw.decode(charset, errors="replace")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.IGNORECASE | re.DOTALL)
    title = clean_text(html.unescape(title_match.group(1))) if title_match else ""
    description = meta_content(markup, "description") or meta_content(markup, "og:description")
    og_image = meta_content(markup, "og:image")
    images = page_image_urls(markup, og_image, max_images)
    body = SCRIPT_STYLE_RE.sub(" ", markup)
    body = TAG_RE.sub(" ", body)
    text = clean_text(html.unescape(body))[:max_chars]
    source_type = "tilda" if "tilda" in urlparse(url).netloc.lower() or "tilda" in markup[:5000].lower() else "url"
    return {
        "ok": True,
        "url": url,
        "source_type": source_type,
        "title": title,
        "description": description,
        "text": text,
        "chars": len(text),
        "images": images,
    }


def normalized_versions(spec: dict[str, Any]) -> list[Any]:
    versions = spec.get("text_versions") or spec.get("versions") or spec.get("texts")
    versions = as_list(versions)
    cleaned: list[Any] = []
    for item in versions:
        if isinstance(item, dict):
            text = clean_text(item.get("text") or item.get("body") or item.get("caption"))
            if text:
                normalized = dict(item)
                normalized["text"] = text
                cleaned.append(normalized)
        else:
            text = clean_text(item)
            if text:
                cleaned.append(text)
    if len(cleaned) < 3:
        raise ContentStudioError("Draft spec must contain at least 3 text_versions")
    signatures = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in cleaned]
    if len(set(signatures[:3])) < 3:
        raise ContentStudioError("The first 3 text versions must be meaningfully different")
    if PLACEHOLDER_RE.search("\n".join(signatures)):
        raise ContentStudioError("Text versions contain placeholder content")
    return cleaned


def normalize_images(spec: dict[str, Any], image_url: str, image_path: str) -> list[dict[str, Any]]:
    """Ordered gallery for the post: card first, then any raw photos.

    Accepts spec["images"] as a list of URLs or {role,url,path} objects. Falls
    back to the single primary image_url/image_path when no gallery is given.
    """
    items: list[dict[str, Any]] = []
    for entry in as_list(spec.get("images")):
        if isinstance(entry, dict):
            url = clean_text(entry.get("url") or entry.get("image_url"))
            path = clean_text(entry.get("path") or entry.get("image_path"))
            role = clean_text(entry.get("role")) or None
            if url or path:
                obj: dict[str, Any] = {"url": url or None, "path": path or None}
                if role:
                    obj["role"] = role
                items.append(obj)
        else:
            url = clean_text(entry)
            if url:
                items.append({"url": url, "path": None})
    if not items and (image_url or image_path):
        items.append({"role": "card", "url": image_url or None, "path": image_path or None})
    return items


def _scheduled_in_future(value: str) -> bool:
    """True if scheduled_at is a parseable timestamp in the future.

    Used to auto-promote a draft to 'scheduled'. On a parse failure we assume
    scheduling intent (a time WAS given), so we return True rather than silently
    leaving the post stuck in 'draft'.
    """
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return True
    if dt.tzinfo is None:
        return dt > datetime.now()
    return dt > datetime.now(timezone.utc)


def normalize_record(spec: dict[str, Any], *, allow_missing_image: bool) -> dict[str, Any]:
    versions = normalized_versions(spec)
    topic = clean_text(spec.get("topic") or spec.get("title") or spec.get("product_name") or spec.get("subject"))
    if not topic:
        raise ContentStudioError("Draft spec must include topic/title/product_name")
    image_url = clean_text(spec.get("image_url"))
    image_path = clean_text(spec.get("image_path"))
    if not allow_missing_image and not (image_url or image_path):
        raise ContentStudioError("Draft spec must include image_url or image_path")
    source_value = clean_text(spec.get("source_value") or spec.get("source_url") or spec.get("url"))
    source_type = clean_text(spec.get("source_type"))
    if not source_type:
        source_type = "tilda" if "tilda" in source_value.lower() else ("url" if source_value else "freeform")
    selected = spec.get("selected_version") or spec.get("selected")
    selected_version = int(selected) if selected else 1
    if selected_version < 1 or selected_version > len(versions):
        raise ContentStudioError("selected_version must be a 1-based index inside text_versions")
    record = {
        "source_type": source_type,
        "source_value": source_value or None,
        "topic": topic,
        "product_name": clean_text(spec.get("product_name")) or None,
        "raw_input": spec.get("raw_input") or {},
        "status": clean_text(spec.get("status") or "draft"),
        "text_versions": versions,
        "selected_version": selected_version,
        "image_prompt": clean_text(spec.get("image_prompt")) or None,
        "image_task_id": clean_text(spec.get("image_task_id") or spec.get("task_id")) or None,
        "image_url": image_url or None,
        "image_path": image_path or None,
        "images": normalize_images(spec, image_url, image_path),
        "platforms": as_list(spec.get("platforms")),
        "scheduled_at": clean_text(spec.get("scheduled_at")) or None,
        "publication_links": spec.get("publication_links") or {},
        "notes": clean_text(spec.get("notes")) or None,
    }
    # Deterministic scheduling — fixes "post stuck in 'draft', cron never fires".
    # If the user picked target platforms AND a future send time, the intent is to
    # schedule: force status='scheduled' so the stage-6 cron publishes it, instead of
    # relying on the model to set it (which it does unreliably). Immediate posts (no
    # future time) are published directly by the postmypost skill and keep their
    # status. Explicit terminal statuses (published/failed/cancelled) are respected.
    if (
        record["platforms"]
        and record["scheduled_at"]
        and record["status"] in ("draft", "previewed", "approved")
        and _scheduled_in_future(record["scheduled_at"])
    ):
        record["status"] = "scheduled"
    return record


def db_dsn() -> str | None:
    return os.environ.get("CONTENT_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")


def db_schema() -> str:
    return os.environ.get("CONTENT_DB_SCHEMA") or "hermes_agent"


def db_table() -> str:
    return os.environ.get("CONTENT_DB_TABLE") or "content_drafts"


def psql_command() -> str | None:
    return os.environ.get("CONTENT_PSQL_COMMAND")


def sql_identifier(value: str, kind: str) -> str:
    if not IDENTIFIER_RE.match(value):
        raise ContentStudioError(f"Invalid {kind} name: {value!r}")
    return f'"{value}"'


def qualified_content_table() -> str:
    return f"{sql_identifier(db_schema(), 'schema')}.{sql_identifier(db_table(), 'table')}"


def postgres_driver() -> tuple[str, Any] | None:
    try:
        import psycopg  # type: ignore

        return ("psycopg", psycopg)
    except Exception:
        pass
    try:
        import psycopg2  # type: ignore

        return ("psycopg2", psycopg2)
    except Exception:
        return None


def save_postgres(record: dict[str, Any], dsn: str) -> dict[str, Any]:
    driver = postgres_driver()
    if not driver:
        raise ContentStudioError("Postgres DSN is set, but neither psycopg nor psycopg2 is installed")
    driver_name, module = driver
    table = qualified_content_table()
    sql = f"""
        INSERT INTO {table} (
          source_type, source_value, topic, product_name, raw_input, status,
          text_versions, selected_version, image_prompt, image_task_id,
          image_url, image_path, images, platforms, scheduled_at, publication_links, notes
        )
        VALUES (
          %s, %s, %s, %s, %s::jsonb, %s,
          %s::jsonb, %s, %s, %s,
          %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s
        )
        RETURNING id, status, created_at
    """
    params = [
        record["source_type"],
        record["source_value"],
        record["topic"],
        record["product_name"],
        json.dumps(record["raw_input"], ensure_ascii=False),
        record["status"],
        json.dumps(record["text_versions"], ensure_ascii=False),
        record["selected_version"],
        record["image_prompt"],
        record["image_task_id"],
        record["image_url"],
        record["image_path"],
        json.dumps(record["images"], ensure_ascii=False),
        json.dumps(record["platforms"], ensure_ascii=False),
        record["scheduled_at"],
        json.dumps(record["publication_links"], ensure_ascii=False),
        record["notes"],
    ]
    conn = module.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "backend": "postgres",
        "driver": driver_name,
        "schema": db_schema(),
        "table": db_table(),
        "id": row[0],
        "status": row[1],
        "created_at": str(row[2]),
        "topic": record["topic"],
    }


def sql_literal(value: Any, *, cast: str | None = None) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("'", "''")
    suffix = f"::{cast}" if cast else ""
    return f"'{text}'{suffix}"


def json_literal(value: Any) -> str:
    return sql_literal(json.dumps(value, ensure_ascii=False), cast="jsonb")


def save_psql(record: dict[str, Any], command: str) -> dict[str, Any]:
    table = qualified_content_table()
    scheduled = sql_literal(record["scheduled_at"], cast="timestamptz") if record["scheduled_at"] else "NULL"
    sql = f"""
INSERT INTO {table} (
  source_type, source_value, topic, product_name, raw_input, status,
  text_versions, selected_version, image_prompt, image_task_id,
  image_url, image_path, images, platforms, scheduled_at, publication_links, notes
)
VALUES (
  {sql_literal(record["source_type"])},
  {sql_literal(record["source_value"])},
  {sql_literal(record["topic"])},
  {sql_literal(record["product_name"])},
  {json_literal(record["raw_input"])},
  {sql_literal(record["status"])},
  {json_literal(record["text_versions"])},
  {int(record["selected_version"])},
  {sql_literal(record["image_prompt"])},
  {sql_literal(record["image_task_id"])},
  {sql_literal(record["image_url"])},
  {sql_literal(record["image_path"])},
  {json_literal(record["images"])},
  {json_literal(record["platforms"])},
  {scheduled},
  {json_literal(record["publication_links"])},
  {sql_literal(record["notes"])}
)
RETURNING id, status, created_at;
"""
    args = shlex.split(command)
    if not args:
        raise ContentStudioError("CONTENT_PSQL_COMMAND is empty")
    proc = subprocess.run(args, input=sql, text=True, encoding="utf-8", capture_output=True, timeout=30)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ContentStudioError(f"psql save failed: {detail[:800]}")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    row_lines = [line for line in lines if "|" in line]
    if not row_lines:
        raise ContentStudioError("psql save returned no row")
    row = row_lines[-1].split("|")
    if len(row) < 3:
        raise ContentStudioError(f"Unexpected psql output: {lines[-1]}")
    return {
        "ok": True,
        "backend": "postgres",
        "driver": "psql",
        "schema": db_schema(),
        "table": db_table(),
        "id": int(row[0]),
        "status": row[1],
        "created_at": row[2],
        "topic": record["topic"],
    }


def save_jsonl(record: dict[str, Any]) -> dict[str, Any]:
    out_dir = default_artifact_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    local_id = f"local-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    payload = {"id": local_id, "created_at": now_iso(), **record}
    path = out_dir / "content_drafts.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {
        "ok": True,
        "backend": "jsonl-fallback",
        "id": local_id,
        "path": str(path.resolve()),
        "topic": record["topic"],
        "warning": "Postgres was not used. Set CONTENT_DATABASE_URL with psycopg/psycopg2, or CONTENT_PSQL_COMMAND for psql-based writes.",
    }


def save_draft(spec: dict[str, Any], *, require_postgres: bool, allow_missing_image: bool) -> dict[str, Any]:
    record = normalize_record(spec, allow_missing_image=allow_missing_image)
    dsn = db_dsn()
    if dsn:
        return save_postgres(record, dsn)
    command = psql_command()
    if command:
        return save_psql(record, command)
    if require_postgres:
        raise ContentStudioError("CONTENT_DATABASE_URL/DATABASE_URL or CONTENT_PSQL_COMMAND is required for Postgres save")
    return save_jsonl(record)


# --- Update an existing draft in place (publish write-back) ----------------
#
# The publish step (postmypost skill) records its outcome by UPDATEing the same
# content_drafts row — status, scheduled_at, platforms, publication_links — instead
# of inserting a new row. This keeps one row per post and fills scheduled_at, which
# stage 6 (cron) relies on. When appending publication links, the merge is array-safe
# even if the column still holds the default empty object.

APPEND_LINKS_SQL = (
    "(CASE WHEN jsonb_typeof(publication_links) = 'array' "
    "THEN publication_links ELSE '[]'::jsonb END)"
)


def update_psql(draft_id: int, fields: dict[str, Any], command: str, *, append_links: bool) -> dict[str, Any]:
    parts: list[str] = []
    if "status" in fields:
        parts.append(f"status = {sql_literal(fields['status'])}")
    if "scheduled_at" in fields:
        sched = fields["scheduled_at"]
        parts.append(f"scheduled_at = {sql_literal(sched, cast='timestamptz') if sched else 'NULL'}")
    if "platforms" in fields:
        parts.append(f"platforms = {json_literal(fields['platforms'])}")
    if "publication_links" in fields:
        links = json_literal(fields["publication_links"])
        parts.append(f"publication_links = {APPEND_LINKS_SQL} || {links}" if append_links
                     else f"publication_links = {links}")
    if "notes" in fields:
        parts.append(f"notes = {sql_literal(fields['notes'])}")
    if not parts:
        raise ContentStudioError("update-draft: nothing to update (pass at least one field)")
    table = qualified_content_table()
    sql = (
        f"UPDATE {table} SET {', '.join(parts)} WHERE id = {int(draft_id)} "
        "RETURNING id, status, scheduled_at, publication_links;"
    )
    args = shlex.split(command)
    if not args:
        raise ContentStudioError("CONTENT_PSQL_COMMAND is empty")
    proc = subprocess.run(args, input=sql, text=True, encoding="utf-8", capture_output=True, timeout=30)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ContentStudioError(f"psql update failed: {detail[:800]}")
    row_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip() and "|" in line]
    if not row_lines:
        raise ContentStudioError(f"update-draft: no draft with id {draft_id}")
    row = row_lines[-1].split("|")
    return {
        "ok": True, "backend": "postgres", "driver": "psql",
        "schema": db_schema(), "table": db_table(),
        "id": int(row[0]), "status": row[1], "scheduled_at": row[2] or None,
        "updated_fields": sorted(fields.keys()),
    }


def update_postgres(draft_id: int, fields: dict[str, Any], dsn: str, *, append_links: bool) -> dict[str, Any]:
    driver = postgres_driver()
    if not driver:
        raise ContentStudioError("Postgres DSN is set, but neither psycopg nor psycopg2 is installed")
    driver_name, module = driver
    parts: list[str] = []
    params: list[Any] = []
    if "status" in fields:
        parts.append("status = %s"); params.append(fields["status"])
    if "scheduled_at" in fields:
        parts.append("scheduled_at = %s"); params.append(fields["scheduled_at"] or None)
    if "platforms" in fields:
        parts.append("platforms = %s::jsonb"); params.append(json.dumps(fields["platforms"], ensure_ascii=False))
    if "publication_links" in fields:
        parts.append(f"publication_links = {APPEND_LINKS_SQL} || %s::jsonb" if append_links
                     else "publication_links = %s::jsonb")
        params.append(json.dumps(fields["publication_links"], ensure_ascii=False))
    if "notes" in fields:
        parts.append("notes = %s"); params.append(fields["notes"])
    if not parts:
        raise ContentStudioError("update-draft: nothing to update (pass at least one field)")
    table = qualified_content_table()
    sql = f"UPDATE {table} SET {', '.join(parts)} WHERE id = %s RETURNING id, status, scheduled_at"
    params.append(int(draft_id))
    conn = module.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row:
        raise ContentStudioError(f"update-draft: no draft with id {draft_id}")
    return {
        "ok": True, "backend": "postgres", "driver": driver_name,
        "schema": db_schema(), "table": db_table(),
        "id": row[0], "status": row[1], "scheduled_at": str(row[2]) if row[2] else None,
        "updated_fields": sorted(fields.keys()),
    }


def update_draft(draft_id: int, fields: dict[str, Any], *, append_links: bool) -> dict[str, Any]:
    dsn = db_dsn()
    if dsn:
        return update_postgres(draft_id, fields, dsn, append_links=append_links)
    command = psql_command()
    if command:
        return update_psql(draft_id, fields, command, append_links=append_links)
    raise ContentStudioError("update-draft needs Postgres (set CONTENT_DATABASE_URL or CONTENT_PSQL_COMMAND)")


def default_image_prompt(spec: dict[str, Any]) -> str:
    topic = clean_text(spec.get("topic") or spec.get("title") or spec.get("product_name"))
    if not topic:
        raise ContentStudioError("image_prompt is missing and topic/title is empty")
    # YouTube-thumbnail-style cover: large readable Russian headline, legible at 160px wide.
    return (
        f'создать обложку для - "{topic}". стиль ютуб обложка. текста на обложке русские. '
        f'надо учесть что обложка будет отображаться маленькой, поэтому сделай крупный читаемый '
        f'текст, 2–4 слова максимум, без мелких деталей. Главный текст должен читаться даже при '
        f'уменьшении до 160 px по ширине.'
    )[:20000]


def cmd_extract_url(args: argparse.Namespace) -> int:
    print_json(extract_url(args.url, args.timeout, args.max_chars, args.max_images))
    return 0


def cmd_generate_image(args: argparse.Namespace) -> int:
    print_json(
        generate_image(
            args.prompt,
            aspect_ratio=args.aspect_ratio,
            resolution=args.resolution,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout_seconds,
            request_timeout=args.request_timeout,
            download=not args.no_download,
            mock_url=args.mock_url,
            input_urls=args.input_urls,
        )
    )
    return 0


def cmd_save_draft(args: argparse.Namespace) -> int:
    spec = load_json(args.spec)
    print_json(
        save_draft(
            spec,
            require_postgres=args.require_postgres,
            allow_missing_image=args.allow_missing_image,
        )
    )
    return 0


def cmd_update_draft(args: argparse.Namespace) -> int:
    fields: dict[str, Any] = {}
    if args.status is not None:
        fields["status"] = args.status
    if args.scheduled_at is not None:
        fields["scheduled_at"] = args.scheduled_at or None
    if args.platforms is not None:
        fields["platforms"] = json.loads(args.platforms)
    if args.publication_links is not None:
        links = json.loads(args.publication_links)
        if args.append_links and isinstance(links, dict):
            links = [links]
        fields["publication_links"] = links
    if args.notes is not None:
        fields["notes"] = args.notes
    print_json(update_draft(args.id, fields, append_links=args.append_links))
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    spec = load_json(args.spec)
    image_result = None
    input_urls = args.input_urls or as_list(spec.get("input_urls"))
    if args.generate_image or args.mock_url or input_urls:
        prompt = clean_text(spec.get("image_prompt")) or default_image_prompt(spec)
        image_result = generate_image(
            prompt,
            aspect_ratio=args.aspect_ratio,
            resolution=args.resolution,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout_seconds,
            request_timeout=args.request_timeout,
            download=not args.no_download,
            mock_url=args.mock_url,
            input_urls=input_urls,
        )
        spec["image_prompt"] = prompt
        spec["image_url"] = image_result["image_url"]
        spec["image_path"] = image_result["image_path"]
        spec["image_task_id"] = image_result["task_id"]
    draft_result = None
    if args.save:
        draft_result = save_draft(
            spec,
            require_postgres=args.require_postgres,
            allow_missing_image=args.allow_missing_image,
        )
    print_json({"ok": True, "image": image_result, "draft": draft_result, "spec": spec})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes content studio helper")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract-url", help="Fetch an HTML/Tilda page and extract usable text + product images")
    extract.add_argument("url")
    extract.add_argument("--timeout", type=int, default=25)
    extract.add_argument("--max-chars", type=int, default=8000)
    extract.add_argument("--max-images", type=int, default=6, help="Max product image URLs to return (og:image first)")
    extract.set_defaults(func=cmd_extract_url)

    image = sub.add_parser("generate-image", help="Generate (text-to-image) or edit (image-to-image) through kie.ai")
    image.add_argument("--prompt", required=True)
    image.add_argument("--aspect-ratio", default="1:1")
    image.add_argument("--resolution", choices=["1K", "2K", "4K"], default=None)
    image.add_argument("--poll-interval", type=float, default=5.0)
    image.add_argument("--timeout-seconds", type=int, default=240)
    image.add_argument("--request-timeout", type=int, default=45)
    image.add_argument("--no-download", action="store_true")
    image.add_argument(
        "--input-url",
        dest="input_urls",
        action="append",
        help="Source image URL for image-to-image (background fill/crop). Repeat for several. "
        "When set, uses gpt-image-2-image-to-image.",
    )
    image.add_argument("--mock-url", help="Testing only: skip kie.ai and use this URL")
    image.set_defaults(func=cmd_generate_image)

    save = sub.add_parser("save-draft", help="Save an already prepared draft")
    save.add_argument("--spec", required=True)
    save.add_argument("--require-postgres", action="store_true")
    save.add_argument("--allow-missing-image", action="store_true")
    save.set_defaults(func=cmd_save_draft)

    upd = sub.add_parser("update-draft", help="Update an existing draft in place (publish write-back)")
    upd.add_argument("--id", type=int, required=True)
    upd.add_argument("--status", help="draft/previewed/approved/scheduled/published/failed/cancelled")
    upd.add_argument("--scheduled-at", dest="scheduled_at",
                     help="ISO timestamptz in МСК (+03:00); pass empty string to clear")
    upd.add_argument("--platforms", help="JSON array of platform objects to set (with account_id)")
    upd.add_argument("--publication-links", dest="publication_links",
                     help="JSON (array or object) of publication results to record")
    upd.add_argument("--append-links", action="store_true",
                     help="append to the existing publication_links array instead of replacing")
    upd.add_argument("--notes")
    upd.set_defaults(func=cmd_update_draft)

    prepare = sub.add_parser("prepare", help="Optionally generate image and save the content draft")
    prepare.add_argument("--spec", required=True)
    prepare.add_argument("--generate-image", action="store_true")
    prepare.add_argument(
        "--input-url",
        dest="input_urls",
        action="append",
        help="Source image URL(s) for image-to-image. May also be set as spec.input_urls.",
    )
    prepare.add_argument("--mock-url", help="Testing only: skip kie.ai and use this URL")
    prepare.add_argument("--save", action="store_true")
    prepare.add_argument("--require-postgres", action="store_true")
    prepare.add_argument("--allow-missing-image", action="store_true")
    prepare.add_argument("--aspect-ratio", default="1:1")
    prepare.add_argument("--resolution", choices=["1K", "2K", "4K"], default=None)
    prepare.add_argument("--poll-interval", type=float, default=5.0)
    prepare.add_argument("--timeout-seconds", type=int, default=240)
    prepare.add_argument("--request-timeout", type=int, default=45)
    prepare.add_argument("--no-download", action="store_true")
    prepare.set_defaults(func=cmd_prepare)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
