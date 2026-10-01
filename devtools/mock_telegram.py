"""Run BrewBook locally with NO bot token and NO Crypto Pay account.

Telegram and Crypto Pay are replaced by the same fakes the tests use, and
/dev/ adds a simulator page: the Mini App in a phone frame (loaded with the
real telegram-web-app.js, talking to the "client" over postMessage exactly
like Telegram Web does), plus the customer's chat and the baristas' chat.

    python -m devtools.mock_telegram          # then open http://localhost:8000/dev/
    CRYPTO=off python -m devtools.mock_telegram   # crypto "unavailable" -> demo payment

For local testing and recording only. Never expose it publicly."""
import hashlib
import hmac
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from app.auth import make_test_init_data
from app.config import Settings
from app.cryptopay import CryptoPay
from app.main import create_app
from app.models import Order
from devtools.fakes import BOT_TOKEN, FakeCryptoPay, make_bot

CUSTOMER = {"id": 4242, "first_name": "Mia", "username": "mia_demo"}
BARISTA = {"id": 777, "first_name": "Sam"}
BARISTAS_CHAT = -100555
CP_TOKEN = "mock-cryptopay-token"
HERE = Path(__file__).resolve().parent


def build():
    db = os.environ.get("MOCK_DB") or f"{tempfile.mkdtemp()}/brewbook-mock.db"
    crypto_on = os.environ.get("CRYPTO", "on") != "off"
    settings = Settings(
        _env_file=None, bot_token=BOT_TOKEN, database_url=f"sqlite+aiosqlite:///{db}",
        webapp_url="/app/", barista_chat_id=BARISTAS_CHAT, owner_ids=str(CUSTOMER["id"]),
        cryptopay_token=CP_TOKEN if crypto_on else "", run_bot=False, browser_demo=True,
        stars_per_usd=float(os.environ.get("STARS_PER_USD", 50)),
    )
    bot, tg = make_bot()
    cp = FakeCryptoPay(CP_TOKEN)
    client = CryptoPay(CP_TOKEN, settings.cryptopay_base_url, transport=cp.transport) if crypto_on else None
    app = create_app(settings, bot=bot, cryptopay=client)
    counter = {"update": 0, "msg": 1000}

    async def feed(**update):
        from aiogram.types import Update
        counter["update"] += 1
        await app.state.dispatcher.feed_update(bot, Update(update_id=counter["update"], **update))

    def now():
        return int(datetime.now(UTC).timestamp())

    def user(u):
        return {"is_bot": False, **u}

    @app.get("/dev/", response_class=HTMLResponse, include_in_schema=False)
    async def harness():
        page = (HERE / "harness.html").read_text()
        return page.replace("__INIT_DATA__", json.dumps(
            make_test_init_data(BOT_TOKEN, CUSTOMER["id"], CUSTOMER["first_name"])
        ))

    @app.get("/dev/chats")
    async def chats():
        out = {"customer": [], "baristas": []}
        for (chat_id, _), m in sorted(tg.messages.items(), key=lambda kv: kv[0][1]):
            key = "customer" if chat_id == CUSTOMER["id"] else "baristas" if chat_id == BARISTAS_CHAT else None
            if key is None:
                continue
            kb = m.reply_markup.inline_keyboard if m.reply_markup else []
            out[key].append({
                "id": m.message_id, "html": m.text,
                "buttons": [[{"text": b.text, "data": b.callback_data, "web_app": bool(b.web_app)} for b in row] for row in kb],
            })
        return out

    @app.post("/dev/send")
    async def send(request: Request):
        body = await request.json()
        text = body["text"]
        counter["msg"] += 1
        cmd_len = len(text.split()[0]) if text.startswith("/") else 0
        chat = BARISTAS_CHAT if body.get("chat") == "baristas" else CUSTOMER["id"]
        sender = BARISTA if chat == BARISTAS_CHAT else CUSTOMER
        await feed(message={
            "message_id": counter["msg"], "date": now(), "text": text,
            "chat": {"id": chat, "type": "supergroup" if chat == BARISTAS_CHAT else "private"},
            "from": user(sender),
            "entities": [{"type": "bot_command", "offset": 0, "length": cmd_len}] if cmd_len else [],
        })
        return {"ok": True}

    @app.post("/dev/stars/{slug}")
    async def stars_pay(slug: str):
        """What Telegram does after the customer confirms a Stars invoice."""
        order_id = int(slug.rsplit("-", 1)[-1])
        async with app.state.sessionmaker() as s:
            order = await s.get(Order, order_id)
        payload = f"order:{order_id}"
        await feed(pre_checkout_query={
            "id": f"pq{order_id}{now()}", "from": user(CUSTOMER), "currency": "XTR",
            "total_amount": order.total_stars, "invoice_payload": payload,
        })
        answer = tg.of("AnswerPreCheckoutQuery")[-1]
        if not answer.ok:
            return {"status": "failed", "error": answer.error_message}
        counter["msg"] += 1
        await feed(message={
            "message_id": counter["msg"], "date": now(), "chat": {"id": CUSTOMER["id"], "type": "private"},
            "from": user(CUSTOMER), "successful_payment": {
                "currency": "XTR", "total_amount": order.total_stars, "invoice_payload": payload,
                "telegram_payment_charge_id": f"mock-{order_id}", "provider_payment_charge_id": "",
            },
        })
        return {"status": "paid"}

    @app.post("/dev/crypto/pay/{invoice_id}")
    async def crypto_pay(invoice_id: int, request: Request):
        """What @CryptoTestnetBot does after a test-coin payment: a signed webhook."""
        inv = cp.mark_paid(invoice_id)
        body = json.dumps({"update_id": invoice_id, "update_type": "invoice_paid", "payload": inv}).encode()
        sig = hmac.new(hashlib.sha256(CP_TOKEN.encode()).digest(), body, hashlib.sha256).hexdigest()
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock") as c:
            r = await c.post("/cryptopay/webhook", content=body, headers={"crypto-pay-api-signature": sig})
        return {"webhook_status": r.status_code}

    @app.post("/dev/tap")
    async def tap(request: Request):
        body = await request.json()
        await feed(callback_query={
            "id": f"cb{now()}{body['message_id']}", "from": user(BARISTA), "chat_instance": "mock",
            "data": body["data"], "message": {
                "message_id": body["message_id"], "date": now(), "text": "order",
                "chat": {"id": BARISTAS_CHAT, "type": "supergroup"},
            },
        })
        ans = tg.of("AnswerCallbackQuery")[-1]
        return {"text": ans.text, "alert": bool(ans.show_alert)}

    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
