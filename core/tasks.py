import logging

from celery import shared_task

from core.backend.notification_manager import NotificationManager
from notify.celery import app

logger = logging.getLogger(__name__)

@shared_task(name='notify.send_notification', bind=True, max_retries=3, default_retry_delay=30)
def send_notification(self, notification_data):
    try:
        notification, error = NotificationManager().save_notification(notification_data)
        if notification is None:
            response_data = {
                "unique_identifier": notification_data.get("unique_identifier", ""),
                "status": "Failed",
                "message": error,
                "sent_at": ""
            }
            system_name = str(notification_data.get('system', '')).lower()
            if system_name:
                NotificationManager().queue_notification_callback(system_name=system_name, response_data=response_data)
        else:
            NotificationManager().send_notification(notification)
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - send_notification exception: %s" % ex)
        raise self.retry(exc=ex)