#!/usr/bin/env python3
"""Hermes autoposter — stage 6 cron обвязка (NO LLM).

Publishes scheduled content drafts through PostMyPost when their time comes,
driven purely by the database + the PostMyPost API. No model is ever called.

Flow (one pass, meant to be run every minute by cron):
  1. SELECT drafts with status='scheduled' AND scheduled_at <= now().
  2. Atomically CLAIM each (UPDATE ... status='publishing' WHERE status='scheduled'
     RETURNING ...). The WHERE guard makes a second concurrent/repeat run a no-op,
     so the same post is never published twice (idempotency).
  3. Upload the draft's images once (card first) and create one PostMyPost
     publication per target account (publication_status=5 = live). Pinterest
     accounts also get a title + link.
  4. Write the outcome back with content_studio.update_draft: status
     'published' (all ok) or 'failed' (with notes), appending publication_links.

Reused code (imported, not re-implemented):
  - <skills>/social-publishing/postmypost/scripts/postmypost.py  (upload/create)
  - <skills>/marketing/content-studio/scripts/content_studio.py  (DB + update-draft)

Environment:
  POSTMYPOST_TOKEN, POSTMYPOST_PROJECT_ID   PostMyPost credentials (helpers read them)
  CONTENT_PSQL_COMMAND or CONTENT_DATABASE_URL   how content_studio reaches Postgres
  POSTMYPOST_HELPER / CONTENT_HELPER         optional explicit paths to the two helpers
  HERMES_SKILLS_DIR                          optional skills root (default: repo, then %LOCALAPPDATA%/hermes/skills)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any


# When stdout is redirected to a file on Russian Windows, Python encodes it as
# cp1251, which has no "→"/emoji — a log line with those characters would crash
# the whole run (mid-publish, leaving a draft stuck in 'publishing'). Force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


def log(msg: str) -> None:
    ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_env_file() -> None:
    """Load KEY=VALUE lines from the Hermes .env so cron/Task Scheduler need no
    manual exports. Existing environment values win (never overwritten)."""
    candidates = []
    explicit = os.environ.get("HERMES_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / ".env")
    candidates.append(Path(__file__).resolve().parents[1] / ".env")
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


# --- Locate and import the two helpers ------------------------------------

def _candidate_skill_dirs() -> list[Path]:
    dirs: list[Path] = []
    env_dir = os.environ.get("HERMES_SKILLS_DIR")
    if env_dir:
        dirs.append(Path(env_dir))
    # Repo copy (this file lives at <repo>/cron/autopost.py).
    dirs.append(Path(__file__).resolve().parents[1] / "hermes-skills")
    # Installed copy.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "hermes" / "skills")
    dirs.append(Path.home() / "hermes" / "skills")
    return dirs


def _find_helper(env_var: str, *relative: str) -> Path:
    explicit = os.environ.get(env_var)
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise FileNotFoundError(f"{env_var}={explicit} does not point to a file")
    for base in _candidate_skill_dirs():
        p = base.joinpath(*relative)
        if p.is_file():
            return p
    raise FileNotFoundError(f"Could not locate helper {relative[-1]}; set {env_var} or HERMES_SKILLS_DIR")


def _import_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PMP = _import_module(
    "pmp_helper",
    _find_helper("POSTMYPOST_HELPER", "social-publishing", "postmypost", "scripts", "postmypost.py"),
)
CS = _import_module(
    "cs_helper",
    _find_helper("CONTENT_HELPER", "marketing", "content-studio", "scripts", "content_studio.py"),
)


# --- Database access (reuses content_studio's connection config) -----------

def run_psql_query(sql: str) -> list[str]:
    """Run SQL through CONTENT_PSQL_COMMAND (psql -At) and return non-empty rows."""
    command = CS.psql_command()
    if not command:
        raise RuntimeError(
            "autopost needs CONTENT_PSQL_COMMAND (psql) configured; DSN-only mode not supported here"
        )
    args = shlex.split(command)
    proc = subprocess.run(args, input=sql, text=True, encoding="utf-8", capture_output=True, timeout=30)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"psql failed: {detail[:800]}")
    # psql prints a command-status tag (e.g. "UPDATE 1") after data-modifying
    # statements even in -At mode; drop those so only real rows remain.
    tag = re.compile(r"^(INSERT|UPDATE|DELETE|MERGE|SELECT|COPY)\b")
    return [line.strip() for line in proc.stdout.splitlines()
            if line.strip() and not tag.match(line.strip())]


def due_draft_ids(limit: int) -> list[int]:
    table = CS.qualified_content_table()
    sql = (
        f"SELECT id FROM {table} "
        "WHERE status = 'scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= now() "
        f"ORDER BY scheduled_at ASC LIMIT {int(limit)};"
    )
    return [int(x) for x in run_psql_query(sql)]


def claim_draft(draft_id: int) -> dict[str, Any] | None:
    """Atomically move scheduled -> publishing. Returns the row, or None if already taken."""
    table = CS.qualified_content_table()
    payload = (
        "json_build_object("
        "'id', id, 'text_versions', text_versions, 'selected_version', selected_version, "
        "'product_name', product_name, 'topic', topic, 'source_type', source_type, "
        "'source_value', source_value, 'images', images, 'platforms', platforms, "
        "'scheduled_at', scheduled_at)::text"
    )
    sql = (
        f"UPDATE {table} SET status = 'publishing', updated_at = now() "
        f"WHERE id = {int(draft_id)} AND status = 'scheduled' RETURNING {payload};"
    )
    rows = run_psql_query(sql)
    if not rows:
        return None
    return json.loads(rows[-1])


# --- Turning a draft row into a publication --------------------------------

def selected_text(row: dict[str, Any]) -> str:
    versions = row.get("text_versions") or []
    idx = int(row.get("selected_version") or 1) - 1
    if 0 <= idx < len(versions):
        entry = versions[idx]
        if isinstance(entry, dict):
            return str(entry.get("text") or "").strip()
        return str(entry).strip()
    return ""


def image_urls(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for img in row.get("images") or []:
        if isinstance(img, dict) and img.get("url"):
            urls.append(str(img["url"]))
    return urls


def external_link(row: dict[str, Any]) -> str | None:
    value = row.get("source_value")
    if row.get("source_type") == "url" and value:
        return str(value)
    if isinstance(value, str) and value.startswith("http"):
        return str(value)
    return None


def publish_draft(row: dict[str, Any], pid: int, *, dry_run: bool) -> dict[str, Any]:
    text = selected_text(row)
    title = (row.get("product_name") or row.get("topic") or "").strip() or None
    link = external_link(row)
    platforms = row.get("platforms") or []
    if not platforms:
        raise RuntimeError("draft has no platforms[] with account_id")

    urls = image_urls(row)
    if dry_run:
        log(f"  [dry-run] would upload {len(urls)} image(s) and post to "
            f"{[p.get('account_id') for p in platforms]}")
        return {"status": "dry-run", "links": []}

    # Upload images once; file_ids are project-scoped and reused for every account.
    file_ids: list[int] = []
    for url in urls:
        uploaded = PMP.upload_image(url, pid, poll_interval=3.0, timeout_seconds=120, request_timeout=30)
        file_ids.append(uploaded["file_id"])

    post_at = PMP.now_iso()
    links: list[dict[str, Any]] = []
    failures: list[str] = []
    for p in platforms:
        account_id = p.get("account_id")
        network = p.get("network")
        if not account_id:
            failures.append(f"{network or p}: no account_id")
            continue
        is_pinterest = network == "pinterest"
        try:
            result = PMP.create_publication(
                pid,
                account_ids=[int(account_id)],
                content=text,
                title=title if is_pinterest else None,
                link=link if is_pinterest else None,
                file_ids=file_ids,
                post_at=post_at,
                status=PMP.STATUS_PENDING_PUBLICATION,
                publication_type=PMP.TYPE_POST,
                request_timeout=30,
            )
            links.append({
                "id": result.get("id"),
                "network": network,
                "account_id": int(account_id),
                "post_at": post_at,
            })
            log(f"  ✅ {network} (account {account_id}) → publication {result.get('id')}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{network}({account_id}): {exc}")
            log(f"  ❌ {network} (account {account_id}) → {exc}")

    status = "published" if links and not failures else ("failed" if not links else "failed")
    return {"status": status, "links": links, "failures": failures}


def record_outcome(draft_id: int, outcome: dict[str, Any]) -> None:
    fields: dict[str, Any] = {"status": outcome["status"]}
    if outcome.get("failures"):
        fields["notes"] = "; ".join(outcome["failures"])[:1000]
    if outcome.get("links"):
        fields["publication_links"] = outcome["links"]
        CS.update_draft(draft_id, fields, append_links=True)
    else:
        CS.update_draft(draft_id, fields, append_links=False)


def process_once(*, limit: int, dry_run: bool) -> int:
    pid = PMP.project_id()
    ids = due_draft_ids(limit)
    if not ids:
        log("Нет постов к публикации (status=scheduled с наступившим временем).")
        return 0
    log(f"К публикации готово: {len(ids)} черновик(ов) — {ids}")
    published = 0
    for draft_id in ids:
        row = claim_draft(draft_id)
        if row is None:
            log(f"Черновик {draft_id}: уже взят другим прогоном — пропуск (идемпотентность).")
            continue
        log(f"Черновик {draft_id}: «{row.get('topic') or row.get('product_name')}» — публикую…")
        try:
            outcome = publish_draft(row, pid, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            log(f"Черновик {draft_id}: ошибка публикации → {exc}")
            if not dry_run:
                CS.update_draft(draft_id, {"status": "failed", "notes": str(exc)[:1000]}, append_links=False)
            continue
        if dry_run:
            # release the claim back to scheduled so a real run can pick it up
            table = CS.qualified_content_table()
            run_psql_query(f"UPDATE {table} SET status='scheduled' WHERE id={draft_id} AND status='publishing' RETURNING id;")
            continue
        record_outcome(draft_id, outcome)
        if outcome["status"] == "published":
            published += 1
        log(f"Черновик {draft_id}: итог = {outcome['status']} "
            f"({len(outcome['links'])} публикаций, {len(outcome.get('failures') or [])} ошибок).")
    return published


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes autoposter (stage 6 cron, no LLM)")
    parser.add_argument("--limit", type=int, default=20, help="max drafts per pass")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be posted without publishing (claim is released)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file()
    process_once(limit=args.limit, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        log(f"FATAL: {exc}")
        raise SystemExit(1)
