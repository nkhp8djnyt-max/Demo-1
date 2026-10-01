from app.auth import make_test_init_data
from tests.conftest import CUSTOMER, OTHER, Env, auth


async def test_every_api_call_needs_valid_init_data(env):
    paths = [("GET", "/api/bootstrap"), ("GET", "/api/orders"), ("POST", "/api/orders"),
             ("GET", "/api/orders/1"), ("POST", "/api/orders/1/pay/stars"),
             ("POST", "/api/orders/1/pay/crypto"), ("POST", "/api/orders/1/pay/demo")]
    forged = {"Authorization": "tma " + make_test_init_data("999:not-our-bot", CUSTOMER)}
    for method, path in paths:
        for headers in ({}, forged, {"Authorization": "demo abcdefgh12345"}):
            r = await env.http.request(method, path, headers=headers, json={"lines": []})
            assert r.status_code == 401, (method, path, headers, r.status_code)


async def test_bootstrap_menu(env):
    r = await env.http.get("/api/bootstrap", headers=auth(CUSTOMER))
    b = r.json()
    assert len(b["items"]) == 12
    assert {i["category"] for i in b["items"]} == {"coffee", "not-coffee", "bakery"}
    assert b["payments"] == {"stars": True, "crypto": True, "crypto_network": "testnet", "demo": True}
    assert b["user"]["first_name"] == "Mia"


async def test_server_computes_total_and_ignores_client_user_and_prices(env):
    r = await env.http.post("/api/orders", headers=auth(CUSTOMER), json={
        "user_id": OTHER, "total_cents": 1,
        "lines": [{"item_id": "burnt-sugar-latte", "qty": 2, "size": "L", "milk": "oat", "unit_cents": 1},
                  {"item_id": "cardamom-bun", "qty": 1}],
        "comment": "  name on cup:   <b>Mia</b> ",
    })
    o = r.json()
    assert o["total_cents"] == 2 * (450 + 100 + 60) + 350 == 1570
    assert o["total_stars"] == 785
    assert o["status"] == "awaiting_payment"
    assert o["comment"] == "name on cup: <b>Mia</b>"
    assert [l["label"] for l in o["lines"]] == ["Burnt Sugar Latte · Large · oat", "Cardamom Bun"]
    # The order belongs to the signed user, not the user_id in the body.
    assert (await env.http.get(f"/api/orders/{o['id']}", headers=auth(OTHER))).status_code == 404
    assert (await env.http.get(f"/api/orders/{o['id']}", headers=auth(CUSTOMER))).status_code == 200


async def test_same_line_twice_is_merged_and_defaults_applied(env):
    o = await env.order(lines=[{"item_id": "velvet-flat-white", "qty": 1}, {"item_id": "velvet-flat-white", "qty": 2, "size": "M", "milk": "whole"}])
    assert len(o["lines"]) == 1 and o["lines"][0]["qty"] == 3
    assert o["lines"][0]["unit_cents"] == 390 + 50


async def test_invalid_carts_rejected(env):
    bad = [
        [],
        [{"item_id": "unicorn-latte", "qty": 1}],
        [{"item_id": "ember-espresso", "qty": 0}],
        [{"item_id": "ember-espresso", "qty": 21}],
        [{"item_id": "ember-espresso", "qty": 1, "size": "L"}],
        [{"item_id": "cardamom-bun", "qty": 1, "milk": "oat"}],
        [{"item_id": "dark-cocoa", "qty": 1, "milk": "goat"}],
        [{"item_id": "dark-cocoa", "qty": 1, "size": "XL"}],
    ]
    for lines in bad:
        r = await env.http.post("/api/orders", headers=auth(CUSTOMER), json={"lines": lines})
        assert r.status_code in (400, 422), (lines, r.status_code)
    r = await env.http.post("/api/orders", headers=auth(CUSTOMER),
                            json={"lines": [{"item_id": "ember-espresso", "qty": 1}], "comment": "x" * 201})
    assert r.status_code == 400


async def test_cannot_pay_someone_elses_order(env):
    o = await env.order()
    for kind in ("stars", "crypto", "demo"):
        r = await env.http.post(f"/api/orders/{o['id']}/pay/{kind}", headers=auth(OTHER))
        assert r.status_code == 404


async def test_my_orders_lists_only_paid_own_orders(env_nocrypto):
    env = env_nocrypto
    paid = await env.order()
    await env.order()  # unpaid, not listed
    await env.order(user=OTHER)
    await env.http.post(f"/api/orders/{paid['id']}/pay/demo", headers=auth(CUSTOMER))
    rows = (await env.http.get("/api/orders", headers=auth(CUSTOMER))).json()
    assert [r["id"] for r in rows] == [paid["id"]]


async def test_browser_demo_mode(tmp_path):
    async with Env(tmp_path, browser_demo=True) as env:
        guest = {"Authorization": "demo visitor-123456"}
        b = (await env.http.get("/api/bootstrap", headers=guest)).json()
        assert b["browser_demo"] is True
        assert b["payments"]["stars"] is False and b["payments"]["crypto"] is False
        o = (await env.http.post("/api/orders", headers=guest, json={"lines": [{"item_id": "ember-espresso", "qty": 1}]})).json()
        assert (await env.http.post(f"/api/orders/{o['id']}/pay/stars", headers=guest)).status_code == 400
        r = (await env.http.post(f"/api/orders/{o['id']}/pay/crypto", headers=guest)).json()
        assert r["mode"] == "demo"
        paid = (await env.http.post(f"/api/orders/{o['id']}/pay/demo", headers=guest)).json()
        assert paid["status"] == "new" and paid["payment_method"] == "demo"
        # Guests have no Telegram chat; only the barista chat is notified.
        sent = env.tg.of("SendMessage")
        assert [m.chat_id for m in sent] == [env.settings.barista_chat_id]
        assert "browser demo" in sent[0].text
        # Another browser can't see it.
        other = {"Authorization": "demo visitor-999999"}
        assert (await env.http.get(f"/api/orders/{o['id']}", headers=other)).status_code == 404


async def test_static_app_and_health(env):
    assert (await env.http.get("/health")).json() == {"ok": True}
    page = await env.http.get("/app/")
    assert page.status_code == 200
    assert "telegram-web-app.js" in page.text and "Demo project" in page.text
