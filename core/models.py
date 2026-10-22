import json
import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from base.models import GenericBaseModel, BaseModel


class NotificationType(GenericBaseModel):
    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    @property
    def active_providers(self):
        return Provider.objects.filter(notification_type=self, is_active=True)

class System(GenericBaseModel):
    api_key = models.CharField(max_length=255, unique=True, editable=False, blank=True)
    is_active = models.BooleanField(default=True)
    is_internal = models.BooleanField(
        default=False,
        help_text="Marks the system record used by this notification service for internal notifications.",
    )
    email_signature = models.TextField(blank=True)
    sms_signature = models.CharField(max_length=160, blank=True)
    callback_enabled = models.BooleanField(default=False)
    webhook_url = models.URLField(blank=True, null=True)

    class Meta:
        ordering = ('name',)
        constraints = [
            models.UniqueConstraint(
                fields=["is_internal"],
                condition=models.Q(is_internal=True),
                name="unique_internal_system",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_urlsafe(40)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class Provider(GenericBaseModel):
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    systems = models.ManyToManyField(
        System,
        blank=True,
        related_name="scoped_providers",
        help_text=(
            "Optional system allow-list. Leave both scope fields empty to make this provider available globally. "
            "When scopes are set, a notification may use the provider when its system or organization matches."
        ),
    )
    organizations = models.ManyToManyField(
        "Organization",
        blank=True,
        related_name="scoped_providers",
        help_text=(
            "Optional organization allow-list. Leave both scope fields empty to make this provider available globally. "
            "An organization match is exact; a system scope applies to every organization in that system."
        ),
    )
    slug = models.SlugField(max_length=100, unique=True)
    priority = models.IntegerField(null=True, blank=True)
    class_name = models.CharField(max_length=100, help_text="Callback class containing its config")
    callback_verification_config = models.JSONField(default=dict, blank=True)
    sends_callbacks = models.BooleanField(default=False)
    verify_callback_signature = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.verify_callback_signature and not self.callback_verification_config:
            raise ValidationError({
                "callback_verification_config": "Callback verification keys are required when signature verification is enabled."
            })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def is_available_for(self, system_id, organization_id=None):
        has_system_scopes = self.systems.exists()
        has_organization_scopes = self.organizations.exists()
        if not has_system_scopes and not has_organization_scopes:
            return True
        return (
            (bool(system_id) and self.systems.filter(pk=system_id).exists())
            or (bool(organization_id) and self.organizations.filter(pk=organization_id).exists())
        )

class ProviderConfig(BaseModel):
    name = models.CharField(max_length=100)
    description = models.TextField(
        blank=True,
        help_text="Describe what this configuration is for, such as its sender ID or account.",
    )
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="configs")
    systems = models.ManyToManyField(
        System,
        blank=True,
        related_name="scoped_provider_configs",
        help_text=(
            "Optional system allow-list. Leave both scope fields empty to make this config available wherever its "
            "provider is available. A matching system permits every organization in that system."
        ),
    )
    organizations = models.ManyToManyField(
        "Organization",
        blank=True,
        related_name="scoped_provider_configs",
        help_text=(
            "Optional organization allow-list. When config scopes are set, the notification system or organization "
            "must match one of them in addition to satisfying the provider's scope."
        ),
    )
    priority = models.IntegerField(default=0, help_text="Lower numbers are tried first.")
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive configurations are never used for delivery, including in overrides.",
    )

    class Meta:
        ordering = ("priority", "name")
        constraints = [
            models.UniqueConstraint(fields=("provider", "name"), name="unique_provider_config_name"),
        ]

    @property
    def config(self):
        return {entry.key: entry.parsed_value for entry in self.entries.all()}

    def __str__(self):
        return f"{self.provider.name}: {self.name}"

    def is_available_for(self, system_id, organization_id=None):
        has_system_scopes = self.systems.exists()
        has_organization_scopes = self.organizations.exists()
        if not has_system_scopes and not has_organization_scopes:
            return True
        return (
            (bool(system_id) and self.systems.filter(pk=system_id).exists())
            or (bool(organization_id) and self.organizations.filter(pk=organization_id).exists())
        )


