from typing import Dict

from django.core.exceptions import ValidationError
from django.template import Template, Context

from core.backend.notification_types.base_notification import BaseNotification


class PushNotification(BaseNotification):
    def prepare_content(self) -> Dict[str, str]:
        body = Template(self.template.content).render(Context(self.data))
        return {
            'title': self.data.get('title', 'Notification'),
            'body': body
        }

    def validate(self) -> bool:
        if not self.recipient:  # Device token
            raise ValidationError("Push notification requires a device token")
        return True