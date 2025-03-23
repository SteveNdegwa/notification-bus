from abc import ABC, abstractmethod
from typing import Dict

from django.db.models import QuerySet

from core.backend.services import ProviderService
from core.models import Notification


class BaseNotification(ABC):
    def __init__(self, notification: Notification):
        self.notification = notification
        self.template = notification.template
        self.recipient = notification.recipient
        self.data = notification.data

    def active_providers(self) -> QuerySet:
        return ProviderService().filter(notification_type=self.notification.notification_type, is_active=True)

    @abstractmethod
    def prepare_content(self) -> Dict[str, str]:
        pass

    @abstractmethod
    def validate(self) -> bool:
        pass
