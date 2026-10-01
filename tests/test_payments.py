"""Stars, Crypto Pay and demo payments end to end, with Telegram and
Crypto Pay replaced by fakes."""
import hashlib
import hmac
import json
from datetime import UTC, datetime

from tests.conftest import BARISTAS, CP_TOKEN, CUSTOMER, OTHER, Env, auth

MSG = dict(date=int(datetime.now(UTC).timestamp()))


def user(uid=CUSTOMER, name="Mia"):
    return {"id": uid, "is_bot": False, "first_name": name, "username": "mia_tests"}


async def precheckout(env, order, uid=CUSTOMER, amount=None, currency="XTR", qid="pq1"):
    await env.feed(pre_checkout_query={
        "id": qid, "from": user(uid), "currency": currency,
        "total_amount": order["total_stars"] if amount is None else amount,
        "invoice_payload": f"order:{order['id']}",
    })
    return env.tg.of("AnswerPreCheckoutQuery")[-1]


async def successful_payment(env, order, charge="tg-charge-1"):
    await env.feed(message={
        **MSG, "message_id": 77, "chat": {"id": CUSTOMER, "type": "private"}, "from": user(),
        "successful_payment": {
            "currency": "XTR", "total_amount": order["total_stars"], "invoice_payload": f"order:{order['id']}",
            "telegram_payment_charge_id": charge, "provider_payment_charge_id": "",
        },
    })


async def test_stars_full_flow(env):
    o = await env.order(comment="for <Mia>")
    r = await env.http.post(f"/api/orders/{o['id']}/pay/stars", headers=auth(CUSTOMER))
    assert r.json() == {"invoice_link": f"https://t.me/$order-{o['id']}"}
    link = env.tg.of("CreateInvoiceLink")[0]
    assert link.currency == "XTR" and link.provider_token == ""
    assert link.prices[0].amount == 785 and link.payload == f"order:{o['id']}"

    ans = await precheckout(env, o)
    assert ans.ok is True

    await successful_payment(env, o)
    order = (await env.http.get(f"/api/orders/{o['id']}", headers=auth(CUSTOMER))).json()
    assert order["status"] == "new" and order["payment_method"] == "stars"

    barista, customer = env.tg.of("SendMessage")
    assert barista.chat_id == BARISTAS
    assert f"Order #{o['id']}" in barista.text and "⭐ 785 Stars" in barista.text
    assert "2× Burnt Sugar Latte · Large · oat — $12.20" in barista.text
    assert "for &lt;Mia&gt;" in barista.text  # user text is HTML-escaped
    buttons = [b.callback_data for b in barista.reply_markup.inline_keyboard[0]]
    assert buttons == [f"st:{o['id']}:preparing", f"st:{o['id']}:ready"]
    assert customer.chat_id == CUSTOMER and "is paid" in customer.text

    # Telegram retries / duplicate updates must not double-notify.
    await successful_payment(env, o)
    assert len(env.tg.of("SendMessage")) == 2


async def test_precheckout_rejections(env):
    o = await env.order()
    assert (await precheckout(env, o, amount=1, qid="a")).ok is False
    assert (await precheckout(env, o, currency="USD", qid="b")).ok is False
    assert (await precheckout(env, o, uid=OTHER, qid="c")).ok is False
    bogus = {**o, "id": 9999}
    ans = await precheckout(env, bogus, qid="d")
    assert ans.ok is False and "no longer exists" in ans.error_message
    await successful_payment(env, o)
    ans = await precheckout(env, o, qid="e")
    assert ans.ok is False and "already paid" in ans.error_message


async def test_paid_order_cannot_be_paid_again(env_nocrypto):
    env = env_nocrypto
    o = await env.order()
    assert (await env.http.post(f"/api/orders/{o['id']}/pay/demo", headers=auth(CUSTOMER))).status_code == 200
    for kind in ("stars", "crypto", "demo"):
        assert (await env.http.post(f"/api/orders/{o['id']}/pay/{kind}", headers=auth(CUSTOMER))).status_code == 409


