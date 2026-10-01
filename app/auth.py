"""Telegram Mini App initData validation.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
hash       = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))
data_check_string is every field except `hash`, as key=value, sorted by key,
joined with newlines.
"""
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode


class InitDataError(Exception):
    pass


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str
    username: str | None
    language_code: str | None


def _secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def _check_string(fields: dict[str, str]) -> str:
    return "\n".join(f"{k}={fields[k]}" for k in sorted(fields))


def validate_init_data(
    init_data: str, bot_token: str, max_age_seconds: int = 86400, now: float | None = None
) -> TelegramUser:
    if not init_data:
        raise InitDataError("missing initData")
    if not bot_token:
        raise InitDataError("server has no bot token configured")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as e:
        raise InitDataError("malformed initData") from e
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InitDataError("duplicate keys")
    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("missing hash")

    expected = hmac.new(_secret(bot_token), _check_string(fields).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise InitDataError("bad signature")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError) as e:
        raise InitDataError("missing auth_date") from e
    current = time.time() if now is None else now
    if max_age_seconds and current - auth_date > max_age_seconds:
        raise InitDataError("initData expired")

    try:
        user = json.loads(fields["user"])
        return TelegramUser(
            id=int(user["id"]),
            first_name=str(user.get("first_name", "")),
            username=user.get("username"),
            language_code=user.get("language_code"),
        )
    except (KeyError, ValueError, TypeError) as e:
        raise InitDataError("missing user") from e


# Browser demo visitors get IDs far below any Telegram chat ID (groups and
# channels are around -1e12), so they can never collide with a real chat.
DEMO_ID_BASE = 9_000_000_000_000_000


def is_demo_id(user_id: int | None) -> bool:
    return user_id is not None and user_id <= -DEMO_ID_BASE


def demo_user(visitor: str) -> TelegramUser:
    """Map an opaque per-browser visitor token to a stable fake user."""
    visitor = visitor.strip()
    if not 8 <= len(visitor) <= 64 or not all(c.isalnum() or c in "-_" for c in visitor):
        raise InitDataError("bad demo visitor id")
    h = int.from_bytes(hashlib.sha256(visitor.encode()).digest()[:6], "big")
    return TelegramUser(id=-(DEMO_ID_BASE + h), first_name="Guest", username=None, language_code="en")


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a signed initData string. Used by tests and the screenshot script."""
    fields = dict(fields)
    fields["hash"] = hmac.new(
        _secret(bot_token), _check_string(fields).encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


def make_test_init_data(
    bot_token: str, user_id: int, first_name: str = "Test", auth_date: int | None = None
) -> str:
    return sign_init_data(
        {
            "query_id": "AAEtest",
            "user": json.dumps({"id": user_id, "first_name": first_name, "language_code": "en"},
                               separators=(",", ":")),
            "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        },
        bot_token,
    )
