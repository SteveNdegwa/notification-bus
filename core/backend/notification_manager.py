import logging

from typing import Dict, Type, Any

from django.utils import timezone

from core.backend.notification_types.base_notification import BaseNotification
from core.backend.notification_types.email_notification import EmailNotification
from core.backend.notification_types.push_notification import PushNotification
from core.backend.notification_types.sms_notification import SMSNotification
from core.backend.providers.base_provider import BaseProvider
from core.backend.services import SystemService, NotificationTypeService, TemplateService, NotificationService, \
    StateService
from core.models import Notification

logger = logging.getLogger(__name__)

class NotificationManager:
    def __init__(self):
        self.notification_types: Dict[str, Type[BaseNotification]] = {
            "email": EmailNotification,
            "sms": SMSNotification,
            "push": PushNotification
        }

    def call_class_method(self, class_instance, function_name, **kwargs):
        try:
            return getattr(class_instance, function_name)(**kwargs)
        except Exception as e:
            logger.exception('%s call_class_method Exception: %s', self.__class__.__name__, e)
            return None

    def get_class_instance(self, class_name, **kwargs):
        try:
            if class_name in globals() and hasattr(globals()[class_name], '__class__'):
                class_object = globals()[class_name]
                return class_object(**kwargs)
        except Exception as e:
            logger.exception('%s get_class_instance Exception: %s', self.__class__.__name__, e)
        return None

    @staticmethod
    def validate_notification_data(notification_data: Any):
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

        if 'data' not in notification_data:
            raise ValueError("Missing 'data' in notification data")
        if not isinstance(notification_data['data'], dict):
            raise ValueError("'data' must be a dictionary")

        notification_data['template_name'] = str(notification_data.get('template_name', '')).upper()

    @staticmethod
    def save_notification(notification_data: Dict) -> Notification:
        system = SystemService().get(name=notification_data.get('system'))
        if system is None:
            raise Exception("Invalid system")
        notification_type = NotificationTypeService().get(name=notification_data.get('notification_type'))
        if notification_type is None:
            raise Exception("Invalid notification type")
        template = TemplateService().get(name=notification_data.get('template_name'))
        notification = NotificationService().create(
            system=system, notification_type=notification_type, recipient=notification_data.get('recipient'),
            template=template, data=notification_data.get('data'), status=StateService().get(name='Pending')
        )
        if notification is None:
            raise Exception("Notification not created")
        return notification

    def get_notification_instance(self, notification) -> BaseNotification:
        notification_type_name = notification.notification_type.name
        notification_class = self.notification_types.get(notification_type_name)
        if not notification_class:
            raise ValueError("Unsupported notification type: %s" % notification_type_name)
        return notification_class(notification)

    def send_notification(self, notification: Notification) -> bool:
        try:
            notification_handler = self.get_notification_instance(notification)
            notification_handler.validate()
            active_providers = notification_handler.active_providers()
            if not active_providers.exists():
                raise Exception("No active providers found for %s notifications" % notification.notification_type.name)
            content = notification_handler.prepare_content()
            for provider in active_providers:
                provider_handler: BaseProvider = self.get_class_instance(provider.class_name, provider=provider)
                if not provider_handler.validate_config():
                    logger.error("Invalid configuration for provider: %s" % provider.name)
                    continue
                if provider_handler.send(notification.recipient, content):
                    NotificationService().update(
                        pk=notification.id, sent_time=timezone.now(), status=StateService().get(name='Sent'))
                    return True
            raise Exception("Notification not sent")
        except Exception as ex:
            logger.exception("NotificationManager - send_notification exception: %s" % ex)
            NotificationService().update(pk=notification.id, status=StateService().get(name='Failed'))
            return False




