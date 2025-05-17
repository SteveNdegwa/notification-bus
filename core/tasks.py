import logging
from typing import Dict

from celery import shared_task

from core.backend.notification_manager import NotificationManager
from notify.celery import app

logger = logging.getLogger(__name__)

@shared_task(name='notify.send_notification', bind=True, max_retries=3, default_retry_delay=30)
def send_notification(self, notification_data: Dict) -> str:
    """
    Celery task to handle the creation and sending of a notification.

    This task validates and saves the notification data, then sends it using the appropriate handler.
    Retries up to 3 times on failure with a 30-second delay between attempts.

    :param self: Reference to the Celery task instance (for retries).
    :param notification_data: Dictionary containing notification information.
    :return: "success" if task completes without raising an exception.
    """
    try:
        notification = NotificationManager().save_notification(notification_data)
        if notification:
            NotificationManager().send_notification(notification)
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - send_notification exception: %s" % ex)
        raise self.retry(exc=ex)
