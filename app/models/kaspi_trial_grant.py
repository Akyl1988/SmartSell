from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text

from app.models.base import Base


class KaspiTrialGrant(Base):
    __tablename__ = "kaspi_trial_grants"

    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False, server_default="kaspi")
    merchant_uid = Column(String(128), nullable=False)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id = Column(ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True)
    granted_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    trial_ends_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(32), nullable=False, server_default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint("provider", "merchant_uid", name="uq_kaspi_trial_grants_provider_merchant"),
        Index("ix_kaspi_trial_grants_merchant_uid", "merchant_uid"),
    )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"<KaspiTrialGrant(id={self.id}, provider={self.provider}, "
            f"merchant_uid={self.merchant_uid}, company_id={self.company_id})>"
        )
