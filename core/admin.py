from django.contrib import admin

from core.models import State, NotificationType, System, NotificationTemplate, Provider, ProviderConfig, Notification


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

@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'name', 'description', 'type', 'subject', 'body', 'is_active', 'date_modified', 'date_created')
    list_filter = ('type', 'is_active')
    search_fields = ('id', 'code', 'name', 'description', 'type__name', 'subject')

@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'is_active', 'date_modified', 'date_created')
    list_filter = ('is_active',)
    search_fields = ('id', 'name', 'description')

@admin.register(ProviderConfig)
class ProviderConfigAdmin(admin.ModelAdmin):
    list_display = ('provider', 'system', 'type', 'config', 'is_active', 'date_modified', 'date_created')
    list_filter = ('provider', 'system', 'type', 'is_active')
    search_fields = ('id', 'provider__name', 'system__name', 'type__name')

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('system', 'type', 'to', 'template', 'data', 'status', 'date_modified', 'date_created')
    list_filter = ('system', 'type', 'template', 'status')
    search_fields = ('id', 'system__name', 'type__name', 'to', 'template__code', 'status__name')

