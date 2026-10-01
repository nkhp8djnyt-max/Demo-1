from datetime import UTC, datetime

from tests.conftest import BARISTAS, CUSTOMER, OWNER, auth

NOW = int(datetime.now(UTC).timestamp())


def msg(text, uid=CUSTOMER, chat=None, chat_type="private"):
    return {"message_id": 1, "date": NOW, "text": text,
            "chat": {"id": chat or uid, "type": chat_type},
            "from": {"id": uid, "is_bot": False, "first_name": "Mia <3"},
            "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]}


def tap(env, data, chat=BARISTAS, uid=777, message_id=100):
    return env.feed(callback_query={
        "id": f"cb{data}{chat}", "from": {"id": uid, "is_bot": False, "first_name": "Sam"}, "chat_instance": "x",
        "data": data, "message": {"message_id": message_id, "date": NOW, "chat": {"id": chat, "type": "supergroup"}, "text": "order"},
    })


async def test_start_shows_open_menu_button(env):
    await env.feed(message=msg("/start"))
    sent = env.tg.of("SendMessage")[0]
    assert "BrewBook" in sent.text and "Mia &lt;3" in sent.text and "Demo project" in sent.text
    button = sent.reply_markup.inline_keyboard[0][0]
    assert button.text == "☕ Open menu" and button.web_app.url == "https://brewbook.example/app/"


async def paid_order(env):
    o = await env.order()
    await env.http.post(f"/api/orders/{o['id']}/pay/demo", headers=auth(CUSTOMER))
    return o


async def test_barista_buttons_move_status_and_notify_customer(env_nocrypto):
    env = env_nocrypto
    o = await paid_order(env)
    env.tg.calls.clear()

    await tap(env, f"st:{o['id']}:preparing")
    edit = env.tg.of("EditMessageText")[0]
    assert edit.chat_id == BARISTAS and "Preparing" in edit.text
    assert [b.callback_data for b in edit.reply_markup.inline_keyboard[0]] == [f"st:{o['id']}:ready"]
    assert env.tg.of("SendMessage")[0].chat_id == CUSTOMER
    assert "being prepared" in env.tg.of("SendMessage")[0].text
    assert (await env.http.get(f"/api/orders/{o['id']}", headers=auth(CUSTOMER))).json()["status"] == "preparing"

    await tap(env, f"st:{o['id']}:ready")
    edit = env.tg.of("EditMessageText")[-1]
    assert "Ready for pickup" in edit.text and edit.reply_markup is None
    assert "is ready" in env.tg.of("SendMessage")[-1].text

    # Going backwards or tapping a stale button is refused.
    env.tg.calls.clear()
    await tap(env, f"st:{o['id']}:preparing")
    assert env.tg.of("AnswerCallbackQuery")[0].show_alert is True
    assert not env.tg.of("SendMessage")


async def test_status_buttons_only_work_in_barista_chat(env_nocrypto):
    env = env_nocrypto
    o = await paid_order(env)
    env.tg.calls.clear()
    await tap(env, f"st:{o['id']}:ready", chat=CUSTOMER)
    assert "Only the barista chat" in env.tg.of("AnswerCallbackQuery")[0].text
    assert (await env.http.get(f"/api/orders/{o['id']}", headers=auth(CUSTOMER))).json()["status"] == "new"


async def test_orders_command_owner_only(env_nocrypto):
    env = env_nocrypto
    o = await paid_order(env)
    env.tg.calls.clear()
    await env.feed(message=msg("/orders", uid=CUSTOMER))
    assert "for the shop owner" in env.tg.of("SendMessage")[-1].text

    await env.feed(message=msg("/orders", uid=OWNER))
    text = env.tg.of("SendMessage")[-1].text
    assert "Recent orders" in text and f"#{o['id']}" in text and "$15.70" in text and "demo" in text

    await env.feed(message=msg("/orders", uid=999, chat=BARISTAS, chat_type="supergroup"))
    assert "Recent orders" in env.tg.of("SendMessage")[-1].text
