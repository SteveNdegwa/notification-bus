import logging
import os
import re
from datetime import timedelta
from email.utils import make_msgid
from typing import Dict, Type, Any, Optional, Union, List
from uuid import UUID

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Q, When
from django.utils import timezone

from core.backend.notification_types.base_notification import BaseNotification
from core.backend.notification_types.email_notification import EmailNotification
from core.backend.notification_types.push_notification import PushNotification
from core.backend.notification_types.sms_notification import SMSNotification

from core.backend.providers.base_provider import BaseProvider

from core.backend.services import SystemService, NotificationTypeService, TemplateService, NotificationService

from core.models import (
    EmailThread, Notification, NotificationOverride, NotificationTry,
    Provider, ProviderConfig, ProviderConfigFailureAlert, System, SystemCallback,
)

logger = logging.getLogger(__name__)

PROVIDER_CONFIG_FAILURE_ALERT_THRESHOLD = 3
PROVIDER_CONFIG_FAILURE_ALERT_COOLDOWN = timedelta(hours=3)


class ThreadNotFound(ValueError):
    """The authenticated system has no thread matching the requested ID."""


class ThreadDeliveryPending(Exception):
    """An earlier notification must finish before this message can be sent."""


class ThreadProviderPending(Exception):
    """The provider has not yet made an accepted message available for replies."""


