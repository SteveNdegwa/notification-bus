import logging
from typing import Dict, List

import requests

from core.backend.providers.base_provider import BaseProvider
from core.models import Notification

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30


class OnfonSMSProvider(BaseProvider):
    def validate_config(self) -> bool:
        """
        Ensures required configuration values are present.
        """
        required_keys = ["api_key", "client_id", "access_key", "base_url", "sender_id"]
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            self.set_last_exception("Missing config keys: %s" % ", ".join(missing_keys))
            logger.error("OnfonSMSProvider - %s", self.last_exception)
            return False
        return True

    def send(self, recipients: List[str], content: Dict[str, str]) -> str:
        """
        Sends an SMS to one or more recipients.

        :param recipients: List of phone number(s).
        :param content: Dict with 'body' key containing the message.
        :return: Sent status if sms is sent successfully else Failed status.
        """
        try:
            if not self.validate_config():
                return Notification.Status.FAILED

            message = content.get("body", "")
            if not message:
                self.set_last_exception("Empty message body")
                logger.error("OnfonSMSProvider - %s", self.last_exception)
                return Notification.Status.FAILED

            if not recipients:
                self.set_last_exception("No recipients provided")
                logger.error("OnfonSMSProvider - %s", self.last_exception)
                return Notification.Status.FAILED

            url = f"{self.config.get('base_url').rstrip('/ ')}/sms/SendBulkSMS"
            headers = {
                "AccessKey": self.config.get("access_key"),
                "Content-Type": "application/json",
            }
            payload = {
                "SenderId": self.config.get("sender_id"),
                "IsUnicode": True,
                "IsFlash": False,
                "ScheduleDateTime": "",
                "MessageParameters": [
                    {
                        "Number": recipient,
                        "Text": message,
                    }
                    for recipient in recipients
                ],
                "ApiKey": self.config.get("api_key"),
                "ClientId": self.config.get("client_id"),
            }

            response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            response_data = response.json()

            error_code = response_data.get("ErrorCode")
            if error_code not in (0, "0"):
                error_message = response_data.get("ErrorMessage") or "Unknown API error"
                self.set_last_exception(f"API error: {error_message}")
                logger.error("OnfonSMSProvider - %s", self.last_exception)
                return Notification.Status.FAILED

            return Notification.Status.SENT

        except requests.exceptions.RequestException as ex:
            logger.error("OnfonSMSProvider - SMS sending failed: %s", ex)
            self.set_last_exception(ex)
            return Notification.Status.FAILED
        except Exception as ex:
            logger.exception("OnfonSMSProvider - send exception: %s", ex)
            self.set_last_exception(ex)
            return Notification.Status.FAILED
