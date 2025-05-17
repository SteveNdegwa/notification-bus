import logging
import json

from django.core.handlers.wsgi import WSGIRequest
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.backend.notification_manager import NotificationManager
from core.models import State
from core.tasks import send_notification

logger = logging.getLogger(__name__)

class NotifyAPIsManager:
    @staticmethod
    @csrf_exempt
    def queue_send_notification(request: WSGIRequest) -> JsonResponse:
        """
        Queue a notification to be sent asynchronously.

        This view function handles HTTP POST requests to queue a notification for sending.
        It expects the request body to contain JSON data with the notification details.
        The function uses Celery to queue the task for sending the notification.

        :param request: The HTTP request object.
        :type request: WSGIRequest
        :return: A JSON response indicating the result of the operation.
        :rtype: JsonResponse
        """
        try:
            data = json.loads(request.body)
            send_notification.delay(data)
            return JsonResponse({"code": "100.000.000", "message": "Notification queued successfully"})
        except Exception as ex:
            logger.exception("NotifyAPIsManager - queue_send_notification exception: %s" % ex)
            return JsonResponse({"code": "999.999.999", "message": "Send notification failed with an exception"})

    @csrf_exempt
    def belio_sms_provider_callback(self, request):
        """
        Handle Belio SMS Provider delivery status callback.

        This view function processes HTTP POST requests containing SMS delivery status updates.
        It expects the request body to contain JSON data with the delivery status, correlator, and timestamp.
        Based on the delivery status, it updates the notification status in the database.

        :param request: The HTTP request object.
        :type request: WSGIRequest
        :return: A JSON response indicating the result of the operation.
        :rtype: JsonResponse
        """
        try:
            data = json.loads(request.body)

            delivery_status = data.get("deliveryStatus", "")
            notification_id = data.get("correlator", "")
            sent_time = data.get("timestamp", "")

            if delivery_status == "DeliveredToTerminal":
                NotificationManager().update_notification_status(
                    notification_id=notification_id,
                    status = State.sent(),
                    sent_time=sent_time
                )
            else:
                NotificationManager().update_notification_status(
                    notification_id=notification_id,
                    status=State.failed(),
                )

            return JsonResponse({"message": "Success"})
        except Exception as ex:
            logger.exception("NotifyAPIsManager - belio_sms_provider_callback exception: %s" % ex)
            return JsonResponse({"message": "Internal server error"}, status=500)