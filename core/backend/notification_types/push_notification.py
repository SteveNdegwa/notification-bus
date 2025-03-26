from typing import Dict

from django.core.exceptions import ValidationError
from django.template import Template, Context

from core.backend.notification_types.base_notification import BaseNotification


class PushNotification(BaseNotification):
    """
    Handles push notifications by preparing message content and validating required fields.
    """

    def prepare_content(self) -> Dict[str, str]:
        """
        Prepares the push notification content by rendering the body with context.
        Title is taken from context with a fallback default.

        :return: Dictionary with keys 'title' and 'body' for the push message.
        """
        body = Template(self.template.body).render(Context(self.context))

        return {
            'title': self.context.get('title', 'Notification'),
            'body': body,
            'data': {}, # optional payload
        }

    def validate(self) -> bool:
        """
        Validates that the push notification has a recipient (typically a device token).

        :raises ValidationError: if no recipient is found.
        :return: True if validation passes.
        """
        if not self.recipient:
            raise ValidationError("Push notification requires a device token")

        return True
