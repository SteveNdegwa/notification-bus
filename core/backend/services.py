from utils.service_base import ServiceBase
from core.models import NotificationType, System, Template, Provider, Notification, ProviderCallback, \
    SystemCallback


class NotificationTypeService(ServiceBase):
    manager = NotificationType.objects

class SystemService(ServiceBase):
    manager = System.objects

class TemplateService(ServiceBase):
    manager = Template.objects

class ProviderService(ServiceBase):
    manager = Provider.objects

class NotificationService(ServiceBase):
    manager = Notification.objects

class ProviderCallbackService(ServiceBase):
    manager = ProviderCallback.objects

class SystemCallbackService(ServiceBase):
    manager = SystemCallback.objects