async def test_crypto_invoice_and_webhook(env):
    o = await env.order()
    r = (await env.http.post(f"/api/orders/{o['id']}/pay/crypto", headers=auth(CUSTOMER))).json()
    assert r["mode"] == "crypto" and r["url"].startswith("https://t.me/CryptoTestnetBot/app")
    method, body = env.cp.requests[0]
    assert method == "createInvoice"
    assert body["currency_type"] == "fiat" and body["fiat"] == "USD" and body["amount"] == "15.70"
    assert body["payload"] == f"order:{o['id']}" and body["accepted_assets"] == "USDT,TON"
    # Asking again reuses the same invoice.
    again = (await env.http.post(f"/api/orders/{o['id']}/pay/crypto", headers=auth(CUSTOMER))).json()
    assert again["url"] == r["url"] and len(env.cp.requests) == 1
    # Demo payment is refused while real crypto works.
    assert (await env.http.post(f"/api/orders/{o['id']}/pay/demo", headers=auth(CUSTOMER))).status_code == 403

    inv = env.cp.mark_paid(5001)
    raw = json.dumps({"update_id": 1, "update_type": "invoice_paid", "payload": inv}).encode()
    bad = await env.http.post("/cryptopay/webhook", content=raw, headers={"crypto-pay-api-signature": "00" * 32})
    assert bad.status_code == 401
    sig = hmac.new(hashlib.sha256(CP_TOKEN.encode()).digest(), raw, hashlib.sha256).hexdigest()
    ok = await env.http.post("/cryptopay/webhook", content=raw, headers={"crypto-pay-api-signature": sig})
    assert ok.status_code == 200
    order = (await env.http.get(f"/api/orders/{o['id']}", headers=auth(CUSTOMER))).json()
    assert order["status"] == "new" and order["payment_method"] == "crypto"
    assert "Crypto Pay (testnet)" in env.tg.of("SendMessage")[0].text
    # Webhook retry is harmless.
    await env.http.post("/cryptopay/webhook", content=raw, headers={"crypto-pay-api-signature": sig})
    assert len(env.tg.of("SendMessage")) == 2


async def test_crypto_paid_detected_by_polling(env):
    o = await env.order()
    await env.http.post(f"/api/orders/{o['id']}/pay/crypto", headers=auth(CUSTOMER))
    pending = (await env.http.get(f"/api/orders/{o['id']}?check=1", headers=auth(CUSTOMER))).json()
    assert pending["status"] == "awaiting_payment"
    env.cp.mark_paid(5001)
    paid = (await env.http.get(f"/api/orders/{o['id']}?check=1", headers=auth(CUSTOMER))).json()
    assert paid["status"] == "new" and paid["payment_method"] == "crypto"


async def test_crypto_down_falls_back_to_demo(env):
    env.cp.down = True
    o = await env.order()
    r = (await env.http.post(f"/api/orders/{o['id']}/pay/crypto", headers=auth(CUSTOMER))).json()
    assert r == {"mode": "demo", "reason": "Crypto Pay is unavailable right now"}
    paid = (await env.http.post(f"/api/orders/{o['id']}/pay/demo", headers=auth(CUSTOMER))).json()
    assert paid["payment_method"] == "demo" and paid["status"] == "new"
    assert "Demo payment" in env.tg.of("SendMessage")[0].text


async def test_no_crypto_token_falls_back_to_demo(env_nocrypto):
    env = env_nocrypto
    o = await env.order()
    r = (await env.http.post(f"/api/orders/{o['id']}/pay/crypto", headers=auth(CUSTOMER))).json()
    assert r["mode"] == "demo"


async def test_demo_payments_can_be_switched_off(tmp_path):
    async with Env(tmp_path, crypto=False, demo_payments=False) as env:
        o = await env.order()
        r = (await env.http.post(f"/api/orders/{o['id']}/pay/crypto", headers=auth(CUSTOMER))).json()
        assert r["mode"] == "unavailable"
        assert (await env.http.post(f"/api/orders/{o['id']}/pay/demo", headers=auth(CUSTOMER))).status_code == 403


async def test_blocked_customer_does_not_break_payment(env_nocrypto):
    env = env_nocrypto
    env.tg.blocked.add(CUSTOMER)
    o = await env.order()
    paid = (await env.http.post(f"/api/orders/{o['id']}/pay/demo", headers=auth(CUSTOMER))).json()
    assert paid["status"] == "new"
    assert env.tg.of("SendMessage")[0].chat_id == BARISTAS
