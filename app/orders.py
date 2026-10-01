"""Order rules: building an order from a cart, confirming payment, moving
through statuses, and the text of every Telegram message about an order."""
import html
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import TelegramUser, is_demo_id
from app.config import Settings
from app.menu import OptionError, line_label, money, to_stars, unit_price
from app.models import MenuItem, Order, OrderLine, Status, utcnow

log = logging.getLogger(__name__)

MAX_LINES = 30
MAX_QTY = 20
MAX_COMMENT = 200

STATUS_LABEL = {
    Status.AWAITING_PAYMENT: "Awaiting payment",
    Status.NEW: "New",
    Status.PREPARING: "Preparing",
    Status.READY: "Ready for pickup",
}
STATUS_ICON = {Status.AWAITING_PAYMENT: "⏳", Status.NEW: "🆕", Status.PREPARING: "👩‍🍳", Status.READY: "✅"}
# Allowed moves for baristas. No going backwards, so a mis-tap can't confuse the customer.
NEXT_STATUSES = {Status.NEW: (Status.PREPARING, Status.READY), Status.PREPARING: (Status.READY,)}
METHOD_LABEL = {"stars": "Telegram Stars", "crypto": "Crypto Pay (testnet)", "demo": "Demo payment"}


class OrderError(ValueError):
    pass


@dataclass
class CartLine:
    item_id: str
    qty: int
    size: str | None = None
    milk: str | None = None


def display_name(user: TelegramUser) -> str:
    name = user.first_name or "Guest"
    return f"{name} (@{user.username})" if user.username else name


async def create_order(
    s: AsyncSession, settings: Settings, user: TelegramUser, cart: list[CartLine], comment: str
) -> Order:
    if not cart:
        raise OrderError("Your cart is empty")
    if len(cart) > MAX_LINES:
        raise OrderError("Too many lines in one order")
    comment = " ".join((comment or "").split())
    if len(comment) > MAX_COMMENT:
        raise OrderError(f"Comment is longer than {MAX_COMMENT} characters")

    ids = {c.item_id for c in cart}
    items = {m.id: m for m in (await s.scalars(select(MenuItem).where(MenuItem.id.in_(ids)))).all()}
    order = Order(user_id=user.id, user_name=display_name(user), comment=comment, total_cents=0, total_stars=0)
    merged: dict[tuple, OrderLine] = {}
    for c in cart:
        item = items.get(c.item_id)
        if item is None or not item.available:
            raise OrderError(f"Item {c.item_id!r} is not on the menu")
        if not 1 <= c.qty <= MAX_QTY:
            raise OrderError(f"Quantity must be between 1 and {MAX_QTY}")
        try:
            cents, size, milk = unit_price(item, c.size, c.milk)
        except OptionError as e:
            raise OrderError(str(e)) from e
        key = (item.id, size, milk)
        if key in merged:
            merged[key].qty += c.qty
            if merged[key].qty > MAX_QTY:
                raise OrderError(f"Quantity must be between 1 and {MAX_QTY}")
        else:
            merged[key] = OrderLine(item_id=item.id, name=item.name, size=size, milk=milk, qty=c.qty, unit_cents=cents)
    order.lines = list(merged.values())
    order.total_cents = sum(l.qty * l.unit_cents for l in order.lines)
    order.total_stars = to_stars(order.total_cents, settings.stars_per_usd)
    s.add(order)
    await s.commit()
    return order


async def get_order(s: AsyncSession, order_id: int) -> Order | None:
    return await s.get(Order, order_id)


async def confirm_payment(s: AsyncSession, order_id: int, method: str, ref: str | None) -> Order | None:
    """Mark an order paid. Returns the order the first time, None if it was
    already paid (payment providers retry, so this must be idempotent).
    A conditional UPDATE makes two concurrent confirmations safe too."""
    res = await s.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == Status.AWAITING_PAYMENT)
        .values(status=Status.NEW, payment_method=method, payment_ref=ref, paid_at=utcnow())
    )
    await s.commit()
    if res.rowcount != 1:
        return None
    order = await s.get(Order, order_id, populate_existing=True)
    return order


async def change_status(s: AsyncSession, order_id: int, new_status: str) -> Order:
    order = await s.get(Order, order_id)
    if order is None:
        raise OrderError("Order not found")
    if new_status not in NEXT_STATUSES.get(order.status, ()):
        raise OrderError(f"Order is already {STATUS_LABEL[order.status].lower()}")
    order.status = new_status
    await s.commit()
    return order


async def recent_orders(s: AsyncSession, limit: int = 10) -> list[Order]:
    return list((await s.scalars(select(Order).order_by(Order.id.desc()).limit(limit))).all())


