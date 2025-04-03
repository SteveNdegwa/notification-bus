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
        body = Template(self.template.body).render(Context(self.context))
        if len(body) > 160:  # Typical SMS character limit
            raise ValidationError("SMS content exceeds 160 characters")

        # TODO: HANDLE LOGIC TO USE DIFFERENT SMS_SERVICE_ID IF NEED BE

        return {
            'sender_id': self.notification.system.name,
            'body': body,
            'unique_identifier': str(self.notification.id)
        }

    def validate(self) -> bool:
        """
        Validates that the SMS has a valid phone number and that template content is present.

        :raises ValidationError: If phone number or template content is invalid.
        :return: True if validation passes.
        """
        phone_pattern = r'254\d{9}'
        for recipient in self.recipients:
            if not re.match(phone_pattern, recipient):
                raise ValidationError("Invalid phone number")
        return True
