"""
Mobizon SMS service integration for OTP delivery.
"""

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MobizonService:
    """Service for Mobizon SMS API integration"""

    def __init__(self):
        self.api_key = settings.MOBIZON_API_KEY
        self.base_url = settings.MOBIZON_API_URL

    async def send_sms(self, phone: str, message: str, sender: str = "SmartSell") -> bool:
        """Send SMS via Mobizon API"""

        # Clean phone number (remove + and spaces)
        phone = phone.replace("+", "").replace(" ", "").replace("-", "")

        # Validate phone number format (Kazakhstan numbers)
        if not phone.startswith("7") or len(phone) != 11:
            logger.error(f"Invalid phone number format: {phone}")
            return False

        payload = {"recipient": phone, "text": message, "from": sender}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/service/message/sendsmsmessage",
                    params={"apikey": self.api_key},
                    json=payload,
                )

                if response.status_code == 200:
                    data = response.json()

                    if data.get("code") == 0:  # Success code
                        logger.info(f"SMS sent successfully to {phone}")
                        return True
                    else:
                        logger.error(f"Mobizon API error: {data.get('message')}")
                        return False
                else:
                    logger.error(f"Mobizon HTTP error: {response.status_code}")
                    return False

        except httpx.RequestError as e:
            logger.error(f"Mobizon request error: {e}")
            return False
        except Exception as e:
            logger.error(f"Mobizon unexpected error: {e}")
            return False

    async def send_otp(self, phone: str, code: str) -> bool:
        """Send OTP code via SMS"""

        message = f"Ваш код подтверждения SmartSell: {code}. Код действителен 5 минут."
        return await self.send_sms(phone, message)

    async def send_password_reset(self, phone: str, code: str) -> bool:
        """Send password reset OTP code"""

        message = f"Код для сброса пароля SmartSell: {code}. Код действителен 5 минут."
        return await self.send_sms(phone, message)

    async def send_order_notification(self, phone: str, order_number: str, status: str) -> bool:
        """Send order status notification"""

        status_messages = {
            "confirmed": f"Ваш заказ {order_number} подтвержден и готовится к отправке.",
            "shipped": f"Ваш заказ {order_number} отправлен. Ожидайте доставку.",
            "delivered": f"Ваш заказ {order_number} доставлен. Спасибо за покупку!",
            "cancelled": f"Ваш заказ {order_number} отменен. По вопросам обращайтесь к менеджеру.",
        }

        message = status_messages.get(status, f"Статус заказа {order_number} изменен на: {status}")
        return await self.send_sms(phone, message)

    async def send_payment_notification(
        self, phone: str, order_number: str, amount: float, status: str
    ) -> bool:
        """Send payment notification"""

        if status == "success":
            message = f"Оплата заказа {order_number} на сумму {amount} ₸ прошла успешно."
        elif status == "failed":
            message = f"Оплата заказа {order_number} не удалась. Попробуйте еще раз."
        else:
            message = f"Статус платежа заказа {order_number}: {status}"

        return await self.send_sms(phone, message)

    async def get_balance(self) -> Optional[dict[str, Any]]:
        """Get account balance"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/service/user/getownbalance",
                    params={"apikey": self.api_key},
                )

                if response.status_code == 200:
                    data = response.json()

                    if data.get("code") == 0:
                        logger.info("Retrieved Mobizon balance")
                        return data.get("data")
                    else:
                        logger.error(f"Mobizon balance error: {data.get('message')}")
                        return None
                else:
                    logger.error(f"Mobizon balance HTTP error: {response.status_code}")
                    return None

        except Exception as e:
            logger.error(f"Mobizon balance error: {e}")
            return None

    async def get_message_status(self, message_id: str) -> Optional[dict[str, Any]]:
        """Get message delivery status"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/service/message/getmessagestatus",
                    params={"apikey": self.api_key, "messageId": message_id},
                )

                if response.status_code == 200:
                    data = response.json()

                    if data.get("code") == 0:
                        logger.info(f"Retrieved message status for {message_id}")
                        return data.get("data")
                    else:
                        logger.error(f"Mobizon message status error: {data.get('message')}")
                        return None
                else:
                    logger.error(f"Mobizon message status HTTP error: {response.status_code}")
                    return None

        except Exception as e:
            logger.error(f"Mobizon message status error: {e}")
            return None

    async def send_bulk_sms(
        self, recipients: list[str], message: str, sender: str = "SmartSell"
    ) -> dict[str, Any]:
        """Send bulk SMS to multiple recipients"""

        results = {"total": len(recipients), "sent": 0, "failed": 0, "errors": []}

        for phone in recipients:
            success = await self.send_sms(phone, message, sender)

            if success:
                results["sent"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(phone)

        logger.info(f"Bulk SMS results: {results['sent']}/{results['total']} sent successfully")
        return results

    def validate_phone_number(self, phone: str) -> bool:
        """Validate Kazakhstan phone number format"""

        # Clean phone number
        phone = phone.replace("+", "").replace(" ", "").replace("-", "")

        # Check format: 7XXXXXXXXXX (Kazakhstan)
        return phone.startswith("7") and len(phone) == 11 and phone.isdigit()

    def format_phone_number(self, phone: str) -> str:
        """Format phone number for Mobizon API"""

        # Remove all non-digit characters except +
        phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

        # Remove + if present
        if phone.startswith("+"):
            phone = phone[1:]

        # Add country code if missing
        if phone.startswith("7") and len(phone) == 11:
            return phone
        elif phone.startswith("77") and len(phone) == 11:
            return "7" + phone[2:]  # Remove duplicate 7
        elif len(phone) == 10:
            return "7" + phone  # Add country code

        return phone
