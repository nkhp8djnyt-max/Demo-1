"""Bot side: /start with the Mini App button, the Telegram Stars payment
flow, barista status buttons and the owner's /orders list."""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, PreCheckoutQuery, WebAppInfo,
)

from app import orders as svc
from app.config import Settings
from app.models import Status

log = logging.getLogger(__name__)


def _order_id(payload: str) -> int | None:
    if payload.startswith("order:") and payload[6:].isdigit():
        return int(payload[6:])
    return None


async def start(message: Message, settings: Settings) -> None:
    name = html.escape(message.from_user.first_name if message.from_user else "there")
    text = (
        f"Hi {name}! ☕ Welcome to <b>BrewBook</b>, a small neighbourhood coffee bar.\n\n"
        "Tap <b>Open menu</b>, pick your drinks and pay right here in Telegram. "
        "We'll message you when your order is ready.\n\n"
        "<i>Demo project: a fictional coffee shop, no real coffee will be made.</i>"
    )
    markup = None
    if settings.webapp_url:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="☕ Open menu", web_app=WebAppInfo(url=settings.webapp_url))
        ]])
    await message.answer(text, reply_markup=markup)


async def list_orders(message: Message, settings: Settings, sessionmaker) -> None:
    user_id = message.from_user.id if message.from_user else None
    in_barista_chat = settings.barista_chat_id is not None and message.chat.id == settings.barista_chat_id
    if user_id not in settings.owner_id_set and not in_barista_chat:
        await message.answer("This command is for the shop owner.")
        return
    async with sessionmaker() as s:
        orders = await svc.recent_orders(s, 10)
        await message.answer(svc.owner_list_text(orders, settings.currency))


async def pre_checkout(query: PreCheckoutQuery, sessionmaker) -> None:
    """Telegram asks us to confirm before charging. Answer within 10 seconds."""
    order_id = _order_id(query.invoice_payload)
    error = None
    async with sessionmaker() as s:
        order = await svc.get_order(s, order_id) if order_id else None
        if order is None:
            error = "This order no longer exists. Please start a new one."
        elif order.user_id != query.from_user.id:
            error = "This invoice belongs to another customer."
        elif order.status != Status.AWAITING_PAYMENT:
            error = "This order is already paid."
        elif query.currency != "XTR" or query.total_amount != order.total_stars:
            error = "The price changed. Please reopen the menu and try again."
    if error:
        await query.answer(ok=False, error_message=error)
    else:
        await query.answer(ok=True)


async def successful_payment(message: Message, sessionmaker, notifier: svc.Notifier) -> None:
    sp = message.successful_payment
    order_id = _order_id(sp.invoice_payload)
    if order_id is None:
        log.error("payment with unknown payload %r", sp.invoice_payload)
        return
    async with sessionmaker() as s:
        order = await svc.confirm_payment(s, order_id, "stars", sp.telegram_payment_charge_id)
        if order is not None:
            await notifier.order_paid(s, order)


async def change_status(cb: CallbackQuery, settings: Settings, sessionmaker, notifier: svc.Notifier) -> None:
    chat_id = cb.message.chat.id if cb.message else None
    if settings.barista_chat_id is None or chat_id != settings.barista_chat_id:
        await cb.answer("Only the barista chat can change orders.", show_alert=True)
        return
    try:
        _, order_id, status = cb.data.split(":")
        async with sessionmaker() as s:
            order = await svc.change_status(s, int(order_id), status)
    except (ValueError, svc.OrderError) as e:
        await cb.answer(str(e) if isinstance(e, svc.OrderError) else "Bad button", show_alert=True)
        return
    await notifier.status_changed(order)
    who = cb.from_user.first_name if cb.from_user else "Barista"
    await cb.answer(f"#{order.id} → {svc.STATUS_LABEL[order.status]} ({who})")


def create_router() -> Router:
    """A fresh router per Dispatcher (aiogram routers can only be attached once)."""
    router = Router(name="brewbook")
    router.message.register(start, CommandStart())
    router.message.register(list_orders, Command("orders"))
    router.pre_checkout_query.register(pre_checkout)
    router.message.register(successful_payment, F.successful_payment)
    router.callback_query.register(change_status, F.data.startswith("st:"))
    return router
