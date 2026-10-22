import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.db.models import Q

from core.backend.callback_manager import MAX_SYSTEM_CALLBACK_ATTEMPTS, ProviderCallbackHandler, SystemCallbackSender
from core.backend.notification_manager import NotificationManager, ThreadDeliveryPending, ThreadProviderPending
from core.models import Notification, SystemCallback

logger = logging.getLogger(__name__)

@shared_task(name='notify.send_notification', bind=True, max_retries=3, default_retry_delay=30)
def send_notification(self, notification_data: dict) -> str:
    """
    Celery task to process and send a notification.

    Resolve or create an optional email thread for the payload's system, save
    the notification, and attempt provider delivery. Thread creation and saving
    are atomic. If threaded delivery must wait, queue the persisted notification
    for another attempt so retries do not create a second notification or thread.

    :param self: Task instance (for retries).
    :type self: celery.Task
    :param notification_data: Notification input dictionary, including optional thread creation or attachment fields.
    :type notification_data: dict
    :return: "success" if processed or queued for later delivery, or "failed" if an existing notification fails processing.
    :rtype: str
    :raises self.retry: If thread resolution or notification saving fails before persistence.
    """
    notification = None
    manager = NotificationManager()
    try:
        with transaction.atomic():
            system = manager._get_system(notification_data.get("system"))
            if system is None:
                raise ValueError("Invalid system")
            thread = manager.save_email_thread(notification_data, system)
            notification = manager.save_notification(notification_data, email_thread=thread, raise_errors=True)
        if not notification.email_thread_id and notification.recipient_resolution_status in [
            Notification.RecipientResolutionStatus.PENDING,
            Notification.RecipientResolutionStatus.FAILED,
        ]:
            notification = manager.resolve_notification_recipients(notification)
        manager.send_notification(notification)
        return "success"
    except (ThreadDeliveryPending, ThreadProviderPending) as ex:
        # Retry delivery by ID; replaying this payload could create another thread.
        delivery_attempt = 1 if isinstance(ex, ThreadProviderPending) else 0
        process_existing_notification.apply_async(
            args=[str(notification.id)], kwargs={"delivery_attempt": delivery_attempt}, countdown=30,
        )
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - send_notification exception: %s" % ex)
        if notification is not None:
            return "failed"
        raise self.retry(exc=ex)


@shared_task(name='notify.process_provider_callback', bind=True, max_retries=3, default_retry_delay=30)
def process_provider_callback(self, callback_id: str) -> str:
    """
    Process a persisted provider callback and update its notification status.

    Delegate callback verification and processing to the provider callback
    handler. Retry processing failures using the task's configured retry limit.

    :param self: Task instance (for retries).
    :type self: celery.Task
    :param callback_id: Primary key of the persisted provider callback.
    :type callback_id: str
    :return: "success" if callback processing completes.
    :rtype: str
    :raises self.retry: If callback processing fails.
    """
    try:
        ProviderCallbackHandler.process(callback_id)
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - process_provider_callback exception: %s" % ex)
        raise self.retry(exc=ex)


@shared_task(name='notify.process_existing_notification', bind=True, max_retries=None, default_retry_delay=30)
def process_existing_notification(self, notification_id: str, *, delivery_attempt: int = 0) -> str:
    """
    Load a persisted notification, resolve recipients, and attempt delivery.

    Skip threaded notifications that are already sent. Resolve recipients for
    ordinary notifications before delivery; threaded delivery resolves them
    while holding the thread lock. Retry waits for earlier thread messages
    without consuming the delivery-attempt budget. Provider availability and
    other processing failures increment that budget, allowing three retries.

    :param self: Task instance (for retries).
    :type self: celery.Task
    :param notification_id: Primary key of the notification to process.
    :type notification_id: str
    :param delivery_attempt: Number of delivery retries already consumed, excluding thread-order waits.
    :type delivery_attempt: int
    :return: "success" if processing completes or the threaded notification was already sent,
             or "failed" if provider availability retries are exhausted.
    :rtype: str
    :raises self.retry: If delivery must wait or a processing failure has retries remaining.
    :raises Exception: If another processing failure occurs after the delivery retry budget is exhausted.
    """
    try:
        manager = NotificationManager()
        notification = Notification.objects.get(id=notification_id)
        if notification.email_thread_id and notification.status == Notification.Status.SENT:
            return "success"
        if not notification.email_thread_id and notification.recipient_resolution_status in [
            Notification.RecipientResolutionStatus.PENDING,
            Notification.RecipientResolutionStatus.PROCESSING,
            Notification.RecipientResolutionStatus.FAILED,
        ]:
            notification = manager.resolve_notification_recipients(notification)
        manager.send_notification(notification)
        return "success"
    except ThreadDeliveryPending as ex:
        # Waiting for a predecessor is not a delivery failure and must not use
        # up the ordinary task's small retry budget.
        raise self.retry(exc=ex, countdown=30, max_retries=None)
    except ThreadProviderPending as ex:
        if delivery_attempt >= 3:
            manager.update_notification_status(
                notification_id, Notification.Status.FAILED, message=str(ex),
            )
            return "failed"
        raise self.retry(exc=ex, countdown=30, kwargs={"delivery_attempt": delivery_attempt + 1})
    except Exception as ex:
        logger.exception("CeleryTasks - process_existing_notification exception: %s" % ex)
        if delivery_attempt >= 3:
            raise
        raise self.retry(exc=ex, kwargs={"delivery_attempt": delivery_attempt + 1})


@shared_task(name='notify.send_system_callback', bind=True, max_retries=MAX_SYSTEM_CALLBACK_ATTEMPTS)
def send_system_callback(self, callback_id: str) -> str:
    """
    Deliver a persisted system callback to its configured webhook.

    Retry delivery failures with the callback sender's exponential delay.
    Return "failed" when the callback reaches the maximum number of attempts.

    :param self: Task instance (for retries).
    :type self: celery.Task
    :param callback_id: Primary key of the persisted system callback.
    :type callback_id: str
    :return: "success" if delivery completes, or "failed" if callback attempts are exhausted.
    :rtype: str
    :raises self.retry: If delivery fails and callback attempts remain.
    :raises SystemCallback.DoesNotExist: If the requested callback does not exist.
    """
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
    """
    Queue failed system callbacks that are due for another delivery attempt.

    Select failed callbacks below the maximum attempt limit whose next retry
    time has arrived, then queue each callback through send_system_callback.

    :return: Number of system callbacks queued for delivery.
    :rtype: int
    :raises Exception: If callback lookup or task queueing fails.
    """
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
    """
    Requeue stale recipient resolution and pending threaded notification delivery.

    Select notifications whose recipient resolution is pending, processing, or
    failed, along with pending threaded notifications. Only include records last
    modified at least the specified number of minutes ago, then queue processing
    by notification ID.

    :param minutes: Minimum time since the notification was last modified, in minutes.
    :type minutes: int
    :return: Number of notifications queued for processing.
    :rtype: int
    :raises Exception: If notification lookup or task queueing fails.
    """
    stale_before = timezone.now() - timedelta(minutes=minutes)
    notifications = Notification.objects.filter(
        Q(recipient_resolution_status__in=[
            Notification.RecipientResolutionStatus.PENDING,
            Notification.RecipientResolutionStatus.PROCESSING,
            Notification.RecipientResolutionStatus.FAILED,
        ]) | Q(email_thread__isnull=False, status=Notification.Status.PENDING),
        date_modified__lte=stale_before,
    )
    count = 0
    for notification in notifications:
        process_existing_notification.delay(str(notification.id))
        count += 1
    return count