class ProviderConfigEntry(BaseModel):
    provider_config = models.ForeignKey(ProviderConfig, on_delete=models.CASCADE, related_name="entries")

    class ValueType(models.TextChoices):
        STRING = "string", "Text"
        JSON = "json", "JSON (number, boolean, null, list or object)"

    key = models.CharField(max_length=255)
    value = models.TextField(blank=True)
    value_type = models.CharField(max_length=10, choices=ValueType.choices, default=ValueType.STRING)

    class Meta:
        ordering = ("key",)
        constraints = [
            models.UniqueConstraint(
                fields=("provider_config", "key"), name="unique_provider_config_entry_key",
            ),
        ]

    @property
    def parsed_value(self):
        return json.loads(self.value) if self.value_type == self.ValueType.JSON else self.value

    def clean(self):
        super().clean()
        if self.value_type == self.ValueType.JSON:
            try:
                json.loads(self.value)
            except (ValueError, TypeError):
                raise ValidationError({"value": "Enter a valid JSON value for this value type."})

    def __str__(self):
        return self.key


class Organization(BaseModel):
    class IdentifierType(models.TextChoices):
        ORGANIZATION_ID = "organization_id", "Organization ID"
        REMOTE_CODE = "remote_code", "Remote code"

    system = models.ForeignKey(System, on_delete=models.CASCADE, related_name="organizations")
    name = models.CharField(max_length=255)
    identifier_type = models.CharField(max_length=20, choices=IdentifierType.choices)
    identifier = models.CharField(max_length=255)

    class Meta:
        ordering = ("system__name", "name")
        constraints = [
            models.UniqueConstraint(fields=("system", "identifier"), name="unique_system_organization_identifier"),
        ]

    def clean(self):
        super().clean()
        self.identifier = (self.identifier or "").strip()
        if not self.identifier:
            raise ValidationError({"identifier": "Enter an organization identifier."})
        if self.identifier_type == self.IdentifierType.ORGANIZATION_ID:
            try:
                uuid.UUID(self.identifier)
            except (ValueError, TypeError, AttributeError):
                raise ValidationError({"identifier": "Organization ID must be a valid UUID."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.system.name})"


