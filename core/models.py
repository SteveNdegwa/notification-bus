import json
import secrets
from django.core.exceptions import ValidationError
from django.db import models

from base.models import GenericBaseModel, BaseModel


def clean_organization_remote_codes(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError("Organization remote codes must be a list of strings.")
    codes = []
    for item in value:
        code = item.strip()
        if not code:
            raise ValidationError("Organization remote codes cannot be blank.")
        if len(code) > 255:
            raise ValidationError("Organization remote codes cannot exceed 255 characters.")
        if code not in codes:
            codes.append(code)
    return codes


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
    organization_remote_codes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Optional organization remote-code allow-list. Leave both scope fields empty to make this provider "
            "available globally. A system scope applies to every organization in that system."
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
        self.organization_remote_codes = clean_organization_remote_codes(
            self.organization_remote_codes,
        )
        if self.verify_callback_signature and not self.callback_verification_config:
            raise ValidationError({
                "callback_verification_config": "Callback verification keys are required when signature verification is enabled."
            })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def is_available_for(self, system_id, organization_remote_code=None):
        has_system_scopes = self.systems.exists()
        has_organization_scopes = bool(self.organization_remote_codes)
        if not has_system_scopes and not has_organization_scopes:
            return True
        return (
            (bool(system_id) and self.systems.filter(pk=system_id).exists())
            or organization_remote_code in self.organization_remote_codes
        )


class ProviderConfigDefinition(BaseModel):
    class ValueType(models.TextChoices):
        STRING = "string", "Text"
        INTEGER = "integer", "Integer"
        FLOAT = "float", "Decimal number"
        BOOLEAN = "boolean", "Boolean"
        JSON = "json", "JSON"

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="config_definitions")
    name = models.CharField(max_length=255)
    key = models.CharField(max_length=255)
    value_type = models.CharField(max_length=10, choices=ValueType.choices, default=ValueType.STRING)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("provider", "name"), name="unique_provider_definition_name"),
            models.UniqueConstraint(fields=("provider", "key"), name="unique_provider_definition_key"),
        ]

    def __str__(self):
        return f"{self.provider.name}: {self.name}"


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
    organization_remote_codes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Optional organization remote-code allow-list. When config scopes are set, the notification system or "
            "organization remote code must match one of them in addition to satisfying the provider's scope."
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
        return {entry.definition.key: entry.parsed_value for entry in self.entries.select_related("definition")}

    def __str__(self):
        return f"{self.provider.name}: {self.name}"

    def clean(self):
        super().clean()
        self.organization_remote_codes = clean_organization_remote_codes(
            self.organization_remote_codes,
        )

    def is_available_for(self, system_id, organization_remote_code=None):
        has_system_scopes = self.systems.exists()
        has_organization_scopes = bool(self.organization_remote_codes)
        if not has_system_scopes and not has_organization_scopes:
            return True
        return (
            (bool(system_id) and self.systems.filter(pk=system_id).exists())
            or organization_remote_code in self.organization_remote_codes
        )


