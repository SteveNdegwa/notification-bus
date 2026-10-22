import logging
from typing import Dict, List

import africastalking

from core.backend.providers.base_provider import BaseProvider
from core.models import Notification

logger = logging.getLogger(__name__)


class AfricasTalkingSMSProvider(BaseProvider):
    def validate_config(self) -> bool:
        """
        Ensures required configuration values are present.
        """
        required_keys = ["username", "api_key"]
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            self.set_last_exception("Missing config keys: %s" % ", ".join(missing_keys))
            logger.error("Africa'sTalkingSMSProvider - %s", self.last_exception)
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
            message = content.get("body", "")
            sender_id = content.get("sender_id", None)
            africastalking.initialize(self.config.get("username"), self.config.get("api_key"))
            response = africastalking.SMS.send(message, recipients, sender_id=sender_id if sender_id else None)
            logger.info("Africa's Talking response: %s", response)
            return Notification.Status.SENT
        except Exception as ex:
            logger.exception("Africa'sTalkingSMSProvider - send exception: %s", ex)
            self.set_last_exception(ex)
            return Notification.Status.FAILED
