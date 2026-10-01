"""Test doubles for the two outside services: the Telegram Bot API (an
aiogram session that records calls instead of sending them) and the
Crypto Pay API (an httpx transport that answers like the real thing)."""
import json
from datetime import UTC, datetime
from itertools import count

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Chat, Message

BOT_TOKEN = "123456:TEST-token-for-unit-tests"


class FakeTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list = []
        self.blocked: set[int] = set()
        self.messages: dict[tuple[int, int], Message] = {}  # latest version of every sent message
        self._ids = count(100)

    def of(self, name: str) -> list:
        return [c for c in self.calls if type(c).__name__ == name]

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        chat_id = getattr(method, "chat_id", None)
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if name in ("SendMessage", "EditMessageText"):
            msg = Message(
                message_id=getattr(method, "message_id", None) or next(self._ids),
                date=datetime.now(UTC), chat=Chat(id=chat_id, type="private"), text=method.text,
                reply_markup=method.reply_markup,
            )
            self.messages[(chat_id, msg.message_id)] = msg
            return msg
        if name == "CreateInvoiceLink":
            return "https://t.me/$" + method.payload.replace(":", "-")
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self):
        pass


def make_bot() -> tuple[Bot, FakeTelegram]:
    session = FakeTelegram()
    return Bot(BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML")), session


class FakeCryptoPay:
    """Speaks the Crypto Pay API over an httpx MockTransport."""

    def __init__(self, token: str):
        self.token = token
        self.invoices: dict[int, dict] = {}
        self.requests: list[tuple[str, dict]] = []
        self.down = False
        self._ids = count(5001)
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused")
        if request.headers.get("Crypto-Pay-API-Token") != self.token:
            return httpx.Response(401, json={"ok": False, "error": {"code": 401, "name": "UNAUTHORIZED"}})
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.requests.append((method, body))
        if method == "createInvoice":
            inv_id = next(self._ids)
            inv = {
                "invoice_id": inv_id, "status": "active", "currency_type": body["currency_type"],
                "fiat": body["fiat"], "amount": body["amount"], "payload": body["payload"],
                "bot_invoice_url": f"https://t.me/CryptoTestnetBot?start=IV{inv_id}",
                "mini_app_invoice_url": f"https://t.me/CryptoTestnetBot/app?startapp=invoice-IV{inv_id}",
            }
            self.invoices[inv_id] = inv
            return httpx.Response(200, json={"ok": True, "result": inv})
        if method == "getInvoices":
            ids = [int(x) for x in body["invoice_ids"].split(",")]
            return httpx.Response(200, json={"ok": True, "result": {"items": [self.invoices[i] for i in ids if i in self.invoices]}})
        return httpx.Response(400, json={"ok": False, "error": {"code": 400, "name": "METHOD_NOT_FOUND"}})

    def mark_paid(self, invoice_id: int) -> dict:
        self.invoices[invoice_id]["status"] = "paid"
        return self.invoices[invoice_id]
