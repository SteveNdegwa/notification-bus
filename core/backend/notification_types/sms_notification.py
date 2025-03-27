import re
from typing import Dict

from django.core.exceptions import ValidationError
from django.template import Template, Context

from core.backend.notification_types.base_notification import BaseNotification


class SMSNotification(BaseNotification):
    """
    Handles SMS notifications by rendering template content and validating input data.
    """

    def prepare_content(self) -> Dict[str, str]:
        """
        Renders the SMS body content using Django's templating engine and checks character limit.

        :raises ValidationError: If rendered SMS body exceeds 160 characters.
        :return: A dictionary with the SMS body.
        """
        body = Template(self.template.content).render(Context(self.context))
        if len(body) > 160:  # Typical SMS character limit
            raise ValidationError("SMS content exceeds 160 characters")

        return {'sender_id': self.notification.system.name, 'body': body}

    def validate(self) -> bool:
        """
        Validates that the SMS has a valid phone number and that template content is present.

        :raises ValidationError: If phone number or template content is invalid.
        :return: True if validation passes.
        """
        phone_pattern = r'^\+?[1-9]\d{1,14}$'  # E.164 format
        for recipient in self.recipient.split(","):
            recipient = recipient.strip()
            if not re.match(phone_pattern, recipient):
                raise ValidationError("Invalid phone number")

        if not self.template.content:
            raise ValidationError("SMS template requires content")

        return True
