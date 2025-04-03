import logging
from typing import Dict, List

from firebase_admin import messaging

from core.backend.providers.base_provider import BaseProvider
from core.models import State

logger = logging.getLogger(__name__)


class FirebasePushProvider(BaseProvider):
    def validate_config(self) -> bool:
        """
        Check if required Firebase credential fields are present.
        """
        required_keys = [
            "type", "project_id", "private_key_id", "private_key",
            "client_email", "client_id", "auth_uri", "token_uri",
            "auth_providepr_x509_cert_url", "client_x509_cert_url"
        ]
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            logger.error("FirebasePushProvider - Missing config keys: %s", ", ".join(missing_keys))
            return False
        return True

    def send(self, recipients: List[str], content: Dict[str, str]) -> State:
        """
        Send push notification to a device or list of devices.

        :param recipients: List of Device token(s),
        :param content: Dictionary with 'title', 'body', and optional 'data'.
        :return: Sent state if notification is sent successfully else Failed state.
        """
        try:
            if not recipients:
                raise ValueError("No valid device tokens provided")

            message_payload = messaging.MulticastMessage(
                tokens=recipients,
                notification=messaging.Notification(
                    title=content.get('title', 'Notification'),
                    body=content.get('body', '')
                ),
                data=content.get('data', {})  # Optional payload
            )

            response = messaging.send_each_for_multicast(message_payload)
            logger.info(
                "FirebasePushProvider - Sent push to %d tokens. Success: %d, Failure: %d",
                len(recipients),
                response.success_count,
                response.failure_count
            )

            if response.success_count == 0:
                raise Exception("Push notification not sent")

            return State.sent()

        except Exception as ex:
            logger.exception("FirebasePushProvider - send exception: %s", ex)
            return State.failed()
