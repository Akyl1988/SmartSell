"""
Email service for sending notifications and documents.
"""

import html
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Company, Order

logger = get_logger(__name__)


class EmailService:
    """Service for sending emails"""

    def __init__(self):
        self.smtp_host = settings.SMTP_HOST
        self.smtp_port = settings.SMTP_PORT
        self.smtp_user = settings.SMTP_USER
        self.smtp_password = settings.SMTP_PASSWORD
        self.from_email = settings.SMTP_FROM_EMAIL

        # Setup Jinja2 for email templates
        template_dir = os.path.join(os.path.dirname(__file__), "../templates/email")
        self.jinja_env = Environment(
            loader=(FileSystemLoader(template_dir) if os.path.exists(template_dir) else None)
        )

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        cc: Optional[list[str]] = None,
        bcc: Optional[list[str]] = None,
    ) -> bool:
        """Send email via SMTP"""

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_email
            msg["To"] = to_email
            msg["Subject"] = subject

            if cc:
                msg["Cc"] = ", ".join(cc)

            # Add text body
            text_part = MIMEText(body, "plain", "utf-8")
            msg.attach(text_part)

            # Add HTML body if provided
            if html_body:
                html_part = MIMEText(html_body, "html", "utf-8")
                msg.attach(html_part)

            # Add attachments
            if attachments:
                for file_path in attachments:
                    if os.path.exists(file_path):
                        with open(file_path, "rb") as f:
                            attachment = MIMEApplication(f.read())
                            attachment.add_header(
                                "Content-Disposition",
                                "attachment",
                                filename=os.path.basename(file_path),
                            )
                            msg.attach(attachment)

            # Send email
            recipients = [to_email]
            if cc:
                recipients.extend(cc)
            if bcc:
                recipients.extend(bcc)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg, to_addrs=recipients)

            logger.info(f"Email sent successfully to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    async def send_order_confirmation(self, to_email: str, order: Order, company: Company) -> bool:
        """Send order confirmation email"""

        try:
            # Render email template
            if self.jinja_env:
                template = self.jinja_env.get_template("order_confirmation.html")
                html_body = template.render(order=order, company=company)
            else:
                html_body = self._generate_order_confirmation_html(order, company)

            # Plain text version
            text_body = f"""
            Подтверждение заказа №{order.order_number}

            Уважаемый {order.customer_name or "клиент"},

            Ваш заказ №{order.order_number} принят в обработку.

            Сумма заказа: {order.total_amount} {order.currency}
            Статус: {order.status}

            Спасибо за покупку!

            С уважением,
            Команда {company.name}
            """

            subject = f"Подтверждение заказа №{order.order_number}"

            return await self.send_email(
                to_email=to_email,
                subject=subject,
                body=text_body.strip(),
                html_body=html_body,
            )

        except Exception as e:
            logger.error(f"Failed to send order confirmation: {e}")
            return False

    async def send_order_email(
        self, to_email: str, subject: str, order: Order, pdf_attachment: str = None
    ) -> bool:
        """Send order details with optional PDF attachment"""

        text_body = f"""
        Детали заказа №{order.order_number}

        Номер заказа: {order.order_number}
        Сумма: {order.total_amount} {order.currency}
        Статус: {order.status}
        Клиент: {order.customer_name or "Не указан"}
        Телефон: {order.customer_phone or "Не указан"}

        Товары:
        """

        for item in order.items:
            text_body += f"- {item.name} x{item.quantity} = {item.total_price} {order.currency}\n"

        attachments = [pdf_attachment] if pdf_attachment else None

        return await self.send_email(
            to_email=to_email,
            subject=subject,
            body=text_body.strip(),
            attachments=attachments,
        )

    async def send_payment_notification(
        self, to_email: str, order: Order, payment_status: str, company: Company
    ) -> bool:
        """Send payment notification email"""

        status_messages = {
            "success": "успешно обработан",
            "failed": "не удался",
            "cancelled": "отменен",
            "refunded": "возвращен",
        }

        status_text = status_messages.get(payment_status, payment_status)

        subject = f"Уведомление о платеже - заказ №{order.order_number}"

        text_body = f"""
        Уведомление о платеже

        Уважаемый {order.customer_name or "клиент"},

        Платеж по заказу №{order.order_number} {status_text}.

        Сумма: {order.total_amount} {order.currency}
        Статус платежа: {status_text}

        {"Спасибо за покупку!" if payment_status == "success" else "По вопросам обращайтесь к менеджеру."}

        С уважением,
        Команда {company.name}
        """

        return await self.send_email(to_email=to_email, subject=subject, body=text_body.strip())

    async def send_bulk_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        html_body: Optional[str] = None,
    ) -> dict:
        """Send bulk email to multiple recipients"""

        results = {"total": len(recipients), "sent": 0, "failed": 0, "errors": []}

        for email in recipients:
            success = await self.send_email(
                to_email=email, subject=subject, body=body, html_body=html_body
            )

            if success:
                results["sent"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(email)

        logger.info(f"Bulk email results: {results['sent']}/{results['total']} sent successfully")
        return results

    async def send_subscription_notification(
        self, to_email: str, company_name: str, plan: str, expires_at: str
    ) -> bool:
        """Send subscription notification"""

        subject = f"Уведомление о подписке - {company_name}"

        text_body = f"""
        Уведомление о подписке

        Здравствуйте!

        Ваша подписка на тариф "{plan}" активна до {expires_at}.

        Для продления подписки войдите в личный кабинет.

        С уважением,
        Команда SmartSell
        """

        return await self.send_email(to_email=to_email, subject=subject, body=text_body.strip())

    async def send_low_stock_alert(
        self, to_email: str, products: list[dict], company_name: str
    ) -> bool:
        """Send low stock alert"""

        subject = f"Уведомление о низких остатках - {company_name}"

        text_body = """
        Уведомление о низких остатках

        На вашем складе заканчиваются следующие товары:

        """

        for product in products:
            text_body += f"- {product['name']} (SKU: {product['sku']}): {product['stock']} шт.\n"

        text_body += """

        Рекомендуем пополнить запасы.

        С уважением,
        Команда SmartSell
        """

        return await self.send_email(to_email=to_email, subject=subject, body=text_body.strip())

    def _generate_order_confirmation_html(self, order: Order, company: Company) -> str:
        """Generate HTML email template for order confirmation"""

        items_html = ""
        for item in order.items:
            items_html += f"""
            <tr>
                <td>{html.escape(item.name)}</td>
                <td>{item.quantity}</td>
                <td>{item.unit_price} {html.escape(order.currency)}</td>
                <td>{item.total_price} {html.escape(order.currency)}</td>
            </tr>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Подтверждение заказа</title>
        </head>
        <body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto;">
                <h1 style="color: #333;">Подтверждение заказа №{html.escape(order.order_number)}</h1>

                <p>Уважаемый {html.escape(order.customer_name or "клиент")},</p>

                <p>Ваш заказ №{html.escape(order.order_number)} принят в обработку.</p>

                <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                    <thead>
                        <tr style="background-color: #f5f5f5;">
                            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Товар</th>
                            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Количество</th>
                            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Цена</th>
                            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Сумма</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items_html}
                    </tbody>
                </table>

                <div style="text-align: right; font-size: 18px; font-weight: bold; margin-top: 20px;">
                    Итого: {order.total_amount} {html.escape(order.currency)}
                </div>

                <p style="margin-top: 30px;">Спасибо за покупку!</p>

                <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; color: #666;">
                    С уважением,<br>
                    Команда {html.escape(company.name)}
                </div>
            </div>
        </body>
        </html>
        """

        return html_content
