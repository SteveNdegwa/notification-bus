import base64
import logging
import re
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Dict, List

from core.backend.email_attachments import get_email_attachments
from core.backend.providers.base_provider import BaseProvider
from core.models import Notification

logger = logging.getLogger(__name__)

class GmailSMTPServer(BaseProvider):
    supports_email_threads = True

    def validate_config(self) -> bool:
        """
        Checks if the required SMTP config values are provided.
        """
        required_keys = ['host', 'port', 'sender', 'password']
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            self.set_last_exception("Missing config keys: %s" % ", ".join(missing_keys))
            logger.error("GmailSMTPServer - %s", self.last_exception)
            return False
        return True

    def send(self, recipients: List[str], content: Dict) -> str:
        """
        Composes and sends an email using SMTP.

        :param recipients: List of email addresses.
        :param content: Dictionary containing subject, message, cc, bcc, attachments, etc.
        :return: Sent status if email is sent successfully else Failed status.
        """
        try:
            msg = MIMEMultipart()

            # Set sender details
            from_address = self.config.get('from_address', '')
            msg['From'] = from_address
            msg['Reply-To'] = self.config.get('reply_to', '')

            # Format recipient field
            msg['To'] = ",".join(recipients)
            msg['Date'] = formatdate(localtime=True)
            msg['Subject'] = content.get('subject', '')
            if content.get('email_thread_id'):
                msg['Message-ID'] = content['message_id']
                if content.get('in_reply_to'):
                    msg['In-Reply-To'] = content['in_reply_to']
                    msg['References'] = ' '.join(content['references'])

            # Process CC addresses
            cc = content.get('cc')
            if isinstance(cc, str):
                cc = [email.strip() for email in cc.split(",")]
            elif not cc:
                cc = []

            # Process BCC addresses
            bcc = content.get('bcc')
            if isinstance(bcc, str):
                bcc = [email.strip() for email in bcc.split(",")]
            elif not bcc:
                bcc = []

            if cc:
                msg['Cc'] = ",".join(cc)

            # Determine if the message is HTML or plain text
            message = content.get('message', '')
            if re.search(r'<[^>]+>', message):  # Simple check for HTML content
                msg.attach(MIMEText(message, 'html'))
            else:
                msg.attach(MIMEText(message, 'plain'))

            # add cc and bcc to the list of recipients
            toaddrs = recipients + cc + bcc

            # Handle file attachments
            for attachment in get_email_attachments(content):
                main_type, sub_type = attachment['contentType'].split('/', 1)
                part = MIMEBase(main_type, sub_type)
                part.set_payload(base64.b64decode(attachment['contentBytes']))
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment', filename=attachment['name'])
                msg.attach(part)

            # Send the composed email
            server = smtplib.SMTP(
                self.config['host'], int(self.config['port']), timeout=self.config.get('timeout', 30),
            )
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.set_debuglevel(0)  # Turn off debug output
            server.login(self.config['sender'], self.config['password'])
            server.sendmail(from_addr=from_address, to_addrs=toaddrs, msg=msg.as_string())
            server.close()
            self.sent_message_id = content.get('message_id', '')

            return Notification.Status.SENT

        except Exception as ex:
            logger.exception("GmailSMTPServer - send exception: %s", ex)
            self.set_last_exception(ex)
            return Notification.Status.FAILED
