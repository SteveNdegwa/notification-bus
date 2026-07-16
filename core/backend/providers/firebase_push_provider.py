import logging
import hashlib
import threading
from typing import Dict, List

import firebase_admin
from firebase_admin import credentials, messaging

from core.backend.providers.base_provider import BaseProvider
from core.models import Notification

logger = logging.getLogger(__name__)


class FirebasePushProvider(BaseProvider):
    """
    Provider for Firebase messaging.

    Firebase app initialization is cached at the class level, but provider
    instances are not singletons so per-send state such as last_exception is
    isolated.
    """

    _firebase_app = None
    _config_hash = None
    _firebase_lock = threading.Lock()

    @staticmethod
    def _get_config_hash(config: Dict) -> str:
        """
        Compute a SHA256 hash of the sorted config items.

        :param config: Firebase config dictionary.
        :type config: dict
        :return: SHA256 hash of config.
        :rtype: str
        """
        config_str = str(sorted(config.items()))
        return hashlib.sha256(config_str.encode()).hexdigest()

    def validate_config(self) -> bool:
        """
        Validate that all required Firebase credentials are present in config.

        :return: True if valid, False otherwise.
        :rtype: bool
        """
        required_keys = [
            "type", "project_id", "private_key_id", "private_key", "client_email",
            "client_id", "auth_uri", "token_uri", "auth_provider_x509_cert_url",
            "client_x509_cert_url", "universe_domain"
        ]
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            self.set_last_exception("Missing config keys: %s" % ", ".join(missing_keys))
            logger.error("FirebasePushProvider - %s", self.last_exception)
            return False
        return True

    def _initialize_if_needed(self):
        """
        Initialize or reinitialize the Firebase app if config has changed or not initialized yet.

        :raises ValueError: If the configuration is invalid.
        """
        new_hash = self._get_config_hash(self.config)
        provider_class = type(self)

        with provider_class._firebase_lock:
            if provider_class._firebase_app is not None and provider_class._config_hash == new_hash:
                return

            logger.info("FirebasePushProvider - Firebase config changed or not initialized. (Re)initializing...")

            if not self.validate_config():
                raise ValueError(self.last_exception or "FirebasePushProvider - Invalid Firebase config.")

            if provider_class._firebase_app:
                try:
                    firebase_admin.delete_app(provider_class._firebase_app)
                except Exception as e:
                    logger.warning("FirebasePushProvider - Could not delete app: %s", e)

            cred = credentials.Certificate(self.config)
            provider_class._firebase_app = firebase_admin.initialize_app(cred)
            provider_class._config_hash = new_hash

            logger.info("FirebasePushProvider - Firebase initialized.")

    def send(self, recipients: List[str], content: Dict[str, str]) -> str:
        """
        Send a push notification to multiple device tokens via Firebase.

        :param recipients: List of device tokens to receive the notification.
        :type recipients: List[str]
        :param content: Notification content with 'title', 'body', and optional 'data' payload.
        :type content: dict
        :return: Notification status indicating success or failure.
        :rtype: str
        :raises ValueError: If no recipients are provided.
        """
        try:
            if not recipients:
                raise ValueError("No recipient tokens provided.")

            self._initialize_if_needed()

            message = messaging.MulticastMessage(
                tokens=recipients,
                notification=messaging.Notification(
                    title=content.get("title", "Notification"),
                    body=content.get("body", "")
                ),
                data=content.get("data", {})
            )

            response = messaging.send_each_for_multicast(message, app=type(self)._firebase_app)

            logger.info(
                "FirebasePushProvider - Sent to %d tokens (Success: %d, Failure: %d)",
                len(recipients), response.success_count, response.failure_count
            )

            if response.success_count == 0:
                raise Exception("All push notifications failed")

            return Notification.Status.SENT

        except Exception as e:
            logger.exception("FirebasePushProvider - send exception: %s", e)
            self.set_last_exception(e)
            return Notification.Status.FAILED