class ProviderConfigEntry(BaseModel):
    provider_config = models.ForeignKey(ProviderConfig, on_delete=models.CASCADE, related_name="entries")
    definition = models.ForeignKey(
        ProviderConfigDefinition, on_delete=models.PROTECT, related_name="entries",
    )
    value = models.TextField(blank=True)

    class Meta:
        ordering = ("definition__name",)
        constraints = [
            models.UniqueConstraint(
                fields=("provider_config", "definition"), name="unique_provider_config_entry_definition",
            ),
        ]

    @property
    def parsed_value(self):
        if self.definition.value_type == ProviderConfigDefinition.ValueType.INTEGER:
            return int(self.value)
        if self.definition.value_type == ProviderConfigDefinition.ValueType.FLOAT:
            return float(self.value)
        if self.definition.value_type == ProviderConfigDefinition.ValueType.BOOLEAN:
            return self.value.lower() == "true"
        if self.definition.value_type == ProviderConfigDefinition.ValueType.JSON:
            return json.loads(self.value)
        return self.value

    def clean(self):
        super().clean()
        if self.provider_config_id and self.definition_id:
            if self.provider_config.provider_id != self.definition.provider_id:
                raise ValidationError({"definition": "Definition must belong to the config's provider."})
        if self.definition_id and self.definition.value_type == ProviderConfigDefinition.ValueType.JSON:
            try:
                json.loads(self.value)
            except (ValueError, TypeError):
                raise ValidationError({"value": "Enter a valid JSON value for this value type."})
        elif self.definition_id and self.definition.value_type == ProviderConfigDefinition.ValueType.INTEGER:
            try:
                int(self.value)
            except (ValueError, TypeError):
                raise ValidationError({"value": "Enter a valid integer."})
        elif self.definition_id and self.definition.value_type == ProviderConfigDefinition.ValueType.FLOAT:
            try:
                float(self.value)
            except (ValueError, TypeError):
                raise ValidationError({"value": "Enter a valid number."})
        elif (
            self.definition_id
            and self.definition.value_type == ProviderConfigDefinition.ValueType.BOOLEAN
            and self.value.lower() not in {"true", "false"}
        ):
            raise ValidationError({"value": "Enter true or false."})

    def __str__(self):
        return self.definition.key


class NotificationOverride(BaseModel):
    system = models.ForeignKey(System, on_delete=models.CASCADE, related_name="notification_overrides")
    organization_remote_code = models.CharField(
        max_length=255, blank=True,
        help_text="Optional organization remote code. Organization rules take precedence over system rules.",
    )
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    use_other_providers = models.BooleanField(
        default=False,
        help_text="After configured providers fail, try other active providers by their normal priority.",
    )

    class Meta:
        ordering = ("system__name", "organization_remote_code", "notification_type__name")
        constraints = [
            models.UniqueConstraint(
                fields=("system", "notification_type"), condition=models.Q(organization_remote_code=""),
                name="unique_system_notification_override",
            ),
            models.UniqueConstraint(
                fields=("system", "organization_remote_code", "notification_type"),
                condition=~models.Q(organization_remote_code=""),
                name="unique_organization_notification_override",
            ),
        ]

    def clean(self):
        super().clean()
        self.organization_remote_code = (self.organization_remote_code or "").strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        scope = self.organization_remote_code or self.system
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
                self.override.system_id, self.override.organization_remote_code)):
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
                    self.override_provider.override.organization_remote_code,
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
    organization_remote_code = models.CharField(max_length=255, blank=True)
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('name',)
        constraints = [
            models.UniqueConstraint(
                fields=["system", "code"],
                condition=models.Q(system__isnull=False, organization_remote_code=""),
                name="unique_template_code_per_system",
            ),
            models.UniqueConstraint(
                fields=["system", "organization_remote_code", "code"],
                condition=~models.Q(organization_remote_code=""),
                name="unique_template_code_per_organization",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(system__isnull=True, organization_remote_code=""),
                name="unique_global_template_code",
            ),
            models.CheckConstraint(
                condition=models.Q(organization_remote_code="") | models.Q(system__isnull=False),
                name="organization_template_has_system",
            ),
        ]

    def clean(self):
        super().clean()
        self.organization_remote_code = (self.organization_remote_code or "").strip()
        if self.organization_remote_code and not self.system_id:
            raise ValidationError({"system": "Select a system for an organization template."})

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
    organization_remote_code = models.CharField(max_length=255, blank=True)
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
        self.organization_remote_code = (self.organization_remote_code or "").strip()
        if self.provider_id and self.provider.notification_type_id != self.notification_type_id:
            raise ValidationError({"provider": "Provider must match the notification type."})
        if (self.provider_id and not self.provider.is_available_for(
                self.system_id, self.organization_remote_code)):
            raise ValidationError({"provider": "Provider is not available for this system or organization."})
        if self.provider_config_id and self.provider_config.provider_id != self.provider_id:
            raise ValidationError({"provider_config": "Config must belong to the selected provider."})
        if (self.provider_config_id and not self.provider_config.is_available_for(
                self.system_id, self.organization_remote_code)):
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