class NotificationOverride(BaseModel):
    system = models.ForeignKey(System, null=True, blank=True, on_delete=models.CASCADE, related_name="notification_overrides")
    organization = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.CASCADE, related_name="notification_overrides",
        help_text="Organization rules take precedence over system rules.",
    )
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    use_other_providers = models.BooleanField(
        default=False,
        help_text="After configured providers fail, try other active providers by their normal priority.",
    )

    class Meta:
        ordering = ("system__name", "organization__name", "notification_type__name")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(system__isnull=False) | models.Q(organization__isnull=False),
                name="override_has_system_or_organization",
            ),
            models.UniqueConstraint(
                fields=("system", "notification_type"), condition=models.Q(organization__isnull=True),
                name="unique_system_notification_override",
            ),
            models.UniqueConstraint(
                fields=("organization", "notification_type"), condition=models.Q(organization__isnull=False),
                name="unique_organization_notification_override",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.system_id and not self.organization_id:
            raise ValidationError("Select a system or organization.")
        if self.organization_id:
            if self.system_id and self.system_id != self.organization.system_id:
                raise ValidationError({"system": "The system must own the selected organization."})
            self.system = self.organization.system

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        scope = self.organization or self.system
        return f"{scope}: {self.notification_type} routing"


class NotificationOverrideProvider(BaseModel):
    override = models.ForeignKey(NotificationOverride, on_delete=models.CASCADE, related_name="providers")
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE)
    priority = models.IntegerField(default=0, help_text="Lower numbers are tried first.")
    use_other_configs = models.BooleanField(
        default=False,
        help_text="After selected configs fail, try other active configs for this provider.",
    )

    class Meta:
        ordering = ("priority", "provider__name")
        constraints = [
            models.UniqueConstraint(fields=("override", "provider"), name="unique_override_provider"),
            models.UniqueConstraint(fields=("override", "priority"), name="unique_override_provider_priority"),
        ]

    def clean(self):
        super().clean()
        if self.override_id and self.provider_id and self.override.notification_type_id != self.provider.notification_type_id:
            raise ValidationError({"provider": "Provider must match the override notification type."})
        if (self.override_id and self.provider_id and not self.provider.is_available_for(
                self.override.system_id, self.override.organization_id)):
            raise ValidationError({"provider": "Provider is not available for this override's system or organization."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.override} / {self.provider}"


class NotificationOverrideConfig(BaseModel):
    override_provider = models.ForeignKey(
        NotificationOverrideProvider, on_delete=models.CASCADE, related_name="configs",
    )
    provider_config = models.ForeignKey(ProviderConfig, on_delete=models.CASCADE)
    priority = models.IntegerField(default=0, help_text="Lower numbers are tried first.")

    class Meta:
        ordering = ("priority", "provider_config__name")
        constraints = [
            models.UniqueConstraint(
                fields=("override_provider", "provider_config"), name="unique_override_provider_config",
            ),
            models.UniqueConstraint(
                fields=("override_provider", "priority"), name="unique_override_config_priority",
            ),
        ]

    def clean(self):
        super().clean()
        if (self.override_provider_id and self.provider_config_id
                and self.override_provider.provider_id != self.provider_config.provider_id):
            raise ValidationError({"provider_config": "Config must belong to the selected provider."})
        if (self.override_provider_id and self.provider_config_id
                and not self.provider_config.is_available_for(
                    self.override_provider.override.system_id,
                    self.override_provider.override.organization_id,
                )):
            raise ValidationError({
                "provider_config": "Config is not available for this override's system or organization.",
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.provider_config)


class Template(GenericBaseModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=100)
    system = models.ForeignKey(System, null=True, blank=True, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, null=True, blank=True, on_delete=models.CASCADE)
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('name',)
        constraints = [
            models.UniqueConstraint(
                fields=["system", "code"],
                condition=models.Q(system__isnull=False, organization__isnull=True),
                name="unique_template_code_per_system",
            ),
            models.UniqueConstraint(
                fields=["organization", "code"],
                condition=models.Q(organization__isnull=False),
                name="unique_template_code_per_organization",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(system__isnull=True, organization__isnull=True),
                name="unique_global_template_code",
            ),
        ]

    def clean(self):
        super().clean()
        if self.organization_id:
            if self.system_id and self.system_id != self.organization.system_id:
                raise ValidationError({"system": "The system must own the selected organization."})
            self.system = self.organization.system

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class EmailThread(BaseModel):
    system = models.ForeignKey(System, on_delete=models.CASCADE)
    name = models.CharField(max_length=255, help_text="The fixed subject used for every email in this thread.")
    provider = models.ForeignKey(Provider, null=True, blank=True, on_delete=models.PROTECT)
    sender_address = models.CharField(max_length=320, blank=True)

    class Meta:
        ordering = ('-date_created',)

    def __str__(self):
        return self.name


class Notification(BaseModel):
    class Status(models.TextChoices):
        PENDING = "Pending"
        PROCESSING = "Processing"
        GETTING_RECIPIENTS = "Getting Recipients"
        SENT = "Sent"
        FAILED = "Failed"
        CONFIRMATION_PENDING = "Confirmation Pending"

    class RecipientResolutionStatus(models.TextChoices):
        NOT_REQUIRED = "Not Required"
        PENDING = "Pending"
        PROCESSING = "Processing"
        RESOLVED = "Resolved"
        FAILED = "Failed"

    unique_identifier = models.CharField(max_length=255, null=True, blank=True)
    delivery_cycle = models.PositiveIntegerField(default=1, editable=False)
    system = models.ForeignKey(System, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, null=True, blank=True, on_delete=models.SET_NULL)
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    user_ids = models.JSONField(default=list, blank=True)
    recipients = models.JSONField(default=list)
    recipient_resolution_status = models.CharField(
        max_length=20,
        choices=RecipientResolutionStatus.choices,
        default=RecipientResolutionStatus.NOT_REQUIRED,
    )
    recipient_resolution_error = models.TextField(blank=True)
    template = models.ForeignKey(Template, null=True, on_delete=models.SET_NULL)
    provider = models.ForeignKey(Provider, null=True, on_delete=models.SET_NULL)
    provider_config = models.ForeignKey(ProviderConfig, null=True, on_delete=models.SET_NULL)
    context = models.JSONField()
    email_thread = models.ForeignKey(
        EmailThread, null=True, blank=True, on_delete=models.PROTECT, related_name="notifications",
    )
    email_message_id = models.TextField(blank=True)
    email_provider_message_id = models.TextField(blank=True)
    email_references = models.JSONField(default=list, blank=True)
    sent_time = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ('-date_created',)

    def clean(self):
        super().clean()
        if self.organization_id and self.organization.system_id != self.system_id:
            raise ValidationError({"organization": "Organization must belong to the notification system."})
        if self.provider_id and self.provider.notification_type_id != self.notification_type_id:
            raise ValidationError({"provider": "Provider must match the notification type."})
        if (self.provider_id and not self.provider.is_available_for(
                self.system_id, self.organization_id)):
            raise ValidationError({"provider": "Provider is not available for this system or organization."})
        if self.provider_config_id and self.provider_config.provider_id != self.provider_id:
            raise ValidationError({"provider_config": "Config must belong to the selected provider."})
        if (self.provider_config_id and not self.provider_config.is_available_for(
                self.system_id, self.organization_id)):
            raise ValidationError({"provider_config": "Config is not available for this system or organization."})

    def __str__(self):
        return "%s %s notification to %s" %(self.system.name, self.notification_type.name, self.recipients)


class NotificationTry(BaseModel):
    class Status(models.TextChoices):
        PROCESSING = "Processing"
        FAILED = "Failed"
        CONFIRMATION_PENDING = "Confirmation Pending"
        SUCCESS = "Success"

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="tries")
    delivery_cycle = models.PositiveIntegerField(default=1, editable=False)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT)
    provider_config = models.ForeignKey(ProviderConfig, on_delete=models.PROTECT)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PROCESSING)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("date_created",)

    def __str__(self):
        return f"{self.provider} / {self.provider_config}: {self.status}"


