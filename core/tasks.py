import logging

from celery import shared_task

from core.backend.notification_manager import NotificationManager
from notify.celery import app

logger = logging.getLogger(__name__)

@shared_task(name='notify.send_notification', bind=True, max_retries=3, default_retry_delay=30)
def send_notification(self, notification_data):
    try:
        notification = NotificationManager().save_notification(notification_data)
        NotificationManager().send_notification(notification)
        notification.refresh_from_db()
        response_data = {
            "notification_id": str(notification.id),
            "unique_identifier": notification.unique_identifier,
            "status": notification.status.name,
            "sent_at": notification.sent_time
        }
        send_response.delay(system_name=notification.system.name, response_data=response_data)
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - send_notification exception: %s" % ex)
        raise self.retry(exc=ex)

@shared_task(name='notify.send_response', bind=True, max_retries=3, default_retry_delay=30)
def send_response(self, system_name: str, response_data: dict):
    try:
        app.send_task(
            f'{system_name}.handle_send_notification_response',
            args=(response_data,),
            queue=f'{system_name}_queue'
        )
        return "success"
    except Exception as ex:
        logger.exception("CeleryTasks - send_response exception: %s" % ex)
        raise self.retry(exc=ex)