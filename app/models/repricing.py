"""Repricing rules and run logs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class RepricingRule(BaseModel):
    __tablename__ = "repricing_rules"

    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    scope = Column(SAJSON, nullable=True)
    scope_type = Column(String(32), nullable=True)
    scope_value = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=False, nullable=False)
    min_price = Column(Numeric(14, 2), nullable=True)
    max_price = Column(Numeric(14, 2), nullable=True)
    step = Column(Numeric(14, 2), nullable=True)
    undercut = Column(Numeric(14, 2), nullable=True)
    rounding_mode = Column(String(32), nullable=True)
    cooldown_seconds = Column(Integer, nullable=True)
    max_delta_percent = Column(Numeric(5, 2), nullable=True)

    company = relationship("Company")
    runs = relationship("RepricingRun", back_populates="rule", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_repricing_rules_company_active", "company_id", "is_active"),)


class RepricingRun(BaseModel):
    __tablename__ = "repricing_runs"

    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id = Column(ForeignKey("repricing_rules.id", ondelete="CASCADE"), nullable=True, index=True)

    status = Column(String(32), nullable=False, default="running")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    stats = Column(SAJSON, nullable=True)

    processed = Column(Integer, nullable=True)
    changed = Column(Integer, nullable=True)
    failed = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)

    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)

    requested_by_user_id = Column(Integer, nullable=True)
    triggered_by_user_id = Column(Integer, nullable=True)
    request_id = Column(String(64), nullable=True)

    rule = relationship("RepricingRule", back_populates="runs")
    company = relationship("Company")
    diffs = relationship("RepricingDiff", back_populates="run", cascade="all, delete-orphan")
    items = relationship("RepricingRunItem", back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_repricing_runs_company_rule", "company_id", "rule_id"),)


class RepricingDiff(BaseModel):
    __tablename__ = "repricing_diffs"

    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id = Column(ForeignKey("repricing_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(ForeignKey("repricing_runs.id", ondelete="CASCADE"), nullable=False, index=True)

    product_id = Column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    sku = Column(String(100), nullable=True)
    old_price = Column(Numeric(14, 2), nullable=True)
    new_price = Column(Numeric(14, 2), nullable=True)
    reason = Column(String(255), nullable=True)
    meta = Column(SAJSON, nullable=True)

    run = relationship("RepricingRun", back_populates="diffs")
    rule = relationship("RepricingRule")
    product = relationship("Product")
    company = relationship("Company")

    __table_args__ = (Index("ix_repricing_diffs_company_run", "company_id", "run_id"),)


class RepricingRunItem(BaseModel):
    __tablename__ = "repricing_run_items"

    run_id = Column(ForeignKey("repricing_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    old_price = Column(Numeric(14, 2), nullable=True)
    new_price = Column(Numeric(14, 2), nullable=True)
    reason = Column(String(255), nullable=True)
    status = Column(String(32), nullable=True)
    error = Column(Text, nullable=True)

    run = relationship("RepricingRun", back_populates="items")
    product = relationship("Product")

    __table_args__ = (
        Index("ix_repricing_run_items_run", "run_id"),
        Index("ix_repricing_run_items_product", "product_id"),
    )


def repricing_run_stats(
    *,
    processed: int = 0,
    changed: int = 0,
    skipped: int = 0,
    errors: int = 0,
) -> dict[str, Any]:
    return {
        "processed": int(processed or 0),
        "changed": int(changed or 0),
        "skipped": int(skipped or 0),
        "errors": int(errors or 0),
        "timestamp": datetime.utcnow().isoformat(),
    }
