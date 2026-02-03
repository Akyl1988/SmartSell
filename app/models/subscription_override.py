from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

from app.models.base import Base


class SubscriptionOverride(Base):
    __tablename__ = "subscription_overrides"

    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False, server_default="kaspi")
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_uid = Column(String(128), nullable=False)
    active_until = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    created_by_user_id = Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "company_id", "provider", "merchant_uid", name="uq_subscription_overrides_company_provider_merchant"
        ),
        Index("ix_subscription_overrides_provider_merchant", "provider", "merchant_uid"),
    )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"<SubscriptionOverride(id={self.id}, company_id={self.company_id}, "
            f"provider={self.provider}, merchant_uid={self.merchant_uid})>"
        )
