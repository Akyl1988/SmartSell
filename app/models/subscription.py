from __future__ import annotations

from datetime import date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, Text, Date, ForeignKey, TIMESTAMP, text

from app.core.db import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            name="fk_subscriptions_company",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    plan: Mapped[str] = mapped_column(Text, nullable=False)

    # ВАЖНО: в аннотации — Python-тип date, в колонке — SA Date
    next_billing_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # связи (если их ещё нет)
    company = relationship("Company", back_populates="subscriptions", lazy="selectin")
    payments = relationship("BillingPayment", back_populates="subscription", lazy="selectin")


# Если в Company/ BillingPayment ещё нет back_populates — добавьте:
# в app/models/company.py:
# subscriptions = relationship("Subscription", back_populates="company", lazy="selectin")
#
# в app/models/billing_payment.py:
# subscription = relationship("Subscription", back_populates="payments", lazy="selectin")
