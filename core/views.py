import logging
import json

from django.core.handlers.wsgi import WSGIRequest
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt

from core.backend.callback_manager import ProviderCallbackHandler
from core.backend.services import ProviderService
from core.backend.notification_manager import NotificationManager, ThreadNotFound
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
        Resolve or create an optional email thread and save the notification before
        queueing delivery through Celery. Return the persisted notification ID.

        :param request: The HTTP request object.
        :type request: WSGIRequest
        :return: A JSON response indicating the result of the operation.
        :rtype: JsonResponse
        """
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")

            system = getattr(request, "system", None)
            if system is None:
                return JsonResponse(
                    {"code": "401.000.000", "message": "Missing authenticated system"},
                    status=401
                )
            data["system"] = str(system.id)

            manager = NotificationManager()
            with transaction.atomic():
                thread = manager.save_email_thread(data, system)
                notification = manager.save_notification(data, email_thread=thread, raise_errors=True)

            result = {
                "code": "100.000.000",
                "message": "Notification queued successfully",
                "notification_id": str(notification.id),
            }
            if notification.email_thread_id:
                result["email_thread_id"] = str(notification.email_thread_id)

            try:
                transaction.on_commit(lambda: process_existing_notification.delay(str(notification.id)))
            except Exception:
                logger.exception("Unable to queue notification %s", notification.id)
                result.update({
                    "code": "999.999.999",
                    "message": "Notification saved but queue unavailable; retry from admin or run reconciliation",
                })
                return JsonResponse(result, status=503)

            return JsonResponse(result)
        except ThreadNotFound as ex:
            return JsonResponse({"message": str(ex)}, status=404)
        except (ValueError, KeyError, ValidationError) as ex:
            return JsonResponse({"message": str(ex)}, status=400)
        except Exception as ex:
            logger.exception("NotifyAPIsManager - queue_send_notification exception: %s" % ex)
            return JsonResponse(
                {"code": "999.999.999", "message": "Send notification failed with an exception"},
                status=500
            )

    @csrf_exempt
    def provider_callback(self, request, provider_slug: str):
        """
        Receive a provider callback, persist it, and queue asynchronous processing.
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
