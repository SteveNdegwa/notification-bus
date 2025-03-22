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


class NotificationType(GenericBaseModel):
    def __str__(self):
        return self.name

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


class NotificationTemplate(GenericBaseModel):
    code = models.CharField(max_length=100, unique=True)
    type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('-date_created',)

class Provider(GenericBaseModel):
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('-date_created',)

class ProviderConfig(BaseModel):
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name='configs')
    system = models.ForeignKey(System, on_delete=models.CASCADE)
    type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    config = models.JSONField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return "%s %s %s config" % (self.provider.name, self.system.name, self.type.name)

    class Meta:
        ordering = ('-date_created',)

class Notification(BaseModel):
    system = models.ForeignKey(System, on_delete=models.CASCADE)
    type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    to = models.CharField(max_length=255)
    template = models.ForeignKey(NotificationTemplate, on_delete=models.SET_NULL, null=True)
    data = models.JSONField()
    status = models.ForeignKey(State, on_delete=models.CASCADE)

    def __str__(self):
        return "%s %s notification to %s" %(self.system.name, self.type.name, self.to)

    class Meta:
        ordering = ('-date_created',)

