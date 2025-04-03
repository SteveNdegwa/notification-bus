import logging
from typing import Dict, Union, List

import africastalking

from core.backend.providers.base_provider import BaseProvider
from core.models import State

logger = logging.getLogger(__name__)


class AfricasTalkingSMSProvider(BaseProvider):
    def validate_config(self) -> bool:
        """
        Ensures required configuration values are present.
        """
        required_keys = ["username", "api_key"]
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            logger.error("Africa'sTalkingSMSProvider - Missing config keys: %s", ", ".join(missing_keys))
            return False
        return True

    def send(self, recipients: List[str], content: Dict[str, str]) -> State:
        """
        Sends an SMS to one or more recipients.

        :param recipients: List of phone number(s).
        :param content: Dict with 'body' key containing the message.
        :return: Sent state if sms is sent successfully else Failed state.
        """
        try:
            message = content.get("body", "")
            sender_id = self.config.get("sender_id", None)
            africastalking.initialize(self.config.get("username"), self.config.get("api_key"))
            response = africastalking.SMS.send(message, recipients, sender_id=sender_id if sender_id else None)
            logger.info("Africa's Talking response: %s", response)
            return State.sent()
        except Exception as ex:
            logger.exception("Africa'sTalkingSMSProvider - send exception: %s", ex)
            return State.failed()
