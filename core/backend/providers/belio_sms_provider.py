import logging
from typing import Dict, List

import requests

from core.backend.providers.base_provider import BaseProvider
from core.models import Notification

logger = logging.getLogger(__name__)


class BelioSMSProvider(BaseProvider):
    def validate_config(self) -> bool:
        """
        Ensures required configuration values are present.
        """
        required_keys = ["api_key", "cookie", "url", "sms_service_id", "callback_url"]
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            self.set_last_exception("Missing config keys: %s" % ", ".join(missing_keys))
            logger.error("BelioSMSProvider - %s", self.last_exception)
            return False
        return True

    def send(self, recipients: List[str], content: Dict[str, str]) -> str:
        """
        Sends an SMS to one or more recipients.

        :param recipients: List of phone number(s).
        :param content: Dict with 'body' key containing the message.
        :return: ConfirmationPending status if sms is queued successfully else Failed status.
        """
        try:
            message = content.get("body", "")
            unique_identifier = content.get("unique_identifier", "")

            url = self.config.get("url")
            headers = {
                "Authorization": self.config.get("api_key"),
                "Cookie": self.config.get("cookie"),
                "Content-Type": "application/json"
            }

            data = {
                "smsServiceId": self.config.get("sms_service_id"),
                "message": message,
                "addresses": recipients,
                "deliveryReportRequest": {
                    "correlator": unique_identifier,
                    "callbackUrl": self.config.get("callback_url")
                }
            }

            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()

            return Notification.Status.CONFIRMATION_PENDING

        except Exception as ex:
            logger.exception("BelioSMSProvider - send exception: %s", ex)
            self.set_last_exception(ex)
            return Notification.Status.FAILED

    def handle_callback(self, data: Dict, headers: Dict) -> Dict:
        delivery_status = data.get("deliveryStatus", "")
        notification_id = data.get("correlator", "")
        sent_time = data.get("timestamp")
        status = Notification.Status.SENT if delivery_status in ["DeliveredToTerminal", "Delivered"] else Notification.Status.FAILED

        return {
            "notification_id": notification_id,
            "status": status,
            "sent_time": sent_time if status == Notification.Status.SENT else None,
            "state": {
                "delivery_status": delivery_status,
            }
        }
