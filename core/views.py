import logging
import json

from django.core.handlers.wsgi import WSGIRequest
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

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
    def sms_response(self, request):
        try:
            data = json.loads(request.body)
            print(data)
            # if data.get('deliveryStatus') == 'DeliveredToTerminal':
            return JsonResponse({"code": "100.000.000", "message": "Success"})
        except Exception as ex:
            logger.exception("NotifyAPIsManager - queue_send_notification exception: %s" % ex)
            return JsonResponse({"code": "999.999.999", "message": "Send notification failed with an exception"})