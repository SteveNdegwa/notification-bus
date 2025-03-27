import logging
from typing import Dict, Type, Any
from django.utils import timezone

from core.backend.notification_types.base_notification import BaseNotification
from core.backend.notification_types.email_notification import EmailNotification
from core.backend.notification_types.push_notification import PushNotification
from core.backend.notification_types.sms_notification import SMSNotification

from core.backend.providers.base_provider import BaseProvider
from core.backend.providers.providers_registry import PROVIDER_CLASSES

from core.backend.services import SystemService, NotificationTypeService, TemplateService, NotificationService, \
    StateService

from core.models import Notification, Provider

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
            raise ValueError("Missing 'system' in notification data")
        if not isinstance(notification_data['system'], str):
            notification_data['system'] = str(notification_data['system']).lower()

        if 'notification_type' not in notification_data:
            raise ValueError("Missing 'notification_type' in notification data")
        if not isinstance(notification_data['notification_type'], str):
            notification_data['notification_type'] = str(notification_data['notification_type']).lower()

        if 'recipient' not in notification_data:
            raise ValueError("Missing 'recipient' in notification data")

        if 'context' not in notification_data:
            raise ValueError("Missing 'context' in notification data")
        if not isinstance(notification_data['context'], dict):
            raise ValueError("'context' must be a dictionary")

        # Normalize template name
        notification_data['template_name'] = str(notification_data.get('template_name', '')).lower()

    @staticmethod
    def save_notification(notification_data: Dict) -> Notification:
        """
        Create a notification instance in the database.
        """
        system = SystemService().get(name=notification_data.get('system'))
        if system is None:
            raise Exception("Invalid system")

        notification_type = NotificationTypeService().get(name=notification_data.get('notification_type'))
        if notification_type is None:
            raise Exception("Invalid notification type")

        template = TemplateService().get(name=notification_data.get('template_name'))

        notification = NotificationService().create(
            system=system,
            unique_identifier=notification_data.get('unique_identifier', ''),
            notification_type=notification_type,
            recipient=notification_data.get('recipient'),
            template=template,
            context=notification_data.get('context'),
            status=StateService().get(name='Pending')
        )
        if notification is None:
            raise Exception("Notification not created")
        return notification

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

                provider_class_instance.initialize()

                if provider_class_instance.send(notification.recipient, content):
                    NotificationService().update(
                        pk=notification.id,
                        provider=provider,
                        sent_time=timezone.now(),
                        status=StateService().get(name='Sent')
                    )
                    return True

            # If none of the providers succeed
            raise Exception("Notification not sent")

        except Exception as ex:
            logger.exception("NotificationManager - send_notification exception: %s" % ex)
            NotificationService().update(
                pk=notification.id,
                status=StateService().get(name='Failed')
            )
            return False
