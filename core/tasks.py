import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from core.backend.callback_manager import MAX_SYSTEM_CALLBACK_ATTEMPTS, ProviderCallbackHandler, SystemCallbackSender
from core.backend.notification_manager import NotificationManager
from core.models import Notification, SystemCallback

logger = logging.getLogger(__name__)

@shared_task(name='notify.send_notification', bind=True, max_retries=3, default_retry_delay=30)
def send_notification(self, notification_data: dict) -> str:
    """
    Celery task to process and send a notification.

    Validates the input data, saves the notification, and attempts to send it
    using the appropriate provider. Retries on failure.

    :param self: Task instance (for retries).
    :param notification_data: Notification input dictionary.
    :type notification_data: dict
    :return: "success" if the notification is processed.
    :rtype: str
    :raises self.retry: On failure after logging the exception.
    """
    notification = None
    manager = NotificationManager()
    try:
        notification = manager.save_notification(notification_data)
        if notification:
            if notification.recipient_resolution_status in [
                Notification.RecipientResolutionStatus.PENDING,
                Notification.RecipientResolutionStatus.FAILED,
            ]:
                notification = manager.resolve_notification_recipients(notification)
            manager.send_notification(notification)
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - send_notification exception: %s" % ex)
        if notification is not None:
            return "failed"
        raise self.retry(exc=ex)


@shared_task(name='notify.process_provider_callback', bind=True, max_retries=3, default_retry_delay=30)
def process_provider_callback(self, callback_id: str) -> str:
    try:
        ProviderCallbackHandler.process(callback_id)
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - process_provider_callback exception: %s" % ex)
        raise self.retry(exc=ex)


@shared_task(name='notify.process_existing_notification', bind=True, max_retries=3, default_retry_delay=30)
def process_existing_notification(self, notification_id: str) -> str:
    try:
        manager = NotificationManager()
        notification = Notification.objects.get(id=notification_id)
        if notification.recipient_resolution_status in [
            Notification.RecipientResolutionStatus.PENDING,
            Notification.RecipientResolutionStatus.PROCESSING,
            Notification.RecipientResolutionStatus.FAILED,
        ]:
            notification = manager.resolve_notification_recipients(notification)
        manager.send_notification(notification)
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - process_existing_notification exception: %s" % ex)
        raise self.retry(exc=ex)


@shared_task(name='notify.send_system_callback', bind=True, max_retries=MAX_SYSTEM_CALLBACK_ATTEMPTS)
def send_system_callback(self, callback_id: str) -> str:
    try:
        SystemCallbackSender.send(callback_id)
        return "success"
    except Exception as ex:
        callback = SystemCallback.objects.get(id=callback_id)
        if callback.attempts >= MAX_SYSTEM_CALLBACK_ATTEMPTS:
            logger.exception("CeleryTasks - send_system_callback exhausted retries: %s" % ex)
            return "failed"
        delay = SystemCallbackSender.retry_delay(callback.attempts)
        logger.exception("CeleryTasks - send_system_callback exception: %s" % ex)
        raise self.retry(exc=ex, countdown=delay)


@shared_task(name='notify.retry_failed_system_callbacks')
def retry_failed_system_callbacks() -> int:
    callbacks = SystemCallback.objects.filter(
        status=SystemCallback.Status.FAILED,
        attempts__lt=MAX_SYSTEM_CALLBACK_ATTEMPTS,
        next_retry_at__lte=timezone.now(),
    )
    count = 0
    for callback in callbacks:
        send_system_callback.delay(str(callback.id))
        count += 1
    return count


@shared_task(name='notify.reconcile_recipient_resolution')
def reconcile_recipient_resolution(minutes: int = 10) -> int:
    stale_before = timezone.now() - timedelta(minutes=minutes)
    notifications = Notification.objects.filter(
        recipient_resolution_status__in=[
            Notification.RecipientResolutionStatus.PENDING,
            Notification.RecipientResolutionStatus.PROCESSING,
            Notification.RecipientResolutionStatus.FAILED,
        ],
        date_modified__lte=stale_before,
    )
    count = 0
    for notification in notifications:
        process_existing_notification.delay(str(notification.id))
        count += 1
    return count
