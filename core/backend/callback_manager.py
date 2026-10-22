import logging
from datetime import timedelta
from typing import Dict

import requests
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from core.backend.notification_manager import NotificationManager
from core.backend.providers.providers_registry import PROVIDER_CLASSES
from core.models import Notification, NotificationTry, ProviderCallback, SystemCallback

logger = logging.getLogger(__name__)

MAX_SYSTEM_CALLBACK_ATTEMPTS = 5
SYSTEM_CALLBACK_TIMEOUT_SECONDS = 10


class ProviderCallbackHandler:
    @staticmethod
    def receive(provider, data: Dict, headers: Dict) -> ProviderCallback:
        """
        Persist an incoming provider callback and queue it for processing.

        :param provider: Provider that submitted the callback.
        :type provider: Provider
        :param data: Parsed callback payload.
        :type data: Dict
        :param headers: Request headers used for optional signature verification.
        :type headers: Dict
        :return: Persisted callback in its initial received state.
        :rtype: ProviderCallback
        """
        callback = ProviderCallback.objects.create(provider=provider, data=data, headers=headers)
        from core.tasks import process_provider_callback
        process_provider_callback.delay(str(callback.id))
        return callback

    @staticmethod
    def process(callback_id: str) -> None:
        """
        Verify and apply a provider delivery callback.

        Successful confirmations complete the matching notification try and update
        the notification. Failed confirmations mark the try failed, evaluate the
        provider/config alert threshold, and queue the notification to continue with
        its remaining delivery candidates. Processing errors are recorded on the
        callback before being raised for task retry handling.

        :param callback_id: Primary key of the stored provider callback.
        :type callback_id: str
        :return: None.
        :rtype: None
        :raises ProviderCallback.DoesNotExist: If the callback record does not exist.
        :raises Notification.DoesNotExist: If the referenced notification does not exist.
        :raises ValueError: If the provider, signature, or callback result is invalid.
        """
        callback = ProviderCallback.objects.select_related("provider").get(id=callback_id)
        callback.status = ProviderCallback.Status.PROCESSING
        callback.error = ""
        callback.save(update_fields=["status", "error", "date_modified"])

        try:
            provider = callback.provider
            provider_class = PROVIDER_CLASSES.get(provider.class_name)
            if provider_class is None:
                raise ValueError(f"Unknown provider class: {provider.class_name}")

            provider_config = dict(provider.callback_verification_config or {})
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
            notification = Notification.objects.get(pk=notification_id)
            attempt = NotificationTry.objects.filter(
                notification_id=notification_id,
                provider=provider,
                delivery_cycle=notification.delivery_cycle,
                status=NotificationTry.Status.CONFIRMATION_PENDING,
            ).order_by("-date_created").first()
            if status == Notification.Status.FAILED:
                if notification.status != Notification.Status.CONFIRMATION_PENDING:
                    raise ValueError("Notification is not awaiting provider confirmation")
                if attempt is not None:
                    attempt.status = NotificationTry.Status.FAILED
                    attempt.error = str(result.get("message") or result.get("error") or "Provider confirmation failed")
                    attempt.save(update_fields=["status", "error", "date_modified"])
                    NotificationManager().queue_provider_config_failure_alert(attempt)
                Notification.objects.filter(pk=notification_id).update(status=Notification.Status.PROCESSING)
                from core.tasks import process_existing_notification
                transaction.on_commit(lambda: process_existing_notification.delay(str(notification_id)))
            else:
                if attempt is not None:
                    attempt.status = NotificationTry.Status.SUCCESS
                    attempt.save(update_fields=["status", "date_modified"])
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
        """
        Send a queued status callback to its originating system.

        The callback attempt count and response details are persisted. HTTP failures
        mark the callback failed and schedule its next retry using exponential
        backoff before the request exception is raised to the task runner.

        :param callback_id: Primary key of the system callback to send.
        :type callback_id: str
        :return: None.
        :rtype: None
        :raises SystemCallback.DoesNotExist: If the callback record does not exist.
        :raises requests.RequestException: If the callback HTTP request fails.
        """
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
        """
        Calculate the bounded exponential delay before a system callback retry.

        :param attempts: Number of callback attempts already made.
        :type attempts: int
        :return: Delay in seconds, starting at 60 seconds and capped at one hour.
        :rtype: int
        """
        return min(60 * (2 ** max(attempts - 1, 0)), 3600)
