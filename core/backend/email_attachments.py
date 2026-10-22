"""Provider-independent email attachments, represented as Base64 JSON objects."""

import base64
import binascii
import mimetypes
import re
from pathlib import Path


def normalize_attachments(attachments):
    """
    Normalize email attachments into provider-independent Base64 objects.

    Each input may be a worker-local file path or an object containing a name,
    optional content type, and either Base64 ``contentBytes`` or raw ``content``.
    A single path or object is accepted as shorthand for a list.

    :param attachments: Attachment objects, file paths, a single attachment, or None.
    :return: Normalized objects containing name, contentType, and Base64 contentBytes.
    :rtype: List[Dict[str, str]]
    :raises ValueError: If the collection, metadata, content, or Base64 data is invalid.
    :raises OSError: If a referenced worker-local file cannot be read.
    """
    if attachments is None:
        return []
    if isinstance(attachments, (str, dict)):
        attachments = [attachments]
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list of objects or file paths")

    normalized = []
    for attachment in attachments:
        if isinstance(attachment, str):
            path = Path(attachment)
            attachment = {
                "name": path.name,
                "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        if not isinstance(attachment, dict):
            raise ValueError("Each attachment must be an object or file path")

        name = attachment.get("name", "attachment")
        if not isinstance(name, str) or not name.strip() or "\r" in name or "\n" in name:
            raise ValueError("Attachment name must be a non-empty string without line breaks")
        content_type = (
            attachment.get("contentType") or attachment.get("content_type")
            or mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
        if not isinstance(content_type, str) or not re.fullmatch(r"[\w.+-]+/[\w.+-]+", content_type):
            raise ValueError(f"Attachment '{name}' has an invalid contentType")

        content_bytes = attachment.get("contentBytes")
        if content_bytes is not None:
            if not isinstance(content_bytes, str):
                raise ValueError(f"Attachment '{name}' contentBytes must be a Base64 string")
            try:
                raw_content = base64.b64decode(content_bytes, validate=True)
            except (ValueError, binascii.Error) as ex:
                raise ValueError(f"Attachment '{name}' contentBytes must be valid Base64") from ex
        elif "content" in attachment:
            raw_content = attachment["content"]
            if isinstance(raw_content, str):
                raw_content = raw_content.encode("utf-8")
            if not isinstance(raw_content, bytes):
                raise ValueError(f"Attachment '{name}' content must be text or bytes")
        else:
            raise ValueError(f"Attachment '{name}' must include contentBytes or content")

        normalized.append({
            "name": name,
            "contentType": content_type,
            "contentBytes": base64.b64encode(raw_content).decode("ascii"),
        })
    return normalized


def get_email_attachments(content):
    """
    Read and normalize attachments from an email content dictionary.

    The plural ``attachments`` key takes precedence, while the legacy singular
    ``attachment`` key remains supported when the plural key is absent.

    :param content: Email content or template context containing attachment data.
    :type content: Dict
    :return: Provider-independent normalized attachment objects.
    :rtype: List[Dict[str, str]]
    :raises ValueError: If any supplied attachment is invalid.
    :raises OSError: If a referenced worker-local file cannot be read.
    """
    attachments = content.get("attachments")
    if attachments is None:
        attachments = content.get("attachment")
    return normalize_attachments(attachments)
