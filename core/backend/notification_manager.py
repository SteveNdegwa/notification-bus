import logging
from typing import Dict, Type, Any, Tuple, Optional, Union, List
from uuid import UUID

import requests
from django.utils import timezone

from core.backend.notification_types.base_notification import BaseNotification
from core.backend.notification_types.email_notification import EmailNotification
from core.backend.notification_types.push_notification import PushNotification
from core.backend.notification_types.sms_notification import SMSNotification

from core.backend.providers.base_provider import BaseProvider
from core.backend.providers.providers_registry import PROVIDER_CLASSES

from core.backend.services import SystemService, NotificationTypeService, TemplateService, NotificationService, \
    StateService, OrganisationService

from core.models import Notification, Provider, State, System
from notify.celery import app

logger = logging.getLogger(__name__)


class NotificationManager:
    def __init__(self):
        # Map notification type names to their respective handler classes
        self.notification_types: Dict[str, Type[BaseNotification]] = {
            "email": EmailNotification,
            "sms": SMSNotification,
            "push": PushNotification
        }

    @staticmethod
    def _clean_recipients(notification_type: str, recipients: Union[List[str], str]) -> List[str]:
        """
        Cleans the recipients.

        Strips and normalizes recipients depending on the notification type.

        :param notification_type: The type of notification (e.g. 'sms', 'email').
        :param recipients: A comma-separated string or list of recipient identifiers.
        :return: List of cleaned recipient strings.
        """
        cleaned_recipients = set()
        if isinstance(recipients, str):
            recipients = [recipient for recipient in recipients.split(",")]
        for recipient in recipients:
            recipient = recipient.strip()
            if notification_type == "sms":
                recipient = recipient.replace("+", "")
            cleaned_recipients.add(recipient)
        return list(cleaned_recipients)

    def _validate_notification_data(self, notification_data: Any):
        """
        Validate required fields in the notification data.

        Ensures required keys exist and normalizes certain values.

        :param notification_data: Dictionary of notification request data.
        """
        if 'system' not in notification_data:
            raise KeyError("Missing 'system' in notification data")
        if 'notification_type' not in notification_data:
            raise KeyError("Missing 'notification_type' in notification data")
        if 'recipients' not in notification_data:
            raise KeyError("Missing 'recipients' in notification data")
        if 'context' not in notification_data:
            raise KeyError("Missing 'context' in notification data")
        if not isinstance(notification_data['context'], dict):
            raise ValueError("'context' must be a dictionary")

        notification_data['system'] = str(notification_data['system']).lower()
        if 'organisation' in notification_data:
            notification_data['organisation'] = str(notification_data['organisation']).lower()
        notification_data['notification_type'] = str(notification_data['notification_type']).lower()
        notification_data['template'] = str(notification_data.get('template', '')).lower()
        notification_data['recipients'] = self._clean_recipients(
            notification_data['notification_type'], notification_data['recipients'])

    def save_notification(self, notification_data: Dict) -> Optional[Notification]:
        """
        Create a notification instance in the database.

        :param notification_data: Dictionary containing notification parameters.
        :return: Notification object if successful, None otherwise.
        """
        try:
            self._validate_notification_data(notification_data)

            system = SystemService().get(name=notification_data.get('system'))
            if system is None:
                raise Exception("Invalid system")

            organisation = None
            if 'organisation' in notification_data and notification_data['organisation']:
                organisation = OrganisationService().get(name=notification_data['organisation'])
                if organisation is None:
                    raise ValueError("Invalid organisation")

            notification_type = NotificationTypeService().get(name=notification_data.get('notification_type'))
            if notification_type is None:
                raise Exception("Invalid notification type")

            template = TemplateService().get(name=notification_data.get('template'))

            notification = NotificationService().create(
                system=system,
                organisation=organisation,
                unique_identifier=notification_data.get('unique_identifier', ''),
                notification_type=notification_type,
                recipients=notification_data.get('recipients'),
                template=template,
                context=notification_data.get('context'),
                status=StateService().get(name='Pending')
            )
            if notification is None:
                raise Exception("Notification not created")
            return notification

        except Exception as ex:
            logger.exception(f"NotificationManager - save_notification exception: {ex}")
            system = SystemService().get(name=notification_data.get('system'))
            if system:
                self.send_callback_to_system(system, {
                    "status": "failed",
                    "message": str(ex),
                    "unique_identifier": notification_data.get("unique_identifier", None),
                })
            return None

    def _get_notification_instance(self, notification) -> BaseNotification:
        """
        Instantiate a handler class based on the notification type.

        :param notification: Notification object.
        :return: An instance of a BaseNotification subclass.
        """
        notification_type_name = notification.notification_type.name
        notification_class = self.notification_types.get(notification_type_name)
        if not notification_class:
            raise ValueError(f"Unsupported notification type: {notification_type_name}")
        return notification_class(notification)

    @staticmethod
    def _get_provider_class_instance(provider: Provider) -> BaseProvider:
        """
        Instantiate a provider class using its registered name.

        :param provider: Provider object from database.
        :return: Instance of BaseProvider.
        """
        provider_class = PROVIDER_CLASSES.get(provider.class_name, None)
        if provider_class is None:
            raise ValueError(f"Unknown provider class: {provider.class_name}")
        return provider_class(provider.config)

    def send_notification(self, notification: Notification) -> bool:
        """
        Sends a notification using the appropriate handler and active providers.

        :param notification: Notification instance to send.
        :return: True if successfully sent, False otherwise.
        """
        try:
            notification_handler = self._get_notification_instance(notification)
            notification_handler.validate()

            active_providers = notification_handler.active_providers()
            if not active_providers.exists():
                raise Exception(f"No active providers found for {notification.notification_type.name} notifications")

            content = notification_handler.prepare_content()

            for provider in active_providers:
                provider_class_instance = self._get_provider_class_instance(provider)

                if not provider_class_instance.validate_config():
                    logger.warning(f"Invalid configuration for provider: {provider.name}")
                    continue

                send_notification_state = provider_class_instance.send(
                    recipients=notification.recipients, content=content)

                if send_notification_state == State.failed():
                    logger.warning(f"Send notification failed for provider: {provider.name}")
                    continue

                data = {
                    "notification_id": notification.id,
                    "status": send_notification_state,
                    "provider": provider,
                }
                if send_notification_state == State.sent():
                    data["sent_time"] = timezone.now()

                self.update_notification_status(**data)
                return True

            raise Exception("Notification not sent")

        except Exception as ex:
            logger.exception(f"NotificationManager - send_notification exception: {ex}")
            self.update_notification_status(notification_id=notification.id, status=State.failed(), message=str(ex))
            return False

    def update_notification_status(
            self, notification_id: Union[UUID, str], status: State, message: str = None, **kwargs) -> None:
        """
        Updates a notification's status and sends a callback to the system.

        :param notification_id: Notification primary key.
        :param status: New state to set.
        :param message: Optional failure message.
        :param kwargs: Additional fields to update.
        """
        notification = NotificationService().update(pk=notification_id, status=status, **kwargs)
        if notification is None:
            raise Exception("Notification not updated")

        response_data = {
            "notification_id": str(notification.id),
            "unique_identifier": notification.unique_identifier,
            "status": notification.status.name,
        }

        if message is not None:
            response_data["message"] = message

        if notification.status in [State.sent(), State.confirmation_pending()]:
            response_data["sent_time"] = notification.sent_time

        self.send_callback_to_system(system=notification.system, payload=response_data)

    def send_callback_to_system(self, system: System, payload: Dict) -> None:
        """
        Delegates callback dispatch based on the system's configured callback type.

        :param system: System instance.
        :param payload: Payload to send.
        """
        if system.callback_type == "webhook":
            self._send_webhook_callback(system, payload)
        elif system.callback_type == "queue":
            self._send_queue_callback(system, payload)
        else:
            logger.warning(f"Unsupported callback type '{system.callback_type}' for system '{system.name}'.")

    @staticmethod
    def _send_webhook_callback(system: System, payload: Dict) -> None:
        """
        Sends callback payload to a system's webhook endpoint.

        :param system: System instance.
        :param payload: Data payload to send.
        """
        if not system.webhook_url:
            logger.warning(f"Webhook URL not configured for system '{system.name}'.")
            return

        headers = {"Content-Type": "application/json"}
        if system.webhook_auth_token:
            headers["Authorization"] = f"Bearer {system.webhook_auth_token}"

        try:
            response = requests.post(system.webhook_url, json=payload, headers=headers, timeout=5)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Webhook callback to system '{system.name}' failed: {e}")

    @staticmethod
    def _send_queue_callback(system: System, payload: Dict) -> None:
        """
        Sends callback payload to the system's queue.

        :param system: System instance.
        :param payload: Data payload to enqueue.
        """
        queue_name = system.queue_name or f"{system.name}_queue"
        try:
            app.send_task(
                f"{system.name}.handle_notification_response",
                args=(payload,),
                queue=queue_name
            )
        except Exception as e:
            logger.error(f"Queue callback to system '{system.name}' failed: {e}")
