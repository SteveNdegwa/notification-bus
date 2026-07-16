import base64
import logging
import mimetypes
from os.path import basename
from typing import Dict, List, Union

import requests

from core.backend.providers.base_provider import BaseProvider
from core.models import Notification

logger = logging.getLogger(__name__)


class OutlookMailProvider(BaseProvider):
    GRAPH_URL = "https://graph.microsoft.com/v1.0"
    SCOPE = "https://graph.microsoft.com/.default"

    def validate_config(self) -> bool:
        required_keys = ["tenant_id", "client_id", "client_secret", "from_address"]
        missing_keys = [key for key in required_keys if not self.config.get(key)]
        if missing_keys:
            self.set_last_exception("Missing config keys: %s" % ", ".join(missing_keys))
            logger.error("OutlookMailProvider - %s", self.last_exception)
            return False
        return True

    def _get_access_token(self) -> str:
        tenant_id = self.config["tenant_id"]
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        response = requests.post(
            token_url,
            data={
                "client_id": self.config["client_id"],
                "client_secret": self.config["client_secret"],
                "scope": self.config.get("scope", self.SCOPE),
                "grant_type": "client_credentials",
            },
            timeout=self.config.get("timeout", 30),
        )
        response.raise_for_status()

        token_response = response.json()
        access_token = token_response.get("access_token")
        if not access_token:
            raise ValueError("Microsoft token response did not include an access token")

        return access_token

    @staticmethod
    def _normalize_recipients(recipients: Union[str, List[str], None]) -> List[Dict[str, Dict[str, str]]]:
        if isinstance(recipients, str):
            recipients = [email.strip() for email in recipients.split(",") if email.strip()]
        elif not recipients:
            recipients = []

        return [{"emailAddress": {"address": email}} for email in recipients]

    @staticmethod
    def _build_attachment(attachment: Union[str, Dict[str, str]]) -> Dict[str, str]:
        if isinstance(attachment, str):
            content_type = mimetypes.guess_type(attachment)[0] or "application/octet-stream"
            with open(attachment, "rb") as file_obj:
                content_bytes = base64.b64encode(file_obj.read()).decode("utf-8")

            return {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": basename(attachment),
                "contentType": content_type,
                "contentBytes": content_bytes,
            }

        name = attachment.get("name", "attachment")
        content_bytes = attachment.get("contentBytes")
        if not content_bytes and attachment.get("content"):
            raw_content = attachment["content"]
            if isinstance(raw_content, str):
                raw_content = raw_content.encode("utf-8")
            content_bytes = base64.b64encode(raw_content).decode("utf-8")

        if not content_bytes:
            raise ValueError(f"Attachment '{name}' must include contentBytes or content")

        return {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": attachment.get("contentType") or attachment.get("content_type", "application/octet-stream"),
            "contentBytes": content_bytes,
        }

    def send(self, recipients: List[str], content: Dict[str, Union[str, List[str], List[Dict[str, str]]]]) -> str:
        try:
            access_token = self._get_access_token()
            from_address = self.config["from_address"]

            message = {
                "subject": content.get("subject", ""),
                "body": {
                    "contentType": "HTML",
                    "content": content.get("message", ""),
                },
                "toRecipients": self._normalize_recipients(recipients),
                "ccRecipients": self._normalize_recipients(content.get("cc")),
                "bccRecipients": self._normalize_recipients(content.get("bcc")),
            }

            reply_to = self.config.get("reply_to")
            if reply_to:
                message["replyTo"] = self._normalize_recipients(reply_to)

            attachments = content.get("attachments") or content.get("attachment") or []
            if isinstance(attachments, (str, dict)):
                attachments = [attachments]
            if attachments:
                message["attachments"] = [self._build_attachment(attachment) for attachment in attachments]

            response = requests.post(
                f"{self.config.get('graph_url', self.GRAPH_URL)}/users/{from_address}/sendMail",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "message": message,
                    "saveToSentItems": self.config.get("save_to_sent_items", True),
                },
                timeout=self.config.get("timeout", 30),
            )

            if response.status_code == 202:
                return Notification.Status.SENT

            self.set_last_exception("send failed with status %s: %s" % (response.status_code, response.text))
            logger.error("OutlookMailProvider - %s", self.last_exception)
            return Notification.Status.FAILED

        except Exception as ex:
            logger.exception("OutlookMailProvider - send exception: %s", ex)
            self.set_last_exception(ex)
            return Notification.Status.FAILED
