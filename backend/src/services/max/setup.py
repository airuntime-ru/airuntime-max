"""Register this deployment's bot with MAX: commands and the webhook subscription.

Run it once after the token is in place, and again whenever the public URL changes:

    python -m src.services.max.setup             # report what MAX currently knows
    python -m src.services.max.setup --apply     # write commands + the webhook subscription

It lives here rather than in ``scripts/`` because it needs the application's settings,
its HTTP client and the Ministry CA that ships in the image - that is, it has to run
*inside* the backend container, and ``scripts/`` is deliberately not copied there.

The webhook URL is derived from ``API_URL`` and ``MAX_WEBHOOK_SECRET``, so the secret is
written down in exactly one place (the environment) and the running API and this tool
cannot disagree about the path.
"""

from __future__ import annotations

import argparse
import sys

from src.core.config import settings
from src.services.max.bot import BOT_COMMANDS
from src.services.max.client import MaxApiError, MaxBotClient


def webhook_url() -> str:
    """The full callback URL, secret included. Never log this verbatim."""
    base = (settings.api_url or "").rstrip("/")
    secret = (settings.max_webhook_secret or "").strip()
    if not base or not secret:
        return ""
    return f"{base}{settings.api_prefix}/max/webhook/{secret}"


def _redact(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/<secret>" if url else ""


def _describe(subscriptions: dict) -> str:
    rows = subscriptions.get("subscriptions") or []
    if not rows:
        return "  (none)"
    return "\n".join(
        f"  {_redact(str(row.get('url', '')))}  {sorted(row.get('update_types') or [])}"
        for row in rows
    )


def _prune_stale_subscriptions(client: MaxBotClient, current_url: str) -> int:
    """Drop our own superseded callbacks - e.g. after the webhook secret is rotated.

    MAX keeps delivering to every registered URL, so a stale one means it retries against
    a 404 forever. Scoped to this deployment's API on purpose: the hackathon bot is
    shared, and another team's subscription is none of our business.
    """
    prefix = current_url.rsplit("/", 1)[0] + "/"
    removed = 0
    for row in client.list_subscriptions().get("subscriptions") or []:
        url = str(row.get("url") or "")
        if url.startswith(prefix) and url != current_url:
            client.delete_subscription(url)
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure the MAX bot for this deployment")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the configuration instead of only reporting it",
    )
    args = parser.parse_args(argv)

    if not settings.max_bot_token:
        print("MAX_BOT_TOKEN is not set", file=sys.stderr)
        return 1

    client = MaxBotClient(
        settings.max_bot_token,
        base_url=settings.max_api_base_url,
        ca_bundle=settings.max_ca_bundle,
    )

    try:
        profile = client.get_me()
    except MaxApiError as exc:
        print(f"Could not reach the MAX API: {exc}", file=sys.stderr)
        return 1

    print(f"bot:      {profile.name} (@{profile.username}, id={profile.user_id})")
    configured_name = (settings.max_bot_username or "").lstrip("@")
    if profile.username and profile.username != configured_name:
        # Deep links are built from the configured name, so a mismatch produces links that
        # open nothing - invisible until a customer taps one.
        print(
            f"warning:  MAX_BOT_USERNAME is {settings.max_bot_username!r} but the bot is "
            f"@{profile.username} - deep links would be wrong",
            file=sys.stderr,
        )

    url = webhook_url()
    if not url:
        print("API_URL or MAX_WEBHOOK_SECRET is missing - cannot build a webhook URL")
        return 1
    print(f"webhook:  {_redact(url)}")
    print(f"mini app: {settings.resolved_max_miniapp_url}")
    # The API cannot set this: every open_app button names the bot, and MAX opens whatever
    # URL is saved against that bot on the partner platform.
    print("          set it by hand: MAX для партнёров -> Чат-боты -> ⋮ -> Настройки")

    try:
        print("current subscriptions:")
        print(_describe(client.list_subscriptions()))
    except MaxApiError as exc:
        print(f"could not list subscriptions: {exc}", file=sys.stderr)

    if not args.apply:
        print("\nDry run. Re-run with --apply to write this configuration.")
        return 0

    if not url.startswith("https://"):
        print(
            "refusing to subscribe a non-HTTPS webhook: MAX only delivers over HTTPS with a "
            "certificate from a trusted CA",
            file=sys.stderr,
        )
        return 1

    try:
        client.set_commands(BOT_COMMANDS)
        print(f"\ncommands set: {', '.join(command['name'] for command in BOT_COMMANDS)}")
        client.subscribe_webhook(
            url,
            update_types=["bot_started", "bot_added", "message_created", "message_callback"],
        )
        print("webhook subscribed")
        removed = _prune_stale_subscriptions(client, url)
        if removed:
            print(f"removed {removed} stale subscription(s) pointing at this API")
        print("subscriptions now:")
        print(_describe(client.list_subscriptions()))
    except MaxApiError as exc:
        print(f"configuration failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