class ProviderConfigFailureAlert(BaseModel):
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="failure_alerts")
    provider_config = models.ForeignKey(
        ProviderConfig, on_delete=models.PROTECT, related_name="failure_alerts",
    )
    trigger_try = models.OneToOneField(
        NotificationTry, on_delete=models.PROTECT, related_name="failure_alert",
    )
    notification = models.ForeignKey(
        Notification, null=True, blank=True, on_delete=models.SET_NULL, related_name="provider_failure_alerts",
    )
    recipients = models.JSONField(default=list)

    class Meta:
        ordering = ("-date_created",)
        indexes = [
            models.Index(
                fields=("provider", "provider_config", "date_created"),
                name="config_failure_alert_pair_idx",
            ),
        ]

    def __str__(self):
        return f"Failure alert for {self.provider} / {self.provider_config}"


class ProviderCallback(BaseModel):
    class Status(models.TextChoices):
        RECEIVED = "Received"
        PROCESSING = "Processing"
        PROCESSED = "Processed"
        FAILED = "Failed"

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    data = models.JSONField(default=dict)
    headers = models.JSONField(default=dict)
    state = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-date_created',)

    def __str__(self):
        return f"{self.provider.name} callback {self.status}"


class SystemCallback(BaseModel):
    class Status(models.TextChoices):
        PENDING = "Pending"
        SENDING = "Sending"
        SENT = "Sent"
        FAILED = "Failed"

    system = models.ForeignKey(System, on_delete=models.CASCADE)
    notification = models.ForeignKey(Notification, null=True, blank=True, on_delete=models.SET_NULL)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    response_status_code = models.PositiveIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-date_created',)

    def __str__(self):
        return f"{self.system.name} callback {self.status}"
