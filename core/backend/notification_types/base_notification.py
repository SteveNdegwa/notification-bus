from abc import ABC, abstractmethod
from typing import Dict

from django.db.models import QuerySet

from core.backend.services import ProviderService
from core.models import Notification

class BaseNotification(ABC):
    """
    Abstract base class for all notification types.
    Defines the shared interface and structure for notifications like Email, SMS, Push, etc.
    """

    def __init__(self, notification: Notification):
        """
        Initialize with a Notification instance.

        :param notification: The Notification model instance containing all relevant data.
        """
        self.notification = notification
        self.template = notification.template
        self.recipients = notification.recipients
        self.context = notification.context

    def active_providers(self) -> QuerySet:
        """
        Fetches all active providers for the given notification type.

        :return: A queryset of Provider objects that are active for the notification type.
        """
        return ProviderService().filter(
            notification_type=self.notification.notification_type,
            is_active=True
        )

    @abstractmethod
    def prepare_content(self) -> Dict[str, str]:
        """
        Prepare and return the content of the notification (e.g., subject, message, etc.).

        :return: A dictionary of content data for the provider to use.
        """
        pass

    @abstractmethod
    def validate(self) -> bool:
        """
        Validates that all required data for this notification is present and correct.

        :return: True if valid, otherwise raises an error or returns False.
        """
        pass
