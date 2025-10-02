"""
Background tasks for SmartSell3 using asyncio.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_maker
from app.core.logging import get_logger
from app.models import AuditLog, Company, OtpAttempt, Product, ProductStock, User
from app.services import EmailService, KaspiService
from app.utils.idempotency import cleanup_idempotency_records

logger = get_logger(__name__)


class TaskManager:
    """Manager for background tasks"""

    def __init__(self):
        self.running = False
        self.tasks = []

    async def start(self):
        """Start background tasks"""
        if self.running:
            return

        self.running = True
        logger.info("Starting background tasks...")

        # Start periodic tasks
        self.tasks = [
            asyncio.create_task(self._sync_kaspi_orders_task()),
            asyncio.create_task(self._cleanup_expired_data_task()),
            asyncio.create_task(self._send_notifications_task()),
            asyncio.create_task(self._update_stock_levels_task()),
            asyncio.create_task(self._process_scheduled_campaigns_task()),
        ]

        logger.info("Background tasks started")

    async def stop(self):
        """Stop background tasks"""
        if not self.running:
            return

        self.running = False
        logger.info("Stopping background tasks...")

        # Cancel all tasks
        for task in self.tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self.tasks, return_exceptions=True)

        logger.info("Background tasks stopped")

    async def _sync_kaspi_orders_task(self):
        """Periodic task to sync orders from Kaspi"""

        while self.running:
            try:
                await asyncio.sleep(900)  # Run every 15 minutes

                async with async_session_maker() as db:
                    # Get companies with Kaspi integration
                    result = await db.execute(
                        select(Company).where(
                            and_(
                                Company.kaspi_api_key.isnot(None),
                                Company.is_active,
                            )
                        )
                    )
                    companies = result.scalars().all()

                    for company in companies:
                        try:
                            kaspi = KaspiService(company.kaspi_api_key)
                            result = await kaspi.sync_orders(company.id, db)

                            if result["created"] > 0 or result["updated"] > 0:
                                logger.info(
                                    f"Kaspi sync for company {company.id}: "
                                    f"created={result['created']}, updated={result['updated']}"
                                )

                        except Exception as e:
                            logger.error(f"Kaspi sync error for company {company.id}: {e}")

            except Exception as e:
                logger.error(f"Kaspi sync task error: {e}")

    async def _cleanup_expired_data_task(self):
        """Periodic task to clean up expired data"""

        while self.running:
            try:
                await asyncio.sleep(3600)  # Run every hour

                async with async_session_maker() as db:
                    # Clean up expired OTP attempts
                    result = await db.execute(
                        select(OtpAttempt).where(OtpAttempt.expires_at < datetime.utcnow())
                    )
                    expired_otps = result.scalars().all()

                    for otp in expired_otps:
                        await db.delete(otp)

                    await db.commit()

                    if expired_otps:
                        logger.info(f"Cleaned up {len(expired_otps)} expired OTP attempts")

                    # Clean up old audit logs (keep 90 days)
                    cutoff_date = datetime.utcnow() - timedelta(days=90)
                    result = await db.execute(
                        select(AuditLog).where(AuditLog.created_at < cutoff_date)
                    )
                    old_logs = result.scalars().all()

                    for log in old_logs:
                        await db.delete(log)

                    await db.commit()

                    if old_logs:
                        logger.info(f"Cleaned up {len(old_logs)} old audit logs")

                # Clean up idempotency records
                cleaned = await cleanup_idempotency_records()
                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} expired idempotency records")

            except Exception as e:
                logger.error(f"Cleanup task error: {e}")

    async def _send_notifications_task(self):
        """Periodic task to send notifications"""

        while self.running:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes

                async with async_session_maker() as db:
                    # Send low stock alerts
                    await self._send_low_stock_alerts(db)

                    # Send subscription expiry warnings
                    await self._send_subscription_expiry_warnings(db)

            except Exception as e:
                logger.error(f"Notifications task error: {e}")

    async def _update_stock_levels_task(self):
        """Periodic task to update stock levels in external systems"""

        while self.running:
            try:
                await asyncio.sleep(1800)  # Run every 30 minutes

                async with async_session_maker() as db:
                    # Get companies with Kaspi integration
                    result = await db.execute(
                        select(Company).where(
                            and_(
                                Company.kaspi_api_key.isnot(None),
                                Company.is_active,
                            )
                        )
                    )
                    companies = result.scalars().all()

                    for company in companies:
                        try:
                            await self._update_kaspi_stock_levels(company, db)
                        except Exception as e:
                            logger.error(f"Stock update error for company {company.id}: {e}")

            except Exception as e:
                logger.error(f"Stock update task error: {e}")

    async def _process_scheduled_campaigns_task(self):
        """Process scheduled marketing campaigns"""

        while self.running:
            try:
                await asyncio.sleep(60)  # Run every minute

                # TODO: Implement campaign processing
                # This would handle scheduled WhatsApp/Email campaigns

            except Exception as e:
                logger.error(f"Campaigns task error: {e}")

    async def _send_low_stock_alerts(self, db: AsyncSession):
        """Send low stock alerts to company admins"""

        try:
            # Get products with low stock
            result = await db.execute(
                select(ProductStock)
                .join(Product)
                .join(Company)
                .where(
                    and_(
                        ProductStock.quantity <= ProductStock.min_quantity,
                        ProductStock.min_quantity > 0,
                        Product.is_active,
                        Company.is_active,
                    )
                )
            )
            low_stock_items = result.scalars().all()

            if not low_stock_items:
                return

            # Group by company
            companies_stock = {}
            for stock in low_stock_items:
                company_id = stock.product.company_id
                if company_id not in companies_stock:
                    companies_stock[company_id] = []

                companies_stock[company_id].append(
                    {
                        "name": stock.product.name,
                        "sku": stock.product.sku,
                        "stock": stock.quantity,
                        "min_stock": stock.min_quantity,
                    }
                )

            # Send alerts
            email_service = EmailService()

            for company_id, products in companies_stock.items():
                try:
                    # Get company admins
                    result = await db.execute(
                        select(User).where(
                            and_(
                                User.company_id == company_id,
                                User.role == "admin",
                                User.is_active,
                                User.email.isnot(None),
                            )
                        )
                    )
                    admins = result.scalars().all()

                    # Get company info
                    result = await db.execute(select(Company).where(Company.id == company_id))
                    company = result.scalar_one_or_none()

                    if company and admins:
                        for admin in admins:
                            await email_service.send_low_stock_alert(
                                to_email=admin.email,
                                products=products,
                                company_name=company.name,
                            )

                            logger.info(f"Low stock alert sent to {admin.email}")

                except Exception as e:
                    logger.error(f"Low stock alert error for company {company_id}: {e}")

        except Exception as e:
            logger.error(f"Low stock alerts error: {e}")

    async def _send_subscription_expiry_warnings(self, db: AsyncSession):
        """Send subscription expiry warnings"""

        try:
            # Get companies with subscriptions expiring in 7 days
            warning_date = datetime.utcnow() + timedelta(days=7)

            result = await db.execute(
                select(Company).where(
                    and_(
                        Company.subscription_expires_at.isnot(None),
                        Company.subscription_expires_at <= warning_date.isoformat(),
                        Company.is_active,
                    )
                )
            )
            expiring_companies = result.scalars().all()

            email_service = EmailService()

            for company in expiring_companies:
                try:
                    # Get company admins
                    result = await db.execute(
                        select(User).where(
                            and_(
                                User.company_id == company.id,
                                User.role == "admin",
                                User.is_active,
                                User.email.isnot(None),
                            )
                        )
                    )
                    admins = result.scalars().all()

                    for admin in admins:
                        await email_service.send_subscription_notification(
                            to_email=admin.email,
                            company_name=company.name,
                            plan=company.subscription_plan,
                            expires_at=company.subscription_expires_at,
                        )

                        logger.info(f"Subscription warning sent to {admin.email}")

                except Exception as e:
                    logger.error(f"Subscription warning error for company {company.id}: {e}")

        except Exception as e:
            logger.error(f"Subscription warnings error: {e}")

    async def _update_kaspi_stock_levels(self, company: Company, db: AsyncSession):
        """Update stock levels in Kaspi"""

        try:
            kaspi = KaspiService(company.kaspi_api_key)

            # Get products with Kaspi integration
            result = await db.execute(
                select(Product).where(
                    and_(
                        Product.company_id == company.id,
                        Product.kaspi_product_id.isnot(None),
                        Product.is_active,
                    )
                )
            )
            products = result.scalars().all()

            updated_count = 0
            for product in products:
                try:
                    # Calculate total available stock
                    total_stock = product.available_stock

                    # Update in Kaspi if different
                    if total_stock != product.kaspi_availability:
                        success = await kaspi.update_product_availability(
                            product.kaspi_product_id, total_stock
                        )

                        if success:
                            product.kaspi_availability = total_stock
                            updated_count += 1

                except Exception as e:
                    logger.error(f"Kaspi stock update error for product {product.id}: {e}")

            if updated_count > 0:
                await db.commit()
                logger.info(f"Updated {updated_count} products in Kaspi for company {company.id}")

        except Exception as e:
            logger.error(f"Kaspi stock update error for company {company.id}: {e}")


# Global task manager
task_manager = TaskManager()


async def start_background_tasks():
    """Start background tasks"""
    await task_manager.start()


async def stop_background_tasks():
    """Stop background tasks"""
    await task_manager.stop()


# Individual task functions that can be called manually
async def sync_company_orders(company_id: int) -> dict[str, Any]:
    """Manually sync orders for specific company"""

    async with async_session_maker() as db:
        result = await db.execute(select(Company).where(Company.id == company_id))
        company = result.scalar_one_or_none()

        if not company or not company.kaspi_api_key:
            raise Exception("Company not found or Kaspi not configured")

        kaspi = KaspiService(company.kaspi_api_key)
        return await kaspi.sync_orders(company.id, db)


async def send_test_notification(user_id: int, message: str) -> bool:
    """Send test notification to user"""

    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            return False

        if user.email:
            email_service = EmailService()
            return await email_service.send_email(
                to_email=user.email, subject="Test Notification", body=message
            )

        return False


async def cleanup_old_data(days: int = 90) -> dict[str, int]:
    """Manually cleanup old data"""

    results = {"otp_attempts": 0, "audit_logs": 0, "idempotency_records": 0}

    async with async_session_maker() as db:
        # Clean up expired OTP attempts
        result = await db.execute(
            select(OtpAttempt).where(OtpAttempt.expires_at < datetime.utcnow())
        )
        expired_otps = result.scalars().all()

        for otp in expired_otps:
            await db.delete(otp)

        results["otp_attempts"] = len(expired_otps)

        # Clean up old audit logs
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        result = await db.execute(select(AuditLog).where(AuditLog.created_at < cutoff_date))
        old_logs = result.scalars().all()

        for log in old_logs:
            await db.delete(log)

        results["audit_logs"] = len(old_logs)

        await db.commit()

    # Clean up idempotency records
    results["idempotency_records"] = await cleanup_idempotency_records()

    return results
