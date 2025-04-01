import logging
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Dict, List, Union
from os.path import basename

from core.backend.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)

class GmailSMTPServer(BaseProvider):
    def initialize(self) -> None:
        """
        Initialize the SMTP client using configuration.
        Sets up the connection and performs login.
        """
        self.client = smtplib.SMTP(f"{self.config['host']}:{self.config['port']}")
        self.client.ehlo()
        self.client.starttls()
        self.client.ehlo()
        self.client.set_debuglevel(0)  # Turn off debug output
        self.client.login(self.config['sender'], self.config['password'])

    def send(self, recipients: List[str], content: Dict[str, Union[str, List[str], List[str]]]) -> bool:
        """
        Composes and sends an email using SMTP.

        :param recipients: List of email addresses.
        :param content: Dictionary containing subject, message, cc, bcc, attachments, etc.
        :return: True if email is sent successfully, False otherwise.
        """
        try:
            msg = MIMEMultipart()

            # Set sender details
            from_address = content.get('from_address', '')
            msg['From'] = from_address
            msg['Reply-To'] = content.get('reply_to', '')

            # Format recipient field
            msg['To'] = ",".join(recipients)
            msg['Date'] = formatdate(localtime=True)
            msg['Subject'] = content.get('subject', '')

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

            # Determine if message is HTML or plain text
            message = content.get('message', '')
            if re.search(r'<[^>]+>', message):  # Simple check for HTML content
                msg.attach(MIMEText(message, 'html'))
            else:
                msg.attach(MIMEText(message, 'plain'))

            # add cc and bcc to list of recipients
            toaddrs = recipients + cc + bcc

            # Handle file attachments
            attachments = content.get('attachment', [])
            for f in attachments:
                with open(f, "rb") as fil:
                    part = MIMEApplication(fil.read(), Name=basename(f))
                part['Content-Disposition'] = f'attachment; filename="{basename(f)}"'
                msg.attach(part)

            # Send the composed email
            self.client.sendmail(from_addr=from_address, to_addrs=toaddrs, msg=msg.as_string())
            self.client.close()
            return True

        except Exception as ex:
            logger.exception("GmailSMTPServer - send exception: %s", ex)
            return False

    def validate_config(self) -> bool:
        """
        Checks if the required SMTP config values are provided.
        """
        required_keys = ['host', 'port', 'sender', 'password']
        missing_keys = [key for key in required_keys if key not in self.config]
        if missing_keys:
            logger.error("GmailSMTPServer - Missing config keys: %s", ", ".join(missing_keys))
            return False
        return True

