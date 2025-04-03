import uuid

from django.db import models

class BaseModel(models.Model):
    id = models.UUIDField(max_length=100, default=uuid.uuid4, unique=True, editable=False, primary_key=True)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    objects = models.Manager

    class Meta:
        abstract = True

class GenericBaseModel(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        abstract = True

class State(GenericBaseModel):
    def __str__(self):
        return self.name

    class Meta:
        ordering = ('-date_created',)

    @classmethod
    def sent(cls):
        state, created = cls.objects.get_or_create(name='Sent')
        return state

    @classmethod
    def failed(cls):
        state, created = cls.objects.get_or_create(name='Failed')
        return state

    @classmethod
    def confirmation_pending(cls):
        state, created = cls.objects.get_or_create(name='ConfirmationPending')
        return state

class NotificationType(GenericBaseModel):
    def __str__(self):
        return self.name

    @property
    def active_providers(self):
        return Provider.objects.filter(notification_type=self, is_active=True)

    class Meta:
        ordering = ('-date_created',)

class System(GenericBaseModel):
    email_signature = models.TextField(blank=True)
    sms_signature = models.CharField(max_length=160, blank=True)
    default_from_email = models.EmailField()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('-date_created',)

class Organisation(GenericBaseModel):
    system = models.ForeignKey(System, on_delete=models.CASCADE)

    def __str__(self):
        return "%s - %s" % (self.system.name, self.name)

    class Meta:
        ordering = ('-date_created',)


class Template(GenericBaseModel):
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('-date_created',)

class Provider(GenericBaseModel):
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    config = models.JSONField()
    priority = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    class_name = models.CharField(max_length=100,  help_text="Callback class containing its config")

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('-date_created',)

class Notification(BaseModel):
    unique_identifier = models.CharField(max_length=255, null=True, blank=True)
    system = models.ForeignKey(System, on_delete=models.CASCADE)
    organisation = models.ForeignKey(Organisation, null=True, blank=True, on_delete=models.CASCADE)
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    recipients = models.JSONField(default=list)
    template = models.ForeignKey(Template, null=True, on_delete=models.SET_NULL)
    provider = models.ForeignKey(Provider, null=True, on_delete=models.SET_NULL)
    context = models.JSONField()
    sent_time = models.DateTimeField(null=True)
    status = models.ForeignKey(State, on_delete=models.CASCADE)

    def __str__(self):
        return "%s %s notification to %s" %(self.system.name, self.notification_type.name, self.recipients)

    class Meta:
        ordering = ('-date_created',)

