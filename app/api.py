"""JSON API for the Mini App. Every /api call must carry Telegram initData
(`Authorization: tma <initData>`); the user is taken from the signed data,
never from the request body."""
import logging
from typing import Annotated

from aiogram.exceptions import TelegramAPIError
from aiogram.types import LabeledPrice
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import orders as svc
from app.auth import InitDataError, TelegramUser, demo_user, is_demo_id, validate_init_data
from app.config import Settings
from app.cryptopay import CryptoPayError, verify_webhook
from app.menu import CATEGORIES, MILKS, SIZES, line_label
from app.models import MenuItem, Order, Status
from sqlalchemy import select

log = logging.getLogger(__name__)
router = APIRouter()


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request):
    async with request.app.state.sessionmaker() as s:
        yield s


def current_user(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> TelegramUser:
    settings: Settings = request.app.state.settings
    if settings.browser_demo and authorization and authorization.startswith("demo "):
        try:
            return demo_user(authorization[5:])
        except InitDataError as e:
            raise HTTPException(401, f"Invalid demo session: {e}") from e
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(401, "Open BrewBook from the Telegram bot")
    try:
        return validate_init_data(authorization[4:], settings.bot_token, settings.init_data_max_age)
    except InitDataError as e:
        raise HTTPException(401, f"Invalid Telegram session: {e}") from e


User = Annotated[TelegramUser, Depends(current_user)]
Session = Annotated[object, Depends(get_session)]
Conf = Annotated[Settings, Depends(get_settings_dep)]


def order_json(o: Order, settings: Settings, request: Request) -> dict:
    return {
        "id": o.id,
        "status": o.status,
        "status_label": svc.STATUS_LABEL[o.status],
        "paid": o.is_paid,
        "payment_method": o.payment_method,
        "total_cents": o.total_cents,
        "total_stars": o.total_stars,
        "comment": o.comment,
        "created_at": o.created_at.isoformat(),
        "lines": [
            {"label": line_label(l.name, l.size, l.milk), "qty": l.qty, "unit_cents": l.unit_cents}
            for l in o.lines
        ],
        "demo_payment_allowed": _demo_pay_allowed(o, settings, request),
    }


def _demo_pay_allowed(o: Order, settings: Settings, request: Request) -> bool:
    if not settings.demo_payments:
        return False
    crypto_down = request.app.state.cryptopay is None or o.id in request.app.state.crypto_failed
    return crypto_down or is_demo_id(o.user_id)


async def _my_order(s, order_id: int, user: TelegramUser) -> Order:
    order = await svc.get_order(s, order_id)
    # Same 404 for "missing" and "someone else's", so IDs can't be probed.
    if order is None or order.user_id != user.id:
        raise HTTPException(404, "Order not found")
    return order


@router.get("/api/bootstrap")
async def bootstrap(request: Request, user: User, s: Session, settings: Conf):
    items = (await s.scalars(select(MenuItem).where(MenuItem.available).order_by(MenuItem.sort))).all()
    in_browser = is_demo_id(user.id)
    return {
        "shop": "BrewBook",
        "demo": True,
        "currency": settings.currency,
        "stars_per_usd": settings.stars_per_usd,
        "user": {"first_name": user.first_name},
        "browser_demo": in_browser,
        "payments": {
            "stars": request.app.state.bot is not None and not in_browser,
            "crypto": request.app.state.cryptopay is not None and not in_browser,
            "crypto_network": settings.cryptopay_network,
            "demo": settings.demo_payments,
        },
        "categories": [{"id": c, "name": n} for c, n in CATEGORIES],
        "sizes": [{"id": k, "name": v[0], "extra_cents": v[1]} for k, v in SIZES.items()],
        "milks": [{"id": k, "name": v[0], "extra_cents": v[1]} for k, v in MILKS.items()],
        "items": [
            {
                "id": m.id, "category": m.category, "name": m.name, "description": m.description,
                "price_cents": m.price_cents, "has_sizes": m.has_sizes, "has_milk": m.has_milk,
                "default_milk": m.default_milk, "icon": m.icon,
            }
            for m in items
        ],
    }


class CartLineIn(BaseModel):
    item_id: str = Field(max_length=40)
    qty: int
    size: str | None = Field(default=None, max_length=2)
    milk: str | None = Field(default=None, max_length=20)


class OrderIn(BaseModel):
    lines: list[CartLineIn] = Field(max_length=svc.MAX_LINES)
    comment: str = Field(default="", max_length=500)


@router.post("/api/orders")
async def create_order(body: OrderIn, request: Request, user: User, s: Session, settings: Conf):
    try:
        order = await svc.create_order(
            s, settings, user, [svc.CartLine(**l.model_dump()) for l in body.lines], body.comment
        )
    except svc.OrderError as e:
        raise HTTPException(400, str(e)) from e
    return order_json(order, settings, request)


@router.get("/api/orders")
async def my_orders(request: Request, user: User, s: Session, settings: Conf):
    rows = (await s.scalars(
        select(Order).where(Order.user_id == user.id, Order.status != Status.AWAITING_PAYMENT)
        .order_by(Order.id.desc()).limit(5)
    )).all()
    return [order_json(o, settings, request) for o in rows]


@router.get("/api/orders/{order_id}")
async def get_order(order_id: int, request: Request, user: User, s: Session, settings: Conf, check: bool = False):
    order = await _my_order(s, order_id, user)
    cp = request.app.state.cryptopay
    if check and cp and order.status == Status.AWAITING_PAYMENT and order.crypto_invoice_id:
        # Works without a public webhook too (e.g. local run behind a tunnel).
        try:
            inv = await cp.get_invoice(order.crypto_invoice_id)
        except CryptoPayError as e:
            log.warning("crypto check failed: %s", e)
            inv = None
        if inv and inv.get("status") == "paid":
            await _on_paid(request, s, order.id, "crypto", f"cryptopay:{order.crypto_invoice_id}")
            order = await _my_order(s, order_id, user)
    return order_json(order, settings, request)


def _require_unpaid(order: Order) -> None:
    if order.status != Status.AWAITING_PAYMENT:
        raise HTTPException(409, "This order is already paid")


@router.post("/api/orders/{order_id}/pay/stars")
async def pay_stars(order_id: int, request: Request, user: User, s: Session):
    order = await _my_order(s, order_id, user)
    _require_unpaid(order)
    bot = request.app.state.bot
    if bot is None or is_demo_id(user.id):
        raise HTTPException(400, "Telegram Stars work only inside Telegram")
    summary = ", ".join(f"{l.qty}× {l.name}" for l in order.lines)
    try:
        link = await bot.create_invoice_link(
            title=f"BrewBook order #{order.id}",
            description=(summary[:240] + "…") if len(summary) > 240 else summary,
            payload=f"order:{order.id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"Order #{order.id}", amount=order.total_stars)],
            provider_token="",
        )
    except TelegramAPIError as e:
        log.warning("createInvoiceLink failed: %s", e)
        raise HTTPException(502, "Telegram couldn't create the invoice. Please try again.") from e
    return {"invoice_link": link}


@router.post("/api/orders/{order_id}/pay/crypto")
async def pay_crypto(order_id: int, request: Request, user: User, s: Session, settings: Conf):
    order = await _my_order(s, order_id, user)
    _require_unpaid(order)
    cp = request.app.state.cryptopay
    if cp is None or is_demo_id(user.id):
        return _crypto_fallback(order, settings, request, "Crypto Pay is not configured on this server")
    if order.crypto_invoice_url:
        return {"mode": "crypto", "url": order.crypto_invoice_url}
    try:
        inv = await cp.create_invoice(
            amount_cents=order.total_cents, fiat=settings.currency, assets=settings.cryptopay_assets,
            description=f"BrewBook order #{order.id} (demo coffee shop)", payload=f"order:{order.id}",
        )
    except CryptoPayError as e:
        log.warning("crypto invoice failed: %s", e)
        request.app.state.crypto_failed.add(order.id)
        return _crypto_fallback(order, settings, request, "Crypto Pay is unavailable right now")
    order.crypto_invoice_id = inv["invoice_id"]
    order.crypto_invoice_url = inv.get("mini_app_invoice_url") or inv["bot_invoice_url"]
    order.payment_method = None
    await s.commit()
    return {"mode": "crypto", "url": order.crypto_invoice_url}


def _crypto_fallback(order: Order, settings: Settings, request: Request, reason: str) -> dict:
    return {"mode": "demo" if _demo_pay_allowed(order, settings, request) else "unavailable", "reason": reason}


@router.post("/api/orders/{order_id}/pay/demo")
async def pay_demo(order_id: int, request: Request, user: User, s: Session, settings: Conf):
    order = await _my_order(s, order_id, user)
    _require_unpaid(order)
    if not _demo_pay_allowed(order, settings, request):
        raise HTTPException(403, "Demo payments are turned off")
    await _on_paid(request, s, order.id, "demo", "demo")
    order = await _my_order(s, order_id, user)
    return order_json(order, settings, request)


@router.post("/cryptopay/webhook")
async def cryptopay_webhook(request: Request, s: Session, settings: Conf):
    body = await request.body()
    if not verify_webhook(settings.cryptopay_token, body, request.headers.get("crypto-pay-api-signature")):
        raise HTTPException(401, "bad signature")
    update = await request.json()
    if update.get("update_type") != "invoice_paid":
        return {"ok": True}
    inv = update.get("payload") or {}
    ref = str(inv.get("payload", ""))
    if ref.startswith("order:") and ref[6:].isdigit():
        order = await svc.get_order(s, int(ref[6:]))
        if order and order.crypto_invoice_id == inv.get("invoice_id"):
            await _on_paid(request, s, order.id, "crypto", f"cryptopay:{inv.get('invoice_id')}")
    return {"ok": True}


async def _on_paid(request: Request, s, order_id: int, method: str, ref: str) -> None:
    order = await svc.confirm_payment(s, order_id, method, ref)
    if order is not None:
        await request.app.state.notifier.order_paid(s, order)
