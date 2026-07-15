#!/usr/bin/env python3
"""Остатки на счетах провайдеров: OpenRouter, kie.ai, fal.ai.

Зависимостей нет — только stdlib.

  python balance.py check           человекочитаемая сводка (для бота)
  python balance.py check --json    то же в JSON (для скриптов/тестов)

Ключи берутся из окружения; если их там нет — подхватываются из .env
(см. load_env_file). Management/admin-ключи намеренно НЕ проброшены в
песочницу агента через terminal.env_passthrough, поэтому .env читаем сами.

Переменные:
  OPENROUTER_MANAGEMENT_KEY  остаток в $ (обязателен: рантайм-ключ /credits не отдаёт)
  OPENROUTER_API_KEY         расход за день/неделю/месяц (рантайм-ключ)
  KIE_AI_API_KEY             кредиты kie.ai
  FAL_ADMIN_KEY              баланс fal.ai (нужен именно Admin-scope ключ)
  HERMES_ENV_FILE            необязательный явный путь к .env
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 25

FAL_KEYS_URL = "https://fal.ai/dashboard/keys"


def load_env_file() -> None:
    """Подхватить KEY=VALUE из .env Hermes. Существующее окружение не трогаем."""
    candidates = []
    explicit = os.environ.get("HERMES_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit))
    home = os.environ.get("HERMES_HOME")
    if home:
        candidates.append(Path(home) / ".env")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / ".env")
    candidates.append(Path.home() / ".hermes" / ".env")
    candidates.append(Path(__file__).resolve().parents[4] / ".env")

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


def http_get_json(url: str, headers: dict) -> tuple[int, object]:
    """GET → (http_status, разобранный JSON | сырой текст). Сетевые ошибки наружу."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def _money(value: float) -> str:
    return f"${value:,.2f}".replace(",", " ")


# --- Провайдеры ------------------------------------------------------------
#
# Каждая проверка возвращает единый контракт:
#   {"provider", "title", "ok": bool, "balance": str, "detail": str|None, "error": str|None}
# ok=False означает «честно не смогли узнать» — с причиной. Выдумывать цифры
# нельзя (правило этапа 8), а падать из-за одного провайдера — нельзя тем более:
# остальные должны показаться.


def check_openrouter() -> dict:
    out = {"provider": "openrouter", "title": "OpenRouter", "ok": False,
           "balance": None, "detail": None, "error": None}

    mgmt = os.environ.get("OPENROUTER_MANAGEMENT_KEY") or os.environ.get("OPENROUTER_PROVISIONING_KEY")
    runtime = os.environ.get("OPENROUTER_API_KEY")

    if not mgmt:
        out["error"] = ("не задан OPENROUTER_MANAGEMENT_KEY в .env — "
                        "рантайм-ключ остаток не отдаёт, нужен management-ключ")
    else:
        try:
            status, data = http_get_json(
                "https://openrouter.ai/api/v1/credits",
                {"Authorization": f"Bearer {mgmt}"},
            )
            if status == 200 and isinstance(data, dict) and "data" in data:
                d = data["data"]
                total = float(d.get("total_credits", 0))
                used = float(d.get("total_usage", 0))
                left = total - used
                out["ok"] = True
                out["balance"] = f"{_money(left)} из {_money(total)} (потрачено {_money(used)})"
                out["remaining_usd"] = round(left, 4)
            elif status == 403:
                out["error"] = "ключ не management — OpenRouter не отдаёт остаток такому ключу"
            else:
                out["error"] = f"OpenRouter ответил HTTP {status}"
        except Exception as exc:
            out["error"] = f"не достучались до OpenRouter: {exc}"

    # Расход — приятное дополнение, рантайм-ключом. Его отсутствие не ошибка.
    if runtime:
        try:
            status, data = http_get_json(
                "https://openrouter.ai/api/v1/auth/key",
                {"Authorization": f"Bearer {runtime}"},
            )
            if status == 200 and isinstance(data, dict) and "data" in data:
                d = data["data"]
                out["detail"] = (
                    f"расход: сегодня {_money(float(d.get('usage_daily', 0)))}"
                    f" · неделя {_money(float(d.get('usage_weekly', 0)))}"
                    f" · месяц {_money(float(d.get('usage_monthly', 0)))}"
                )
        except Exception:
            pass  # расход необязателен — молча пропускаем

    return out


