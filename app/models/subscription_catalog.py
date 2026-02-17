from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.types import JSONBCompat, TrimmedString


class Plan(BaseModel):
    __tablename__ = "plans"
    __allow_unmapped__ = True

    code: Mapped[str] = mapped_column(TrimmedString(32, lowercase=True), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(TrimmedString(128), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 0), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trial_days_default: Mapped[int] = mapped_column(Integer, nullable=False, default=14)

    features: Mapped[list[PlanFeature]] = relationship(
        "PlanFeature",
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class Feature(BaseModel):
    __tablename__ = "features"
    __allow_unmapped__ = True

    code: Mapped[str] = mapped_column(TrimmedString(64, lowercase=True), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(TrimmedString(128), nullable=False)
    description: Mapped[str | None] = mapped_column(TrimmedString(512))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    plans: Mapped[list[PlanFeature]] = relationship(
        "PlanFeature",
        back_populates="feature",
        cascade="all, delete-orphan",
    )


class PlanFeature(BaseModel):
    __tablename__ = "plan_features"
    __allow_unmapped__ = True

    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_id: Mapped[int] = mapped_column(ForeignKey("features.id", ondelete="CASCADE"), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    limits_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompat, nullable=True)

    plan: Mapped[Plan] = relationship("Plan", back_populates="features")
    feature: Mapped[Feature] = relationship("Feature", back_populates="plans")

    __table_args__ = (
        UniqueConstraint("plan_id", "feature_id", name="uq_plan_features_plan_feature"),
        Index("ix_plan_features_plan_feature", "plan_id", "feature_id"),
    )


class FeatureUsage(BaseModel):
    __tablename__ = "feature_usage"
    __allow_unmapped__ = True

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_id: Mapped[int] = mapped_column(ForeignKey("features.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    feature: Mapped[Feature] = relationship("Feature")

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "feature_id",
            "subscription_id",
            name="uq_feature_usage_company_feature_subscription",
        ),
        Index("ix_feature_usage_company_feature", "company_id", "feature_id"),
    )
