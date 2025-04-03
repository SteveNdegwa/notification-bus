import logging
from typing import Dict, Type, Any, Tuple, Optional, Union
from uuid import UUID

from django.utils import timezone

from core.backend.notification_types.base_notification import BaseNotification
from core.backend.notification_types.email_notification import EmailNotification
from core.backend.notification_types.push_notification import PushNotification
from core.backend.notification_types.sms_notification import SMSNotification

from core.backend.providers.base_provider import BaseProvider
from core.backend.providers.providers_registry import PROVIDER_CLASSES

from core.backend.services import SystemService, NotificationTypeService, TemplateService, NotificationService, \
    StateService, OrganisationService

from core.models import Notification, Provider, State
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
    def validate_notification_data(notification_data: Any):
        """
        Validate required fields in the notification data.
        Normalize values for consistency.
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

        # Normalize data
        notification_data['system'] = str(notification_data['system']).lower()

        # Normalize organisation
        if 'organisation' in notification_data:
            notification_data['organisation'] = str(notification_data['organisation']).lower()

        # Normalize notification type
        notification_data['notification_type'] = str(notification_data['notification_type']).lower()

        # Normalize template name
        notification_data['template'] = str(notification_data.get('template', '')).lower()

        # Normalize recipients
        if isinstance(notification_data['recipients'], str):
            notification_data['recipients'] = [
                recipient.strip() for recipient in notification_data['recipients'].split(",")]

    def save_notification(self, notification_data: Dict) -> Tuple[Optional[Notification], Optional[str]]:
        """
        Create a notification instance in the database.
        """
        try:
            self.validate_notification_data(notification_data)

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
            return notification, None
        except Exception as ex:
            logger.exception("NotificationManager - save_notification exception: %s" % ex)
            return None, str(ex)

    def get_notification_instance(self, notification) -> BaseNotification:
        """
        Instantiate a handler class based on the notification type.
        """
        notification_type_name = notification.notification_type.name
        notification_class = self.notification_types.get(notification_type_name)
        if not notification_class:
            raise ValueError("Unsupported notification type: %s" % notification_type_name)
        return notification_class(notification)

    @staticmethod
    def get_provider_class_instance(provider: Provider) -> BaseProvider:
        """
        Dynamically instantiate a provider class using its name from the database
        and pass its configuration dictionary to the constructor.
        """
        provider_class = PROVIDER_CLASSES.get(provider.class_name, None)
        if provider_class is None:
            raise ValueError(f"Unknown provider class: {provider.class_name}")
        return provider_class(provider.config)

    def send_notification(self, notification: Notification) -> bool:
        """
        Sends a notification using the appropriate handler and active providers.
        Updates status to 'Sent' or 'Failed' accordingly.
        """
        try:
            notification_handler = self.get_notification_instance(notification)
            notification_handler.validate()

            active_providers = notification_handler.active_providers()
            if not active_providers.exists():
                raise Exception("No active providers found for %s notifications" % notification.notification_type.name)

            content = notification_handler.prepare_content()

            for provider in active_providers:
                provider_class_instance = self.get_provider_class_instance(provider)

                if not provider_class_instance.validate_config():
                    logger.error("Invalid configuration for provider: %s" % provider.name)
                    continue

                send_notification_state = provider_class_instance.send(
                    recipients=notification.recipients, content=content)

                if send_notification_state == State.failed():
                    logger.error("Send notification failed for provider: %s" % provider.name)
                    continue

                data = {
                    "notification_id": notification.id,
                    "status": send_notification_state,
                    "provider": provider,
                }
                if send_notification_state == State.sent(): data["sent_time"] = timezone.now()
                self.update_notification_status(**data)

                return True

            # If none of the providers succeed
            raise Exception("Notification not sent")

        except Exception as ex:
            logger.exception("NotificationManager - send_notification exception: %s" % ex)
            self.update_notification_status(notification_id=notification.id, status=State.failed(), message=str(ex))
            return False

    def update_notification_status(
            self, notification_id: Union[UUID, str], status: State, message: str = None, **kwargs) -> None:
        """
        Updates a notification's status and any other provided fields.
        Queues a callback to the notification's system.
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

        # Queue callback
        self.queue_notification_callback(system_name=notification.system.name, response_data=response_data)

        return

    @staticmethod
    def queue_notification_callback(system_name:str, response_data: Dict) -> None:
        """
        Queues a notification callback
        """
        app.send_task(
            f'{system_name}.handle_send_notification_response',
            args=(response_data,),
            queue=f'{system_name}_queue'
        )
        return

