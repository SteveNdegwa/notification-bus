import logging
from typing import Dict, List, Union
from urllib.parse import quote

import requests

from core.backend.providers.base_provider import BaseProvider
from core.backend.email_attachments import get_email_attachments, normalize_attachments
from core.backend.notification_manager import ThreadProviderPending
from core.models import Notification

logger = logging.getLogger(__name__)


class OutlookMailProvider(BaseProvider):
    supports_email_threads = True
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
        return {
            "@odata.type": "#microsoft.graph.fileAttachment",
            **normalize_attachments(attachment)[0],
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

            attachments = get_email_attachments(content)
            if attachments:
                message["attachments"] = [self._build_attachment(attachment) for attachment in attachments]

            if content.get("email_thread_id"):
                return self._send_thread_message(access_token, from_address, message, content)

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

        except ThreadProviderPending:
            raise
        except Exception as ex:
            logger.exception("OutlookMailProvider - send exception: %s", ex)
            self.set_last_exception(ex)
            return Notification.Status.FAILED

    def _send_thread_message(self, access_token, from_address, message, content):
        """Keep an immutable Graph ID so later notifications can reply to it."""
        if not self.config.get("save_to_sent_items", True):
            raise ValueError("Email threads require save_to_sent_items=true")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Prefer": 'IdType="ImmutableId"',
        }
        timeout = self.config.get("timeout", 30)
        messages_url = (
            f"{self.config.get('graph_url', self.GRAPH_URL)}/users/{quote(from_address, safe='')}/messages"
        )
        parent_id = content.get("parent_provider_message_id")
        if content.get("in_reply_to") and not parent_id:
            raise ValueError("The Outlook thread is missing its parent message ID")

        if parent_id:
            response = requests.post(
                f"{messages_url}/{quote(parent_id, safe='')}/createReply",
                headers=headers, json={}, timeout=timeout,
            )
            # Graph's accepted send is asynchronous; its immutable Sent Items
            # copy can briefly be unavailable immediately after the first send.
            if response.status_code in (404, 429, 503, 504):
                raise ThreadProviderPending(f"Outlook reply temporarily unavailable ({response.status_code})")
        else:
            response = requests.post(messages_url, headers=headers, json=message, timeout=timeout)
        response.raise_for_status()
        draft = response.json()
        draft_id = draft.get("id")
        if not draft_id:
            raise ValueError("Microsoft Graph did not return a draft message ID")
        draft_url = f"{messages_url}/{quote(draft_id, safe='')}"

        if parent_id:
            # A reply to our own sent message defaults to the sender. Explicitly
            # replace recipients and body with those from this notification.
            update = {key: value for key, value in message.items() if key != "attachments"}
            response = requests.patch(draft_url, headers=headers, json=update, timeout=timeout)
            response.raise_for_status()
            for attachment in message.get("attachments", []):
                response = requests.post(
                    f"{draft_url}/attachments", headers=headers, json=attachment, timeout=timeout,
                )
                response.raise_for_status()

        response = requests.post(f"{draft_url}/send", headers=headers, timeout=timeout)
        if response.status_code != 202:
            raise ValueError(f"Microsoft Graph draft send failed with status {response.status_code}: {response.text}")
        self.sent_provider_message_id = draft_id
        self.sent_message_id = draft.get("internetMessageId", "")
        return Notification.Status.SENT
