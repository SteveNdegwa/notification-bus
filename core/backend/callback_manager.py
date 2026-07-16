import logging
from datetime import timedelta
from typing import Dict

import requests
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from core.backend.notification_manager import NotificationManager
from core.backend.providers.providers_registry import PROVIDER_CLASSES
from core.models import ProviderCallback, SystemCallback

logger = logging.getLogger(__name__)

MAX_SYSTEM_CALLBACK_ATTEMPTS = 5
SYSTEM_CALLBACK_TIMEOUT_SECONDS = 10


class ProviderCallbackHandler:
    @staticmethod
    def receive(provider, data: Dict, headers: Dict) -> ProviderCallback:
        callback = ProviderCallback.objects.create(provider=provider, data=data, headers=headers)
        from core.tasks import process_provider_callback
        process_provider_callback.delay(str(callback.id))
        return callback

    @staticmethod
    def process(callback_id: str) -> None:
        callback = ProviderCallback.objects.select_related("provider").get(id=callback_id)
        callback.status = ProviderCallback.Status.PROCESSING
        callback.error = ""
        callback.save(update_fields=["status", "error", "date_modified"])

        try:
            provider = callback.provider
            provider_class = PROVIDER_CLASSES.get(provider.class_name)
            if provider_class is None:
                raise ValueError(f"Unknown provider class: {provider.class_name}")

            provider_config = {
                **(provider.default_config or {}),
                **(provider.callback_verification_config or {}),
            }
            provider_handler = provider_class(provider_config)
            if provider.verify_callback_signature and not provider.callback_verification_config:
                raise ValueError("Provider callback signature verification config is missing")
            if provider.verify_callback_signature and not provider_handler.verify_callback_signature(
                callback.data,
                callback.headers,
            ):
                raise ValueError("Provider callback signature verification failed")

            result = provider_handler.handle_callback(callback.data, callback.headers)
            notification_id = result.get("notification_id")
            status = result.get("status")
            if not notification_id or not status:
                raise ValueError("Provider callback result must include notification_id and status")

            update_data = {}
            if result.get("sent_time"):
                sent_time = result["sent_time"]
                update_data["sent_time"] = parse_datetime(sent_time) if isinstance(sent_time, str) else sent_time
            NotificationManager().update_notification_status(
                notification_id=notification_id,
                status=status,
                **update_data,
            )

            callback.status = ProviderCallback.Status.PROCESSED
            callback.state = result.get("state", {})
            callback.processed_at = timezone.now()
            callback.save(update_fields=["status", "state", "processed_at", "date_modified"])
        except Exception as ex:
            logger.exception("ProviderCallbackHandler - process exception: %s", ex)
            callback.status = ProviderCallback.Status.FAILED
            callback.error = str(ex)
            callback.processed_at = timezone.now()
            callback.save(update_fields=["status", "error", "processed_at", "date_modified"])
            raise


class SystemCallbackSender:
    @staticmethod
    def send(callback_id: str) -> None:
        callback = SystemCallback.objects.select_related("system").get(id=callback_id)
        callback.status = SystemCallback.Status.SENDING
        callback.attempts += 1
        callback.error = ""
        callback.save(update_fields=["status", "attempts", "error", "date_modified"])

        try:
            response = requests.post(
                callback.system.webhook_url,
                json=callback.payload,
                headers={"Content-Type": "application/json"},
                timeout=SYSTEM_CALLBACK_TIMEOUT_SECONDS,
            )
            callback.response_status_code = response.status_code
            callback.response_body = response.text[:5000]
            response.raise_for_status()
            callback.status = SystemCallback.Status.SENT
            callback.sent_at = timezone.now()
            callback.next_retry_at = None
            callback.save(update_fields=[
                "response_status_code",
                "response_body",
                "status",
                "sent_at",
                "next_retry_at",
                "date_modified",
            ])
        except requests.RequestException as ex:
            callback.status = SystemCallback.Status.FAILED
            callback.error = str(ex)
            callback.next_retry_at = timezone.now() + timedelta(
                seconds=SystemCallbackSender.retry_delay(callback.attempts)
            )
            callback.save(update_fields=[
                "status",
                "error",
                "next_retry_at",
                "response_status_code",
                "response_body",
                "date_modified",
            ])
            raise

    @staticmethod
    def retry_delay(attempts: int) -> int:
        return min(60 * (2 ** max(attempts - 1, 0)), 3600)
