import logging
import os
import re
from typing import Dict, Type, Any, Optional, Union, List
from uuid import UUID

import requests
from django.conf import settings
from django.db.models import Case, IntegerField, Q, When
from django.utils import timezone

from core.backend.notification_types.base_notification import BaseNotification
from core.backend.notification_types.email_notification import EmailNotification
from core.backend.notification_types.push_notification import PushNotification
from core.backend.notification_types.sms_notification import SMSNotification

from core.backend.providers.base_provider import BaseProvider
from core.backend.providers.providers_registry import PROVIDER_CLASSES

from core.backend.services import SystemService, NotificationTypeService, TemplateService, NotificationService

from core.models import Notification, Provider, System, SystemCallback

logger = logging.getLogger(__name__)


class NotificationManager:
    """
    Handles the creation, validation, sending, and callback of notifications
    across multiple types (email, SMS, push) using appropriate providers.
    """

    def __init__(self):
        """
        Initialize the notification manager with supported notification types.
        """
        self.notification_types: Dict[str, Type[BaseNotification]] = {
            "email": EmailNotification,
            "sms": SMSNotification,
            "push": PushNotification
        }

    @staticmethod
    def _clean_recipients(notification_type: str, recipients: Union[List[str], str]) -> List[str]:
        """
        Normalize and clean the recipients' list based on the notification type.

        :param notification_type: Type of the notification (e.g., 'sms', 'email').
        :type notification_type: str
        :param recipients: Recipients as a list or comma-separated string.
        :type recipients: Union[List[str], str]
        :return: Cleaned list of recipients.
        :rtype: List[str]
        """
        cleaned_recipients = set()
        if isinstance(recipients, str):
            recipients = [recipient for recipient in recipients.split(",")]
        for recipient in recipients:
            recipient = re.sub(r"\s+", "", recipient)
            if notification_type == "sms":
                recipient = recipient.replace("+", "")
            cleaned_recipients.add(recipient)
        return list(cleaned_recipients)

    def _validate_notification_data(self, notification_data: Any):
        """
        Validates and normalizes notification data.

        :param notification_data: Notification request dictionary.
        :type notification_data: Any
        :raises KeyError: If required keys are missing.
        :raises ValueError: If 'context' is not a dictionary.
        """
        required_fields = ['system', 'notification_type', 'context']
        for field in required_fields:
            if field not in notification_data:
                raise KeyError(f"Missing '{field}' in notification data")
        if not isinstance(notification_data['context'], dict):
            raise ValueError("'context' must be a dictionary")
        if not notification_data.get('recipients') and not notification_data.get('user_ids'):
            raise KeyError("Missing 'recipients' or 'user_ids' in notification data")

        # Normalize fields
        notification_data['system'] = str(notification_data['system'])
        notification_data['notification_type'] = str(notification_data['notification_type']).lower()
        notification_data['template'] = str(notification_data.get('template', '')).lower()
        if notification_data.get('recipients'):
            notification_data['recipients'] = self._clean_recipients(
                notification_data['notification_type'], notification_data['recipients'])
        else:
            notification_data['recipients'] = []
        notification_data['user_ids'] = notification_data.get('user_ids') or []

    def save_notification(self, notification_data: Dict, raise_exception: bool = False) -> Optional[Notification]:
        """
        Create and persist a Notification object.

        :param notification_data: Validated and normalized data.
        :type notification_data: Dict
        :param raise_exception: Re-raise validation or persistence errors after optional callback handling.
        :type raise_exception: bool
        :return: The created Notification object, or None on failure.
        :rtype: Optional[Notification]
        """
        try:
            self._validate_notification_data(notification_data)

            system = self._get_system(notification_data.get('system'))
            if system is None:
                raise ValueError("Invalid system")

            notification_type = NotificationTypeService().get(name=notification_data['notification_type'])
            if notification_type is None:
                raise ValueError("Invalid notification type")

            template = self._get_template_for_system(
                template_name=notification_data.get('template'),
                system=system,
                notification_type=notification_type,
            )

            recipient_resolution_status = Notification.RecipientResolutionStatus.NOT_REQUIRED
            notification_status = Notification.Status.PENDING
            if notification_data['user_ids'] and not notification_data['recipients']:
                recipient_resolution_status = Notification.RecipientResolutionStatus.PENDING
                notification_status = Notification.Status.GETTING_RECIPIENTS

            notification = NotificationService().create(
                system=system,
                unique_identifier=notification_data.get('unique_identifier', ''),
                notification_type=notification_type,
                user_ids=notification_data['user_ids'],
                recipients=notification_data['recipients'],
                recipient_resolution_status=recipient_resolution_status,
                template=template,
                context=notification_data['context'],
                send_exception="",
                status=notification_status,
            )
            if notification is None:
                raise Exception("Notification not created")

            return notification

        except Exception as ex:
            logger.exception(f"NotificationManager - save_notification exception: {ex}")
            system = self._get_system(notification_data.get('system'))
            if system and system.callback_enabled:
                self.send_callback_to_system(system, {
                    "status": "failed",
                    "message": str(ex),
                    "unique_identifier": notification_data.get("unique_identifier", None),
                })
            if raise_exception:
                raise
            return None

    @staticmethod
    def _get_system(system_identifier: Union[UUID, str]) -> Optional[System]:
        """
        Fetch a system by ID, with name fallback for older queued payloads.

        :param system_identifier: System primary key or legacy system name.
        :type system_identifier: Union[UUID, str]
        :return: Matching system, if found.
        :rtype: Optional[System]
        """
        system_identifier = str(system_identifier or "")
        system = SystemService().get(id=system_identifier)
        if system is not None:
            return system
        return SystemService().get(name=system_identifier.lower())

    def resolve_notification_recipients(self, notification: Notification) -> Notification:
        """
        Resolve notification recipients from IDMS when only user IDs were provided.

        Marks the recipient-resolution step as processing, calls IDMS with the
        notification user IDs and notification type, then stores the resolved
        recipients on the notification. If resolution fails, the notification is
        marked failed with the resolution error for reconciliation.

        :param notification: Notification that needs recipient resolution.
        :type notification: Notification
        :return: Notification with resolved recipients.
        :rtype: Notification
        :raises ValueError: If neither recipients nor user IDs are present.
        :raises Exception: If IDMS lookup or notification update fails.
        """
        if notification.recipients:
            return notification
        if not notification.user_ids:
            raise ValueError("Notification has no recipients or user IDs")

        NotificationService().update(
            pk=notification.id,
            status=Notification.Status.GETTING_RECIPIENTS,
            recipient_resolution_status=Notification.RecipientResolutionStatus.PROCESSING,
            recipient_resolution_error="",
        )

        try:
            recipients = self.infer_recipients_from_idms(
                user_ids=notification.user_ids,
                notification_type=notification.notification_type.name,
            )
            notification = NotificationService().update(
                pk=notification.id,
                recipients=self._clean_recipients(notification.notification_type.name, recipients),
                status=Notification.Status.PENDING,
                recipient_resolution_status=Notification.RecipientResolutionStatus.RESOLVED,
                recipient_resolution_error="",
                send_exception="",
            )
            if notification is None:
                raise Exception("Notification recipients not updated")
            return notification
        except Exception as ex:
            NotificationService().update(
                pk=notification.id,
                status=Notification.Status.FAILED,
                recipient_resolution_status=Notification.RecipientResolutionStatus.FAILED,
                recipient_resolution_error=str(ex),
                send_exception=str(ex),
            )
            raise

    @staticmethod
    def _get_template_for_system(template_name: str, system: System, notification_type) -> Optional:
        """
        Fetch a template scoped to the system, falling back to a global template.

        A template with ``system`` set is only available to that system. A template
        with no system is global and can be used by any system.

        :param template_name: Template name from the notification request.
        :type template_name: str
        :param system: Authenticated system sending the notification.
        :type system: System
        :param notification_type: Notification type for the requested template.
        :return: Matching system-scoped or global template, or None when no template name was provided.
        :rtype: Optional[Template]
        :raises ValueError: If a named template does not exist for the system or globally.
        """
        if not template_name:
            return None

        templates = TemplateService().filter(
            Q(system=system) | Q(system__isnull=True),
            name=template_name,
            notification_type=notification_type,
            is_active=True,
        )
        template = None
        if templates is not None:
            template = templates.annotate(
                scope_priority=Case(
                    When(system=system, then=0),
                    default=1,
                    output_field=IntegerField(),
                )
            ).order_by("scope_priority").first()
        if template is None:
            raise ValueError("Invalid template")
        return template

    @staticmethod
    def infer_recipients_from_idms(user_ids: List[str], notification_type: str) -> List[str]:
        """
        Fetch notification recipients from IDMS for the given users and channel.

        IDMS is expected to return either a JSON list or an object containing a
        ``recipients`` list. The endpoint can be configured directly via
        ``IDMS_RECIPIENTS_URL`` or derived from ``IDMS_BASE_URL``.

        :param user_ids: External user IDs provided by the requesting system.
        :type user_ids: List[str]
        :param notification_type: Notification type, such as sms, email, or push.
        :type notification_type: str
        :return: Recipient addresses, phone numbers, or device tokens.
        :rtype: List[str]
        :raises ValueError: If IDMS is not configured or returns an invalid payload.
        :raises requests.RequestException: If the IDMS request fails.
        """
        idms_base_url = settings.IDMS_BASE_URL
        if not idms_base_url:
            raise ValueError("IDMS base URL not configured")
        url = idms_base_url + "/notifications/resolve-recipients/"

        headers = {"Content-Type": "application/json"}
        idms_api_key = getattr(settings, "IDMS_API_KEY", None)
        if idms_api_key:
            headers["X-API-KEY"] = idms_api_key

        response = requests.post(
            url,
            json={"user_ids": user_ids, "notification_type": notification_type},
            headers=headers,
            timeout=getattr(settings, "IDMS_REQUEST_TIMEOUT", 10),
        )
        response.raise_for_status()
        data = response.json()
        recipients = data.get("recipients", data if isinstance(data, list) else None)
        if not isinstance(recipients, list):
            raise ValueError("IDMS response must include a recipients list")
        return recipients

    def _get_notification_instance(self, notification: Notification) -> BaseNotification:
        """
        Return the appropriate notification handler instance.

        :param notification: Notification object.
        :type notification: Notification
        :return: Subclass of BaseNotification.
        :rtype: BaseNotification
        :raises ValueError: If the notification type is unsupported.
        """
        notification_type_name = notification.notification_type.name
        notification_class = self.notification_types.get(notification_type_name)
        if not notification_class:
            raise ValueError(f"Unsupported notification type: {notification_type_name}")
        return notification_class(notification)

    @staticmethod
    def _get_provider_class_instance(provider: Provider, system: System) -> BaseProvider:
        """
        Instantiate the provider class based on registry mapping.

        :param provider: Provider model instance.
        :type provider: Provider
        :return: Instance of the mapped provider class.
        :rtype: BaseProvider
        :raises ValueError: If the provider class is not found in the registry.
        """
        provider_class = PROVIDER_CLASSES.get(provider.class_name)
        if provider_class is None:
            raise ValueError(f"Unknown provider class: {provider.class_name}")
        config = dict(provider.get_config(system) or {})
        if provider.sends_callbacks:
            config["callback_url"] = NotificationManager._build_provider_callback_url(provider)
        return provider_class(config)

    @staticmethod
    def _build_provider_callback_url(provider: Provider) -> str:
        """
        Build the public callback URL that should be passed to a provider.

        The URL is based on ``SYSTEM_BASE_URL`` and the provider's unique slug,
        producing the endpoint handled by ``NotifyAPIsManager.provider_callback``.

        :param provider: Provider that will send callbacks.
        :type provider: Provider
        :return: Absolute provider callback URL.
        :rtype: str
        :raises ValueError: If ``SYSTEM_BASE_URL`` is not configured.
        """
        system_base_url = getattr(settings, "SYSTEM_BASE_URL", "").rstrip("/")
        if not system_base_url:
            raise ValueError("SYSTEM_BASE_URL is not configured")
        return f"{system_base_url}/api/core/callbacks/{provider.slug}/"

    def send_notification(self, notification: Notification) -> bool:
        """
        Attempt to send a notification using all active providers.

        :param notification: Notification object to be sent.
        :type notification: Notification
        :return: True if sent by any provider, False if all fail.
        :rtype: bool
        """
        try:
            notification_handler = self._get_notification_instance(notification)
            notification_handler.validate()

            active_providers = notification_handler.active_providers()
            if not active_providers.exists():
                raise Exception(f"No active providers found for {notification.notification_type.name} notifications")

            content = notification_handler.prepare_content()
            send_exception = ""

            for provider in active_providers:
                provider_class_instance = self._get_provider_class_instance(provider, notification.system)

                if not provider_class_instance.validate_config():
                    send_exception = (
                        provider_class_instance.last_exception
                        or f"Invalid configuration for provider: {provider.name}"
                    )
                    logger.warning(send_exception)
                    continue

                send_notification_status = provider_class_instance.send(
                    recipients=notification.recipients,
                    content=content
                )

                if send_notification_status == Notification.Status.FAILED:
                    send_exception = provider_class_instance.last_exception or f"Send failed for provider: {provider.name}"
                    logger.warning(f"Send notification failed for provider: {provider.name}")
                    continue

                update_data = {
                    "notification_id": notification.id,
                    "status": send_notification_status,
                    "provider": provider,
                    "send_exception": "",
                }
                if send_notification_status == Notification.Status.SENT:
                    update_data["sent_time"] = timezone.now()

                self.update_notification_status(**update_data)
                return True

            raise Exception(send_exception or "Notification not sent")

        except Exception as ex:
            logger.exception(f"NotificationManager - send_notification exception: {ex}")
            self.update_notification_status(
                notification_id=notification.id,
                status=Notification.Status.FAILED,
                message=str(ex),
                send_exception=str(ex),
            )
            return False

    def update_notification_status(
        self,
        notification_id: Union[UUID, str],
        status: str,
        message: Optional[str] = None,
        **kwargs
    ) -> None:
        """
        Update the status of a Notification and trigger a callback to the system.

        :param notification_id: Primary key of the notification.
        :type notification_id: Union[UUID, str]
        :param status: New status to set.
        :type status: str
        :param message: Optional error or status message.
        :type message: Optional[str]
        :param kwargs: Additional fields to update in the notification.
        :raises Exception: If the notification update fails.
        """
        update_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        notification = NotificationService().update(pk=notification_id, status=status, **update_kwargs)
        if notification is None:
            raise Exception("Notification not updated")

        response_data = {
            "notification_id": str(notification.id),
            "unique_identifier": notification.unique_identifier,
            "status": notification.status,
        }

        if message:
            response_data["message"] = message
        if notification.status in [Notification.Status.SENT, Notification.Status.CONFIRMATION_PENDING]:
            response_data["sent_time"] = str(notification.sent_time)

        if notification.system.callback_enabled:
            self.send_callback_to_system(system=notification.system, payload=response_data, notification=notification)

    def send_callback_to_system(
        self,
        system: System,
        payload: Dict,
        notification: Optional[Notification] = None
    ) -> Optional[SystemCallback]:
        """
        Send a callback response to the system after notification processing.

        :param system: System instance receiving the callback.
        :type system: System
        :param payload: Dictionary payload to send.
        :type payload: Dict
        :param notification: Notification linked to the callback, if available.
        :type notification: Optional[Notification]
        :return: Persisted callback record, or None when webhook callback is not configured.
        :rtype: Optional[SystemCallback]
        """
        if not system.webhook_url:
            logger.warning(f"Webhook URL not configured for system '{system.name}'.")
            return None

        callback = SystemCallback.objects.create(system=system, notification=notification, payload=payload)
        from core.tasks import send_system_callback
        send_system_callback.delay(str(callback.id))
        return callback
