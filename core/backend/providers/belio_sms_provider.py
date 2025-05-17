import logging
from typing import Dict, List

import requests

from core.backend.providers.base_provider import BaseProvider
from core.models import State

logger = logging.getLogger(__name__)


class BelioSMSProvider(BaseProvider):
    def validate_config(self) -> bool:
        """
        Ensures required configuration values are present.
        """
        required_keys = ["api_key", "cookie", "url", "default_sms_service_id", "callback_url"]
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            logger.error("BelioSMSProvider - Missing config keys: %s", ", ".join(missing_keys))
            return False
        return True

    def send(self, recipients: List[str], content: Dict[str, str]) -> State:
        """
        Sends an SMS to one or more recipients.

        :param recipients: List of phone number(s).
        :param content: Dict with 'body' key containing the message.
        :return: ConfirmationPending state if sms is queued successfully else Failed state.
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
                "smsServiceId": content.get("sms_service_id", self.config.get("default_sms_service_id")),
                "message": message,
                "addresses": recipients,
                "deliveryReportRequest": {
                    "correlator": unique_identifier,
                    "callbackUrl": self.config.get("callback_url")
                }
            }

            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()

            return State.confirmation_pending()
        except Exception as ex:
            logger.exception("BelioSMSProvider - send exception: %s", ex)
            return State.failed()
