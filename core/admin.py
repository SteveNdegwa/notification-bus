from django.contrib import admin

from core.models import State, NotificationType, System, Template, Provider, Notification, Organisation


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'date_modified', 'date_created')
    search_fields = ('id', 'name', 'description')

@admin.register(NotificationType)
class NotificationTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'date_modified', 'date_created')
    search_fields = ('id', 'name', 'description')

@admin.register(System)
class SystemAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'description', 'email_signature', 'sms_signature', 'default_from_email', 'date_modified',
        'date_created')
    search_fields = ('id', 'name', 'description', 'email_signature', 'sms_signature', 'default_from_email')

@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'system', 'date_modified', 'date_created')
    list_filter = ('system',)
    search_fields = ('id', 'name', 'description', 'system__name')

@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'description', 'notification_type', 'subject', 'body', 'is_active', 'date_modified',
        'date_created')
    list_filter = ('notification_type', 'is_active')
    search_fields = ('id', 'name', 'description', 'notification_type__name', 'subject')

@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'description', 'notification_type', 'config', 'priority', 'is_active', 'date_modified', 'date_created')
    list_filter = ('notification_type', 'is_active')
    search_fields = ('id', 'name', 'description', 'notification_type__name')

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        'system', 'organisation', 'unique_identifier', 'notification_type', 'recipients', 'template', 'provider',
        'context', 'sent_time', 'status', 'date_modified', 'date_created')
    list_filter = ('system', 'organisation', 'notification_type', 'template', 'provider', 'status')
    search_fields = (
        'id', 'system__name', 'organisation__name', 'unique_identifier', 'notification_type__name', 'recipients',
        'template__name', 'provider__name', 'status__name')

