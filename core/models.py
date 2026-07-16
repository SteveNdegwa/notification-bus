import secrets

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
    slug = models.SlugField(max_length=100, unique=True)
    priority = models.IntegerField(null=True, blank=True)
    class_name = models.CharField(max_length=100, help_text="Callback class containing its config")
    default_config = models.JSONField()
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

    def get_config(self, system: System):
        try:
            override = SystemProviderConfig.objects.get(system=system, provider=self)
            return override.config
        except SystemProviderConfig.DoesNotExist:
            return self.default_config

class SystemProviderConfig(BaseModel):
    system = models.ForeignKey(System, on_delete=models.CASCADE)
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE)
    config = models.JSONField()

    class Meta:
        unique_together = ("system", "provider")
        ordering = ('system__name', 'provider__name')

    def __str__(self):
        return f"{self.system.name} - {self.provider.name} Config"

class Template(GenericBaseModel):
    name = models.CharField(max_length=100)
    system = models.ForeignKey(System, null=True, blank=True, on_delete=models.CASCADE)
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('name',)
        constraints = [
            models.UniqueConstraint(
                fields=["system", "name"],
                condition=models.Q(system__isnull=False),
                name="unique_template_name_per_system",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(system__isnull=True),
                name="unique_global_template_name",
            ),
        ]

    def __str__(self):
        return self.name

class Notification(BaseModel):
    class Status(models.TextChoices):
        PENDING = "Pending"
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
    system = models.ForeignKey(System, on_delete=models.CASCADE)
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
    context = models.JSONField()
    send_exception = models.TextField(blank=True)
    sent_time = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ('-date_created',)

    def __str__(self):
        return "%s %s notification to %s" %(self.system.name, self.notification_type.name, self.recipients)


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
