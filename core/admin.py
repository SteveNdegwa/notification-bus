from django.contrib import admin

from core.models import State, NotificationType, System, Template, Provider, Notification


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'date_modified', 'date_created')
    search_fields = ('id', 'name', 'description')

@admin.register(NotificationType)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'date_modified', 'date_created')
    search_fields = ('id', 'name', 'description')

@admin.register(System)
class SystemAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'description', 'email_signature', 'sms_signature', 'default_from_email', 'date_modified',
        'date_created')
    search_fields = ('id', 'name', 'description', 'email_signature', 'sms_signature', 'default_from_email')

@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'name', 'description', 'notification_type', 'subject', 'body', 'is_active', 'date_modified',
        'date_created')
    list_filter = ('notification_type', 'is_active')
    search_fields = ('id', 'code', 'name', 'description', 'notification_type__name', 'subject')

@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'notification_type', 'config', 'is_active', 'date_modified', 'date_created')
    list_filter = ('notification_type', 'is_active')
    search_fields = ('id', 'name', 'description', 'notification_type__name')

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        'system', 'notification_type', 'recipient', 'template', 'data', 'sent_time', 'status', 'date_modified', 'date_created')
    list_filter = ('system', 'notification_type', 'template', 'status')
    search_fields = ('id', 'system__name', 'notification_type__name', 'recipient', 'template__code', 'status__name')

