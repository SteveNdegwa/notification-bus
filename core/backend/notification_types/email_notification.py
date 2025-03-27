import re
from typing import Dict

from django.core.exceptions import ValidationError
from django.template import Template, Context

from core.backend.notification_types.base_notification import BaseNotification


class EmailNotification(BaseNotification):
    """
    Handles the preparation and validation of email notifications.
    Inherits from BaseNotification and implements email-specific logic.
    """

    def prepare_content(self) -> Dict[str, str]:
        """
        Renders the subject and body using Django's Template system and the provided context.
        Assembles a dictionary of email content including optional fields.

        :return: A dictionary with rendered email content and metadata.
        """
        subject = Template(self.template.subject).render(Context(self.context))
        message = Template(self.template.body).render(Context(self.context))

        return {
            'from_address': self.notification.system.default_from_email,
            'reply_to': self.context.get('reply_to', ''),
            'cc': self.context.get('cc', None),
            'bcc': self.context.get('bcc', None),
            'attachments': self.context.get('attachments', None),
            'subject': subject,
            'message': message
        }

    def validate(self) -> bool:
        """
        Validates the recipient email format and ensures subject is present in the template.

        :raises ValidationError: if recipient email is invalid or subject is missing.
        :return: True if validation passes.
        """
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

        for recipient in self.recipient.split(","):
            recipient = recipient.strip()
            if not re.match(email_pattern, recipient):
                raise ValidationError("Invalid email address")

        if not self.template.subject:
            raise ValidationError("Email template requires a subject")

        return True
