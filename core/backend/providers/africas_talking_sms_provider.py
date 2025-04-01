import logging
from typing import Dict, Union, List

import africastalking

from core.backend.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class AfricasTalkingSMSProvider(BaseProvider):
    def initialize(self) -> None:
        """
        Initializes the Africa's Talking SMS client using the configuration.
        """
        username = self.config.get("username")
        api_key = self.config.get("api_key")

        africastalking.initialize(username, api_key)
        self.client = africastalking.SMS

    def send(self, recipients: List[str], content: Dict[str, str]) -> bool:
        """
        Sends an SMS to one or more recipients.

        :param recipients: List of phone number(s).
        :param content: Dict with 'body' key containing the message.
        :return: True if SMS sent successfully, False otherwise.
        """
        try:
            message = content.get("body", "")
            sender_id = self.config.get("sender_id", None)  # Optional sender ID

            response = self.client.send(message, recipients, sender_id=sender_id if sender_id else None)
            logger.info("Africa's Talking response: %s", response)
            return True
        except Exception as ex:
            logger.exception("Africa'sTalkingSMSProvider - send exception: %s", ex)
            return False

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
