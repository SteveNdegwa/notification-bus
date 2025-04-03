from utils.service_base import ServiceBase
from core.models import State, NotificationType, System, Template, Provider, Notification, Organisation


class StateService(ServiceBase):
    manager = State.objects

class NotificationTypeService(ServiceBase):
    manager = NotificationType.objects

class SystemService(ServiceBase):
    manager = System.objects

class OrganisationService(ServiceBase):
    manager = Organisation.objects

class TemplateService(ServiceBase):
    manager = Template.objects

class ProviderService(ServiceBase):
    manager = Provider.objects

class NotificationService(ServiceBase):
    manager = Notification.objects