# ---------- message text ----------

def lines_text(order: Order, currency: str) -> str:
    return "\n".join(
        f"{l.qty}× {html.escape(line_label(l.name, l.size, l.milk))} — {money(l.qty * l.unit_cents, currency)}"
        for l in order.lines
    )


def paid_with(order: Order, currency: str) -> str:
    if order.payment_method == "stars":
        return f"⭐ {order.total_stars} Stars"
    return f"{money(order.total_cents, currency)} · {METHOD_LABEL.get(order.payment_method, '—')}"


def barista_text(order: Order, currency: str) -> str:
    parts = [
        f"{STATUS_ICON[order.status]} <b>Order #{order.id}</b> · {STATUS_LABEL[order.status]}",
        f"👤 {html.escape(order.user_name)}" + (" · browser demo" if is_demo_id(order.user_id) else ""),
        "",
        lines_text(order, currency),
    ]
    if order.comment:
        parts += ["", f"💬 <i>{html.escape(order.comment)}</i>"]
    parts += ["", f"Total: <b>{money(order.total_cents, currency)}</b> · paid with {paid_with(order, currency)}"]
    return "\n".join(parts)


def barista_keyboard(order: Order) -> InlineKeyboardMarkup | None:
    buttons = {
        Status.PREPARING: InlineKeyboardButton(text="👩‍🍳 Preparing", callback_data=f"st:{order.id}:{Status.PREPARING}"),
        Status.READY: InlineKeyboardButton(text="✅ Ready", callback_data=f"st:{order.id}:{Status.READY}"),
    }
    row = [buttons[s] for s in NEXT_STATUSES.get(order.status, ())]
    return InlineKeyboardMarkup(inline_keyboard=[row]) if row else None


def customer_text(order: Order, currency: str) -> str:
    if order.status == Status.NEW:
        return (
            f"☕ <b>Thanks! Order #{order.id} is paid.</b>\n\n{lines_text(order, currency)}\n\n"
            f"Paid with {paid_with(order, currency)}. We'll message you as it moves along."
        )
    if order.status == Status.PREPARING:
        return f"👩‍🍳 Order #{order.id} is being prepared now."
    if order.status == Status.READY:
        return f"✅ Order #{order.id} is ready! Pick it up at the counter and say your name."
    return f"Order #{order.id}: {STATUS_LABEL[order.status]}"


def owner_list_text(orders: list[Order], currency: str) -> str:
    if not orders:
        return "No orders yet."
    rows = ["<b>Recent orders</b>", ""]
    for o in orders:
        when = o.created_at.strftime("%d %b %H:%M")
        items = sum(l.qty for l in o.lines)
        rows.append(
            f"{STATUS_ICON[o.status]} <b>#{o.id}</b> · {when} · {html.escape(o.user_name)}\n"
            f"    {items} item{'s' if items != 1 else ''} · {money(o.total_cents, currency)}"
            f" · {STATUS_LABEL[o.status]}" + (f" · {o.payment_method}" if o.payment_method else "")
        )
    return "\n".join(rows)


# ---------- notifications ----------

class Notifier:
    """Sends order messages through the bot. Every send is best-effort: a
    customer who blocked the bot must never break payment handling."""

    def __init__(self, bot: Bot | None, settings: Settings):
        self.bot = bot
        self.settings = settings

    async def _send(self, chat_id: int, text: str, markup=None):
        if self.bot is None or is_demo_id(chat_id):
            return None
        try:
            return await self.bot.send_message(chat_id, text, reply_markup=markup)
        except TelegramAPIError as e:
            log.warning("send to %s failed: %s", chat_id, e)
            return None

    async def order_paid(self, s: AsyncSession, order: Order) -> None:
        cur = self.settings.currency
        if self.settings.barista_chat_id:
            msg = await self._send(self.settings.barista_chat_id, barista_text(order, cur), barista_keyboard(order))
            if msg is not None:
                order.barista_message_id = msg.message_id
                await s.commit()
        await self._send(order.user_id, customer_text(order, cur))

    async def status_changed(self, order: Order) -> None:
        cur = self.settings.currency
        if self.bot and self.settings.barista_chat_id and order.barista_message_id:
            try:
                await self.bot.edit_message_text(
                    text=barista_text(order, cur), chat_id=self.settings.barista_chat_id,
                    message_id=order.barista_message_id, reply_markup=barista_keyboard(order),
                )
            except TelegramAPIError as e:
                log.warning("edit barista message failed: %s", e)
        await self._send(order.user_id, customer_text(order, cur))
