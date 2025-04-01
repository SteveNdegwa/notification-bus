import logging
from typing import Dict, List, Union

import firebase_admin
from firebase_admin import credentials, messaging

from core.backend.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class FirebasePushProvider(BaseProvider):
    """
    Push notification provider using Firebase Cloud Messaging (FCM).
    """

    def initialize(self) -> None:
        """
        Initialize the Firebase app using the provided configuration.
        The config must contain a valid Firebase service account JSON.
        """
        # Avoid re-initializing Firebase app if already done
        if not firebase_admin._apps:
            cred = credentials.Certificate(self.config)
            self.client = firebase_admin.initialize_app(cred)
        else:
            self.client = firebase_admin.get_app()

    def send(self, recipients: List[str], content: Dict[str, str]) -> bool:
        """
        Send push notification to a device or list of devices.

        :param recipients: List of Device token(s),
        :param content: Dictionary with 'title', 'body', and optional 'data'.
        :return: True if sent successfully to at least one token.
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

            return response.success_count > 0

        except Exception as ex:
            logger.exception("FirebasePushProvider - send exception: %s", ex)
            return False

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
