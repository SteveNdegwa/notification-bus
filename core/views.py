import logging
import json
from json import JSONDecodeError

from django.core.handlers.wsgi import WSGIRequest
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.backend.callback_manager import ProviderCallbackHandler
from core.backend.notification_manager import NotificationManager
from core.backend.services import ProviderService
from core.tasks import process_existing_notification

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
            system = getattr(request, "system", None)
            if system is None:
                return JsonResponse({"code": "401.000.000", "message": "Missing authenticated system"}, status=401)
            data["system"] = str(system.id)
            notification = NotificationManager().save_notification(data, raise_exception=True)
            process_existing_notification.delay(str(notification.id))
            return JsonResponse({
                "code": "100.000.000",
                "message": "Notification queued successfully",
                "notification_id": str(notification.id),
            })
        except JSONDecodeError as ex:
            logger.exception("NotifyAPIsManager - queue_send_notification invalid JSON: %s" % ex)
            return JsonResponse({"code": "400.000.000", "message": "Invalid JSON payload"}, status=400)
        except (KeyError, ValueError) as ex:
            logger.exception("NotifyAPIsManager - queue_send_notification validation error: %s" % ex)
            return JsonResponse({"code": "400.000.000", "message": str(ex)}, status=400)
        except Exception as ex:
            logger.exception("NotifyAPIsManager - queue_send_notification exception: %s" % ex)
            return JsonResponse(
                {"code": "999.999.999", "message": str(ex)},
                status=500
            )

    @staticmethod
    @csrf_exempt
    def provider_callback(request: WSGIRequest, provider_slug: str) -> JsonResponse:
        """
        Receive a delivery-status callback from a notification provider.

        The provider is resolved from the URL slug and must be active with
        callback sending enabled. The request body is parsed as JSON, request
        headers are captured, and the callback is persisted through
        ``ProviderCallbackHandler.receive`` for asynchronous processing.

        :param request: HTTP request containing the provider callback payload.
        :type request: WSGIRequest
        :param provider_slug: Slug identifying the callback provider.
        :type provider_slug: str
        :return: JSON response with the persisted callback ID, or an error response.
        :rtype: JsonResponse
        """
        try:
            provider = ProviderService().get(slug=provider_slug, is_active=True, sends_callbacks=True)
            if provider is None:
                return JsonResponse({"message": "Provider not found"}, status=404)

            data = json.loads(request.body)
            headers = {key: value for key, value in request.headers.items()}
            callback = ProviderCallbackHandler.receive(provider=provider, data=data, headers=headers)

            return JsonResponse({"message": "Callback received", "callback_id": str(callback.id)})
        except Exception as ex:
            logger.exception("NotifyAPIsManager - provider_callback exception: %s" % ex)
            return JsonResponse({"message": "Internal server error"}, status=500)