def check_kie() -> dict:
    out = {"provider": "kie", "title": "kie.ai", "ok": False,
           "balance": None, "detail": None, "error": None}
    key = os.environ.get("KIE_AI_API_KEY")
    if not key:
        out["error"] = "не задан KIE_AI_API_KEY в .env"
        return out
    try:
        status, data = http_get_json(
            "https://api.kie.ai/api/v1/chat/credit",
            {"Authorization": f"Bearer {key}"},
        )
        if status == 200 and isinstance(data, dict) and data.get("code") == 200:
            credits = data.get("data")
            out["ok"] = True
            out["balance"] = f"{credits:g} кредитов"
            out["credits"] = credits
        elif status in (401, 403):
            out["error"] = "ключ kie.ai не принят (проверь KIE_AI_API_KEY)"
        else:
            msg = data.get("msg") if isinstance(data, dict) else None
            out["error"] = f"kie.ai ответил HTTP {status}" + (f": {msg}" if msg else "")
    except Exception as exc:
        out["error"] = f"не достучались до kie.ai: {exc}"
    return out


def check_fal() -> dict:
    out = {"provider": "fal", "title": "fal.ai", "ok": False,
           "balance": None, "detail": None, "error": None}
    key = os.environ.get("FAL_ADMIN_KEY")
    if not key:
        out["error"] = (f"не задан FAL_ADMIN_KEY в .env — нужен ключ со скоупом Admin "
                        f"({FAL_KEYS_URL}); обычный API-ключ баланс не отдаёт")
        return out
    try:
        status, data = http_get_json(
            "https://api.fal.ai/v1/account/billing?expand=credits",
            {"Authorization": f"Key {key}"},
        )
        if status == 200 and isinstance(data, dict):
            credits = data.get("credits") or {}
            balance = credits.get("current_balance")
            if balance is None:
                out["error"] = "fal.ai не вернул баланс (ответ без credits)"
                return out
            currency = credits.get("currency", "USD")
            out["ok"] = True
            out["balance"] = (_money(float(balance)) if currency == "USD"
                              else f"{balance:g} {currency}")
            out["remaining"] = balance
            if data.get("username"):
                out["detail"] = f"аккаунт: {data['username']}"
        elif status == 403:
            out["error"] = (f"ключ не Admin-scope — fal.ai не отдаёт биллинг такому ключу "
                            f"(создать Admin-ключ: {FAL_KEYS_URL})")
        elif status == 401:
            out["error"] = "ключ fal.ai не принят (проверь FAL_ADMIN_KEY)"
        else:
            out["error"] = f"fal.ai ответил HTTP {status}"
    except Exception as exc:
        out["error"] = f"не достучались до fal.ai: {exc}"
    return out


def render(results: list[dict]) -> str:
    lines = ["💰 Остатки на счетах", ""]
    for r in results:
        if r["ok"]:
            lines.append(f"• {r['title']}: {r['balance']}")
            if r.get("detail"):
                lines.append(f"  {r['detail']}")
        else:
            lines.append(f"• {r['title']}: не смог узнать — {r['error']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Остатки на счетах провайдеров")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="показать остатки по всем провайдерам")
    check.add_argument("--json", action="store_true", help="вывести JSON вместо текста")
    args = parser.parse_args()

    load_env_file()
    results = [check_openrouter(), check_kie(), check_fal()]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(render(results))

    # Ошибка только если не удалось узнать вообще ничего: одна недоступная
    # площадка не должна ронять команду — остальные уже показаны.
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
