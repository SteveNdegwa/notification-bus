import re
from typing import Dict

from django.core.exceptions import ValidationError
from django.template import Template, Context

from core.backend.notification_types.base_notification import BaseNotification


class EmailNotification(BaseNotification):
    def prepare_content(self) -> Dict[str, str]:
        subject = Template(self.template.subject).render(Context(self.data))
        body = Template(self.template.content).render(Context(self.data))
        return {
            'subject': subject,
            'body': body
        }

    def validate(self) -> bool:
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, self.recipient):
            raise ValidationError("Invalid email address")
        if not self.template.subject:
            raise ValidationError("Email template requires a subject")
        return True
