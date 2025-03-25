import re
from typing import Dict

from django.core.exceptions import ValidationError
from django.template import Template, Context

from core.backend.notification_types.base_notification import BaseNotification


class SMSNotification(BaseNotification):
    def prepare_content(self) -> Dict[str, str]:
        body = Template(self.template.content).render(Context(self.data))
        if len(body) > 160:  # Typical SMS character limit
            raise ValidationError("SMS content exceeds 160 characters")
        return {'body': body}

    def validate(self) -> bool:
        phone_pattern = r'^\+?[1-9]\d{1,14}$'
        if not re.match(phone_pattern, self.recipient):
            raise ValidationError("Invalid phone number")
        if not self.template.content:
            raise ValidationError("SMS template requires content")
        return True
