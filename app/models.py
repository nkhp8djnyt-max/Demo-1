from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Status:
    AWAITING_PAYMENT = "awaiting_payment"
    NEW = "new"
    PREPARING = "preparing"
    READY = "ready"
    PAID = (NEW, PREPARING, READY)


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # slug, e.g. "flat-white"
    category: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(200))
    price_cents: Mapped[int] = mapped_column(Integer)
    has_sizes: Mapped[bool] = mapped_column(Boolean, default=False)
    has_milk: Mapped[bool] = mapped_column(Boolean, default=False)
    default_milk: Mapped[str | None] = mapped_column(String(20), nullable=True)
    icon: Mapped[str] = mapped_column(String(20))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default=Status.AWAITING_PAYMENT, index=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    total_cents: Mapped[int] = mapped_column(Integer)
    total_stars: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[str | None] = mapped_column(String(10), nullable=True)  # stars | crypto | demo
    payment_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    crypto_invoice_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    crypto_invoice_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    barista_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin", order_by="OrderLine.id"
    )

    @property
    def is_paid(self) -> bool:
        return self.status in Status.PAID


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(60))  # snapshot, menu can change later
    size: Mapped[str | None] = mapped_column(String(2), nullable=True)
    milk: Mapped[str | None] = mapped_column(String(20), nullable=True)
    qty: Mapped[int] = mapped_column(Integer)
    unit_cents: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="lines")