class NotificationManager:
    """
    Handles the creation, validation, sending, and callback of notifications
    across multiple types (email, SMS, push) using appropriate providers.
    """

    def __init__(self):
        """
        Initialize the notification manager with supported notification types.
        """
        self.notification_types: Dict[str, Type[BaseNotification]] = {
            "email": EmailNotification,
            "sms": SMSNotification,
            "push": PushNotification
        }

    @staticmethod
    def _clean_recipients(notification_type: str, recipients: Union[List[str], str]) -> List[str]:
        """
        Normalize and clean the recipients' list based on the notification type.

        :param notification_type: Type of the notification (e.g., 'sms', 'email').
        :type notification_type: str
        :param recipients: Recipients as a list or comma-separated string.
        :type recipients: Union[List[str], str]
        :return: Cleaned list of recipients.
        :rtype: List[str]
        """
        cleaned_recipients = set()
        if isinstance(recipients, str):
            recipients = [recipient for recipient in recipients.split(",")]
        for recipient in recipients:
            recipient = re.sub(r"\s+", "", recipient)
            if notification_type == "sms":
                recipient = recipient.replace("+", "")
            cleaned_recipients.add(recipient)
        return list(cleaned_recipients)

    def _validate_notification_data(self, notification_data: Any):
        """
        Validates and normalizes notification data.

        :param notification_data: Notification request dictionary.
        :type notification_data: Any
        :raises KeyError: If required keys are missing.
        :raises ValueError: If 'context' is not a dictionary.
        """
        required_fields = ['system', 'notification_type', 'context']
        for field in required_fields:
            if field not in notification_data:
                raise KeyError(f"Missing '{field}' in notification data")
        if not isinstance(notification_data['context'], dict):
            raise ValueError("'context' must be a dictionary")
        if not notification_data.get('recipients') and not notification_data.get('user_ids'):
            raise KeyError("Missing 'recipients' or 'user_ids' in notification data")

        # Normalize fields
        notification_data['system'] = str(notification_data['system'])
        notification_data['notification_type'] = str(notification_data['notification_type']).lower()
        notification_data['template'] = str(notification_data.get('template', '')).lower()
        remote_code = notification_data.get('organization_remote_code')
        if remote_code is not None and not isinstance(remote_code, str):
            raise ValueError("organization_remote_code must be a string")
        notification_data['organization_remote_code'] = (remote_code or '').strip()
        if notification_data.get('recipients'):
            notification_data['recipients'] = self._clean_recipients(
                notification_data['notification_type'], notification_data['recipients'])
        else:
            notification_data['recipients'] = []
        notification_data['user_ids'] = notification_data.get('user_ids') or []

    def save_notification(
        self, notification_data: Dict, *, email_thread: Optional[EmailThread] = None, raise_errors: bool = False,
    ) -> Optional[Notification]:
        """
        Create a notification, optionally attached to an existing email thread.

        Threaded notifications use the thread's system and require a valid email
        template. Supplied email addresses are validated before commit, while
        user IDs can be resolved later. Validation failures roll back the notification.

        :param notification_data: Notification request dictionary to validate and normalize.
        :type notification_data: Dict
        :param email_thread: Existing email thread to attach, or None for an ordinary notification.
        :type email_thread: Optional[EmailThread]
        :param raise_errors: Raise errors instead of returning None when saving fails.
        :type raise_errors: bool
        :return: The created Notification object, or None on failure.
        :rtype: Optional[Notification]
        :raises ValueError: If validation or persistence fails and raise_errors is True.
        :raises ValidationError: If threaded email validation fails and raise_errors is True.
        """
        try:
            with transaction.atomic():
                self._validate_notification_data(notification_data)
                if email_thread is not None and notification_data['notification_type'] != 'email':
                    raise ValueError("Threads are only supported for email notifications")

                system = self._get_system(notification_data.get('system'))
                if system is None:
                    raise Exception("Invalid system")

                notification_type = NotificationTypeService().get(name=notification_data['notification_type'])
                if notification_type is None:
                    raise Exception("Invalid notification type")

                template = self._get_template_for_system(
                    template_code=notification_data.get('template'),
                    system=system,
                    notification_type=notification_type,
                    organization_remote_code=notification_data['organization_remote_code'],
                )
                if email_thread is not None and template is None:
                    raise ValueError("A valid email template is required")

                recipient_resolution_status = Notification.RecipientResolutionStatus.NOT_REQUIRED
                notification_status = Notification.Status.PENDING
                if notification_data['user_ids'] and not notification_data['recipients']:
                    recipient_resolution_status = Notification.RecipientResolutionStatus.PENDING
                    notification_status = Notification.Status.GETTING_RECIPIENTS

                notification = NotificationService().create(
                    system=system,
                    organization_remote_code=notification_data['organization_remote_code'],
                    unique_identifier=notification_data.get('unique_identifier', ''),
                    notification_type=notification_type,
                    user_ids=notification_data['user_ids'],
                    recipients=notification_data['recipients'],
                    recipient_resolution_status=recipient_resolution_status,
                    template=template,
                    context=notification_data['context'],
                    email_thread=email_thread,
                    status=notification_status,
                )
                if notification is None:
                    raise Exception("Notification not created")
                if email_thread is not None:
                    EmailNotification(notification).validate()

                return notification

        except Exception as ex:
            logger.exception(f"NotificationManager - save_notification exception: {ex}")
            if raise_errors:
                if isinstance(ex, ValidationError):
                    raise
                raise ValueError(str(ex)) from ex
            system = self._get_system(notification_data.get('system'))
            if system and system.callback_enabled:
                self.send_callback_to_system(system, {
                    "status": "failed",
                    "message": str(ex),
                    "unique_identifier": notification_data.get("unique_identifier", None),
                })
            return None

    @staticmethod
    def create_email_thread(system: System, name: str) -> EmailThread:
        """
        Validate the thread name and persist a thread for the given system.

        Strip surrounding whitespace from the name, which becomes the fixed
        subject for every email in the thread.

        :param system: Authenticated system that owns the email thread.
        :type system: System
        :param name: Thread subject containing 1 to 255 characters after trimming.
        :type name: str
        :return: The created EmailThread object.
        :rtype: EmailThread
        :raises ValueError: If the name is invalid, empty, too long, or contains line breaks.
        """
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
            raise ValueError("thread_name must contain between 1 and 255 characters")
        if "\r" in name or "\n" in name:
            raise ValueError("thread_name cannot contain line breaks")
        return EmailThread.objects.create(system=system, name=name.strip())

    def save_email_thread(self, data: Dict, system: System) -> Optional[EmailThread]:
        """
        Create or lock a system-scoped email thread from the request options.

        Create a thread when create_thread is True, or lock the existing thread
        identified by email_thread_id. Return None when neither is requested.
        Call inside a transaction that also saves the notification so a new
        thread can roll back and an existing thread stays locked through persistence.

        :param data: Notification request containing the thread options and notification type.
        :type data: Dict
        :param system: Authenticated system that owns or is creating the email thread.
        :type system: System
        :return: The created or locked EmailThread object, or None for an ordinary send.
        :rtype: Optional[EmailThread]
        :raises ValueError: If thread options, notification type, name, or UUID are invalid.
        :raises ThreadNotFound: If the requested thread does not belong to the system or does not exist.
        """
        create = data.get("create_thread", False)
        thread_id = data.get("email_thread_id")
        if not isinstance(create, bool):
            raise ValueError("create_thread must be a boolean")
        if create and thread_id is not None:
            raise ValueError("create_thread and email_thread_id cannot be used together")
        if "thread_name" in data and not create:
            raise ValueError("thread_name is only allowed with create_thread=true")
        if not create and thread_id is None:
            return None
        if str(data.get("notification_type", "")).lower() != "email":
            raise ValueError("Threads are only supported for email notifications")
        if create:
            return self.create_email_thread(system, data.get("thread_name"))

        try:
            thread_id = UUID(str(thread_id))
        except (ValueError, TypeError, AttributeError) as ex:
            raise ValueError("email_thread_id must be a valid UUID") from ex
        thread = EmailThread.objects.select_for_update().filter(id=thread_id, system=system).first()
        if thread is None:
            raise ThreadNotFound("Email thread not found")
        return thread

    @staticmethod
    def _get_system(system_identifier: Union[UUID, str]) -> Optional[System]:
        """
        Fetch a system by ID, with name fallback for older queued payloads.

        :param system_identifier: System primary key or legacy system name.
        :type system_identifier: Union[UUID, str]
        :return: Matching system, if found.
        :rtype: Optional[System]
        """
        system_identifier = str(system_identifier or "")
        system = SystemService().get(id=system_identifier)
        if system is not None:
            return system
        return SystemService().get(name=system_identifier.lower())

    def resolve_notification_recipients(self, notification: Notification) -> Notification:
        """
        Resolve notification recipients from IDMS when only user IDs were provided.

        Marks the recipient-resolution step as processing, calls IDMS with the
        notification user IDs and notification type, then stores the resolved
        recipients on the notification. If resolution fails, the notification is
        marked failed with the resolution error for reconciliation.

        :param notification: Notification that needs recipient resolution.
        :type notification: Notification
        :return: Notification with resolved recipients.
        :rtype: Notification
        :raises ValueError: If neither recipients nor user IDs are present.
        :raises Exception: If IDMS lookup or notification update fails.
        """
        if notification.recipients:
            return notification
        if not notification.user_ids:
            raise ValueError("Notification has no recipients or user IDs")

        NotificationService().update(
            pk=notification.id,
            status=Notification.Status.GETTING_RECIPIENTS,
            recipient_resolution_status=Notification.RecipientResolutionStatus.PROCESSING,
            recipient_resolution_error="",
        )

        try:
            recipients = self.infer_recipients_from_idms(
                user_ids=notification.user_ids,
                notification_type=notification.notification_type.name,
            )
            notification = NotificationService().update(
                pk=notification.id,
                recipients=self._clean_recipients(notification.notification_type.name, recipients),
                status=Notification.Status.PENDING,
                recipient_resolution_status=Notification.RecipientResolutionStatus.RESOLVED,
                recipient_resolution_error="",
            )
            if notification is None:
                raise Exception("Notification recipients not updated")
            return notification
        except Exception as ex:
            NotificationService().update(
                pk=notification.id,
                status=Notification.Status.FAILED,
                recipient_resolution_status=Notification.RecipientResolutionStatus.FAILED,
                recipient_resolution_error=str(ex),
            )
            raise

    @staticmethod
    def _get_template_for_system(
        template_code: str, system: System, notification_type, organization_remote_code: str = "",
    ) -> Optional:
        """
        Fetch a template scoped to the system, falling back to a global template.

        A template with ``system`` set is only available to that system. A template
        with no system is global and can be used by any system.

        :param template_code: Template code from the notification request.
        :type template_code: str
        :param system: Authenticated system sending the notification.
        :type system: System
        :param notification_type: Notification type for the requested template.
        :return: Matching system-scoped or global template, or None when no template name was provided.
        :rtype: Optional[Template]
        :raises ValueError: If a coded template does not exist for the system or globally.
        """
        if not template_code:
            return None

        scope = Q(system__isnull=True, organization_remote_code="") | Q(
            system=system, organization_remote_code="",
        )
        if organization_remote_code:
            scope |= Q(system=system, organization_remote_code=organization_remote_code)
        templates = TemplateService().filter(
            scope,
            code=template_code,
            notification_type=notification_type,
            is_active=True,
        )
        template = None
        if templates is not None:
            template = templates.annotate(
                scope_priority=Case(
                    When(
                        system=system,
                        organization_remote_code=organization_remote_code,
                        then=0,
                    ) if organization_remote_code else When(pk__isnull=True, then=0),
                    When(system=system, organization_remote_code="", then=1),
                    default=2,
                    output_field=IntegerField(),
                )
            ).order_by("scope_priority").first()
        if template is None:
            raise ValueError("Invalid template")
        return template

    @staticmethod
    def infer_recipients_from_idms(user_ids: List[str], notification_type: str) -> List[str]:
        """
        Fetch notification recipients from IDMS for the given users and channel.

        Authenticate the configured machine-to-machine client using the IDMS
        client-credentials flow, then resolve the recipients with its bearer
        token. IDMS is expected to return either a JSON list or an object
        containing a ``recipients`` list.

        :param user_ids: External user IDs provided by the requesting system.
        :type user_ids: List[str]
        :param notification_type: Notification type, such as sms, email, or push.
        :type notification_type: str
        :return: Recipient addresses, phone numbers, or device tokens.
        :rtype: List[str]
        :raises ValueError: If IDMS is not configured or returns an invalid payload.
        :raises requests.RequestException: If the IDMS request fails.
        """
        idms_base_url = settings.IDMS_BASE_URL.rstrip("/")
        if not idms_base_url:
            raise ValueError("IDMS base URL not configured")

        client_id = getattr(settings, "IDMS_CLIENT_ID", "")
        client_secret = getattr(settings, "IDMS_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise ValueError("IDMS client credentials not configured")

        timeout = getattr(settings, "IDMS_REQUEST_TIMEOUT", 10)
        token_url = getattr(settings, "IDMS_TOKEN_URL", "") or f"{idms_base_url}/auth/token/"
        token_response = requests.post(
            token_url,
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=timeout,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
        if not access_token:
            raise ValueError("IDMS authentication response must include an access token")

        url = (
            getattr(settings, "IDMS_RECIPIENTS_URL", "")
            or f"{idms_base_url}/notifications/resolve-recipients/"
        )

        response = requests.post(
            url,
            json={"user_ids": user_ids, "notification_type": notification_type},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        recipients = data.get("recipients", data if isinstance(data, list) else None)
        if not isinstance(recipients, list):
            raise ValueError("IDMS response must include a recipients list")
        return recipients

    def _get_notification_instance(self, notification: Notification) -> BaseNotification:
        """
        Return the appropriate notification handler instance.

        :param notification: Notification object.
        :type notification: Notification
        :return: Subclass of BaseNotification.
        :rtype: BaseNotification
        :raises ValueError: If the notification type is unsupported.
        """
        notification_type_name = notification.notification_type.name
        notification_class = self.notification_types.get(notification_type_name)
        if not notification_class:
            raise ValueError(f"Unsupported notification type: {notification_type_name}")
        return notification_class(notification)

    @staticmethod
    def _get_provider_class_instance(provider: Provider, provider_config: ProviderConfig) -> BaseProvider:
        """
        Instantiate the provider class based on registry mapping.

        :param provider: Provider model instance.
        :type provider: Provider
        :return: Instance of the mapped provider class.
        :rtype: BaseProvider
        :raises ValueError: If the provider class is not found in the registry.
        """
        # Providers share the exceptions defined here; load the registry at use time.
        from core.backend.providers.providers_registry import PROVIDER_CLASSES

        provider_class = PROVIDER_CLASSES.get(provider.class_name)
        if provider_class is None:
            raise ValueError(f"Unknown provider class: {provider.class_name}")
        config = dict(provider_config.config or {})
        if provider.sends_callbacks:
            config["callback_url"] = NotificationManager._build_provider_callback_url(provider)
        return provider_class(config)

    @staticmethod
    def _build_provider_callback_url(provider: Provider) -> str:
        """
        Build the public callback URL that should be passed to a provider.

        The URL is based on ``SYSTEM_BASE_URL`` and the provider's unique slug,
        producing the endpoint handled by ``NotifyAPIsManager.provider_callback``.

        :param provider: Provider that will send callbacks.
        :type provider: Provider
        :return: Absolute provider callback URL.
        :rtype: str
        :raises ValueError: If ``SYSTEM_BASE_URL`` is not configured.
        """
        system_base_url = getattr(settings, "SYSTEM_BASE_URL", "").rstrip("/")
        if not system_base_url:
            raise ValueError("SYSTEM_BASE_URL is not configured")
        return f"{system_base_url}/api/core/callbacks/{provider.slug}/"

    def send_notification(self, notification: Notification) -> bool:
        """
        Send a notification using the appropriate ordinary or threaded delivery flow.

        Threaded notifications are sent in order while holding the thread lock.
        Ordinary notifications are sent directly through the configured providers.

        :param notification: Persisted notification to send.
        :type notification: Notification
        :return: True if delivery succeeds or the threaded notification was already sent, otherwise False.
        :rtype: bool
        :raises ThreadDeliveryPending: If an earlier notification in the thread has not finished.
        :raises ThreadProviderPending: If the provider's accepted parent message is not yet available for replies.
        """
        if notification.email_thread_id:
            return self._send_thread_notification(notification)
        return self._send_notification(notification)

    @transaction.atomic
    def _send_thread_notification(self, notification: Notification) -> bool:
        """
        Send an email in thread order while locking the thread through status persistence.

        Reload the notification, resolve pending recipients, and use the latest
        sent message as the reply parent. Persist the message ID and references
        before provider delivery. Commit recipient-resolution failures so later
        messages can proceed.

        :param notification: Persisted notification attached to an email thread.
        :type notification: Notification
        :return: True if delivery succeeds or the notification was already sent, otherwise False.
        :rtype: bool
        :raises ThreadDeliveryPending: If an earlier notification in the thread has not finished.
        :raises ThreadProviderPending: If the provider's accepted parent message is not yet available for replies.
        :raises EmailThread.DoesNotExist: If the notification's email thread no longer exists.
        :raises Notification.DoesNotExist: If the notification no longer exists.
        """
        # PostgreSQL workers cannot choose the same parent or send a notification twice.
        thread = EmailThread.objects.select_for_update().get(id=notification.email_thread_id)
        notification = Notification.objects.select_related("template", "system", "notification_type").get(
            id=notification.id,
        )
        notification.email_thread = thread
        if notification.status == Notification.Status.SENT:
            return True
        earlier = thread.notifications.filter(
            Q(date_created__lt=notification.date_created)
            | Q(date_created=notification.date_created, id__lt=notification.id),
        ).exclude(status__in=[Notification.Status.SENT, Notification.Status.FAILED])
        if earlier.exists():
            raise ThreadDeliveryPending("Waiting for an earlier email in this thread")

        if notification.recipient_resolution_status in [
            Notification.RecipientResolutionStatus.PENDING,
            Notification.RecipientResolutionStatus.PROCESSING,
            Notification.RecipientResolutionStatus.FAILED,
        ]:
            try:
                notification = self.resolve_notification_recipients(notification)
            except Exception:
                # Commit terminal resolution failures so later messages can proceed.
                return False
            notification.email_thread = thread

        parent = thread.notifications.filter(status=Notification.Status.SENT).order_by(
            "-sent_time", "-date_created", "-id",
        ).first()
        notification.email_references = (
            parent.email_references + [parent.email_message_id] if parent and parent.email_message_id else []
        )
        if not notification.email_message_id:
            notification.email_message_id = make_msgid()
        notification.save(update_fields=["email_message_id", "email_references"])
        return self._send_notification(notification, thread=thread, parent=parent)

    def _send_notification(self, notification: Notification, *, thread=None, parent=None) -> bool:
        """
        Attempt to send a notification using all active providers.

        :param notification: Notification object to be sent.
        :type notification: Notification
        :param thread: Locked email thread to use for delivery, or None for an ordinary send.
        :type thread: Optional[EmailThread]
        :param parent: Latest sent notification in the thread, or None for the first message.
        :type parent: Optional[Notification]
        :return: True if sent by any provider, False if all fail.
        :rtype: bool
        :raises ThreadProviderPending: If the provider's accepted parent message is not yet available for replies.
        """
        try:
            notification_handler = self._get_notification_instance(notification)
            notification_handler.validate()
            Notification.objects.filter(pk=notification.id).update(status=Notification.Status.PROCESSING)

            active_providers = notification_handler.active_providers()
            if thread and thread.provider_id:
                active_providers = active_providers.filter(id=thread.provider_id)
            active_providers = self._scope_queryset(active_providers, notification)
            if not active_providers.exists():
                raise Exception(f"No active providers found for {notification.notification_type.name} notifications")

            content = notification_handler.prepare_content()
            if thread:
                content.update({
                    "email_thread_id": str(thread.id),
                    "message_id": notification.email_message_id,
                    "in_reply_to": parent.email_message_id if parent else "",
                    "references": notification.email_references,
                    "parent_provider_message_id": parent.email_provider_message_id if parent else "",
                })
            last_error = ""
            for provider, provider_config in self._delivery_candidates(notification, active_providers):
                attempt = NotificationTry.objects.create(
                    notification=notification,
                    delivery_cycle=notification.delivery_cycle,
                    provider=provider,
                    provider_config=provider_config,
                )
                try:
                    provider_class_instance = self._get_provider_class_instance(provider, provider_config)
                    sender = ""
                    if thread:
                        if not provider_class_instance.supports_email_threads:
                            raise ValueError(f"Provider {provider.name} does not support email threads")
                        sender = provider_class_instance.config.get("from_address", "")
                        if not sender:
                            raise ValueError(f"Provider {provider.name} requires from_address for email threads")
                        if thread.sender_address and sender != thread.sender_address:
                            raise ValueError("The email thread requires its original sender address")

                    if not provider_class_instance.validate_config():
                        raise ValueError(
                            provider_class_instance.last_exception
                            or f"Invalid configuration for provider: {provider.name}"
                        )

                    send_status = provider_class_instance.send(
                        recipients=notification.recipients,
                        content=content,
                    )
                    if send_status == Notification.Status.FAILED:
                        raise ValueError(
                            provider_class_instance.last_exception or f"Send failed for provider: {provider.name}"
                        )

                    attempt.status = (
                        NotificationTry.Status.SUCCESS
                        if send_status == Notification.Status.SENT
                        else NotificationTry.Status.CONFIRMATION_PENDING
                    )
                    attempt.save(update_fields=["status", "date_modified"])
                    update_data = {
                        "notification_id": notification.id,
                        "status": send_status,
                        "provider": provider,
                        "provider_config": provider_config,
                    }
                    if thread:
                        update_data["email_provider_message_id"] = provider_class_instance.sent_provider_message_id
                        update_data["email_message_id"] = (
                            provider_class_instance.sent_message_id
                            if provider_class_instance.sent_provider_message_id
                            else provider_class_instance.sent_message_id or notification.email_message_id
                        )
                        thread.provider = provider
                        thread.sender_address = sender
                        thread.save(update_fields=["provider", "sender_address", "date_modified"])
                    if send_status == Notification.Status.SENT:
                        update_data["sent_time"] = timezone.now()
                    self.update_notification_status(**update_data)
                    return True
                except ThreadProviderPending:
                    attempt.delete()
                    raise
                except Exception as ex:
                    last_error = str(ex)
                    attempt.status = NotificationTry.Status.FAILED
                    attempt.error = last_error
                    attempt.save(update_fields=["status", "error", "date_modified"])
                    self.queue_provider_config_failure_alert(attempt)
                    logger.warning(
                        "Notification %s failed with provider %s config %s: %s",
                        notification.id, provider.id, provider_config.id, ex,
                    )

            raise Exception(last_error or "No unused active provider configurations are available")

        except ThreadProviderPending:
            raise
        except Exception as ex:
            logger.exception(f"NotificationManager - send_notification exception: {ex}")
            self.update_notification_status(
                notification_id=notification.id,
                status=Notification.Status.FAILED,
                message=str(ex),
            )
            return False

    @staticmethod
    def _scope_queryset(queryset, notification: Notification):
        """Restrict a provider or config queryset to the notification's optional allow-lists."""
        eligible_ids = []
        for item in queryset.prefetch_related("systems"):
            system_ids = {system.id for system in item.systems.all()}
            remote_codes = item.organization_remote_codes or []
            if (
                (not system_ids and not remote_codes)
                or notification.system_id in system_ids
                or notification.organization_remote_code in remote_codes
            ):
                eligible_ids.append(item.id)
        return queryset.filter(id__in=eligible_ids)

    def _eligible_configs(self, provider: Provider, notification: Notification):
        """
        Return active provider configs allowed for the notification's system and organization.

        Configurations are ordered by ascending priority and then name so delivery
        attempts remain deterministic when priorities match.

        :param provider: Provider whose configurations should be considered.
        :type provider: Provider
        :param notification: Notification supplying the applicable scope.
        :type notification: Notification
        :return: Ordered queryset of eligible provider configurations.
        """
        configs = provider.configs.filter(is_active=True)
        return self._scope_queryset(configs, notification).order_by("priority", "name")

    def _delivery_candidates(self, notification: Notification, active_providers):
        """
        Build the ordered provider and configuration attempts for a delivery cycle.

        Organization remote-code overrides take precedence over system overrides. When no
        override applies, providers and their active scoped configurations follow
        their normal priorities. Provider/config pairs already tried during the
        notification's current delivery cycle are excluded.

        :param notification: Notification being prepared for delivery.
        :type notification: Notification
        :param active_providers: Active, scope-eligible providers for the notification type.
        :return: Ordered provider and configuration pairs that remain available.
        :rtype: List[Tuple[Provider, ProviderConfig]]
        """
        override = None
        if notification.organization_remote_code:
            override = NotificationOverride.objects.filter(
                system=notification.system,
                organization_remote_code=notification.organization_remote_code,
                notification_type=notification.notification_type,
            ).first()
        if override is None:
            override = NotificationOverride.objects.filter(
                organization_remote_code="",
                system=notification.system,
                notification_type=notification.notification_type,
            ).first()

        provider_rows = []
        active_provider_ids = set(active_providers.values_list("id", flat=True))
        selected_provider_ids = set()
        if override is not None:
            for route in override.providers.select_related("provider").order_by("priority", "provider__name"):
                if route.provider_id not in active_provider_ids:
                    continue
                selected_provider_ids.add(route.provider_id)
                eligible_configs = self._eligible_configs(route.provider, notification)
                selected = list(
                    route.configs.select_related("provider_config")
                    .filter(provider_config_id__in=eligible_configs.values("id"))
                    .order_by("priority", "provider_config__priority", "provider_config__name")
                )
                configs = [item.provider_config for item in selected]
                if not selected or route.use_other_configs:
                    excluded = {config.id for config in configs}
                    configs.extend(
                        self._eligible_configs(route.provider, notification)
                        .exclude(id__in=excluded)
                    )
                provider_rows.append((route.provider, configs))
            if override.use_other_providers:
                remaining = active_providers.exclude(id__in=selected_provider_ids)
                provider_rows.extend((
                    provider,
                    list(self._eligible_configs(provider, notification)),
                ) for provider in remaining)
        else:
            provider_rows = [
                (provider, list(self._eligible_configs(provider, notification)))
                for provider in active_providers
            ]

        used = set(notification.tries.filter(
            delivery_cycle=notification.delivery_cycle,
        ).values_list("provider_id", "provider_config_id"))
        return [
            (provider, config)
            for provider, configs in provider_rows
            for config in configs
            if (provider.id, config.id) not in used
        ]

    def queue_provider_config_failure_alert(self, attempt: NotificationTry) -> None:
        """
        Queue an internal admin email after three consecutive failures for a route.

        At least three consecutive tries for the same provider and configuration
        must have failed. The complete streak since the most recent non-failed try
        is counted and included in the alert along with its first failure time. A
        row-level config lock and a durable alert record prevent duplicate alerts
        for that pair during the three-hour cooldown. The alert is created for
        the active internal system and sent to ``NOTIFICATION_ADMIN_EMAILS`` after
        the transaction commits. Alert creation errors are logged without affecting
        the original notification delivery.

        :param attempt: Newly failed delivery attempt that may trigger the alert.
        :type attempt: NotificationTry
        :return: None.
        :rtype: None
        """
        recipients = getattr(settings, "NOTIFICATION_ADMIN_EMAILS", [])
        if not recipients:
            return
        try:
            with transaction.atomic():
                provider_config = ProviderConfig.objects.select_for_update().select_related("provider").get(
                    pk=attempt.provider_config_id,
                )
                consecutive_failures = []
                tries = NotificationTry.objects.filter(
                    provider_id=attempt.provider_id,
                    provider_config_id=attempt.provider_config_id,
                ).only("status", "date_modified").order_by("-date_created")
                for item in tries.iterator():
                    if item.status != NotificationTry.Status.FAILED:
                        break
                    consecutive_failures.append(item)
                if len(consecutive_failures) < PROVIDER_CONFIG_FAILURE_ALERT_THRESHOLD:
                    return
                if ProviderConfigFailureAlert.objects.filter(
                    provider_id=attempt.provider_id,
                    provider_config_id=attempt.provider_config_id,
                    date_created__gte=timezone.now() - PROVIDER_CONFIG_FAILURE_ALERT_COOLDOWN,
                ).exists():
                    return

                internal_system = System.objects.filter(is_internal=True, is_active=True).first()
                if internal_system is None:
                    raise ValueError("No active internal notification system is configured")

                first_failure = consecutive_failures[-1]

                alert_notification = self.save_notification({
                    "system": str(internal_system.id),
                    "notification_type": "email",
                    "template": "provider-config-failure-alert",
                    "recipients": recipients,
                    "context": {
                        "provider_name": attempt.provider.name,
                        "config_name": provider_config.name,
                        "config_description": provider_config.description or provider_config.name,
                        "config_id": str(provider_config.id),
                        "consecutive_failure_count": len(consecutive_failures),
                        "first_failed_at": timezone.localtime(first_failure.date_modified).strftime(
                            "%d %b %Y %H:%M %Z"
                        ),
                        "latest_error": attempt.error,
                        "failed_at": timezone.localtime(attempt.date_modified).strftime("%d %b %Y %H:%M %Z"),
                        "year": timezone.localdate().year,
                    },
                    "unique_identifier": f"provider-config-failure-{attempt.id}",
                }, raise_errors=True)
                ProviderConfigFailureAlert.objects.create(
                    provider_id=attempt.provider_id,
                    provider_config=provider_config,
                    trigger_try=attempt,
                    notification=alert_notification,
                    recipients=recipients,
                )
                from core.tasks import process_existing_notification
                transaction.on_commit(
                    lambda: process_existing_notification.delay(str(alert_notification.id))
                )
        except Exception:
            logger.exception(
                "Unable to queue provider/config failure alert for try %s", attempt.id,
            )

    def update_notification_status(
        self,
        notification_id: Union[UUID, str],
        status: str,
        message: Optional[str] = None,
        **kwargs
    ) -> None:
        """
        Update the status of a Notification and trigger a callback to the system.

        :param notification_id: Primary key of the notification.
        :type notification_id: Union[UUID, str]
        :param status: New status to set.
        :type status: str
        :param message: Optional error or status message.
        :type message: Optional[str]
        :param kwargs: Additional fields to update in the notification.
        :raises Exception: If the notification update fails.
        """
        update_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        notification = NotificationService().update(pk=notification_id, status=status, **update_kwargs)
        if notification is None:
            raise Exception("Notification not updated")

        response_data = {
            "notification_id": str(notification.id),
            "unique_identifier": notification.unique_identifier,
            "status": notification.status,
        }
        if notification.email_thread_id:
            response_data["email_thread_id"] = str(notification.email_thread_id)

        if message:
            response_data["message"] = message
        if notification.status in [Notification.Status.SENT, Notification.Status.CONFIRMATION_PENDING]:
            response_data["sent_time"] = str(notification.sent_time)

        if notification.system.callback_enabled:
            self.send_callback_to_system(system=notification.system, payload=response_data, notification=notification)

    def send_callback_to_system(
        self,
        system: System,
        payload: Dict,
        notification: Optional[Notification] = None
    ) -> Optional[SystemCallback]:
        """
        Send a callback response to the system after notification processing.

        :param system: System instance receiving the callback.
        :type system: System
        :param payload: Dictionary payload to send.
        :type payload: Dict
        :param notification: Notification linked to the callback, if available.
        :type notification: Optional[Notification]
        :return: Persisted callback record, or None when webhook callback is not configured.
        :rtype: Optional[SystemCallback]
        """
        if not system.webhook_url:
            logger.warning(f"Webhook URL not configured for system '{system.name}'.")
            return None

        callback = SystemCallback.objects.create(system=system, notification=notification, payload=payload)
        from core.tasks import send_system_callback
        transaction.on_commit(lambda: send_system_callback.delay(str(callback.id)))
        return callback
