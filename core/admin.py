from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from core.backend.notification_manager import NotificationManager
from core.models import (
    NotificationType,
    System,
    Template,
    Provider,
    Notification,
    NotificationTry,
    NotificationOverride,
    NotificationOverrideProvider,
    NotificationOverrideConfig,
    ProviderConfig,
    ProviderConfigDefinition,
    ProviderConfigFailureAlert,
    ProviderConfigEntry,
    ProviderCallback,
    SystemCallback,
    EmailThread,
)
from core.tasks import process_existing_notification, process_provider_callback, send_system_callback


TERMINAL_NOTIFICATION_STATUSES = {
    Notification.Status.SENT,
    Notification.Status.FAILED,
}

STATUS_COLORS = {
    Notification.Status.PENDING: ("#854d0e", "#fef3c7"),
    Notification.Status.PROCESSING: ("#075985", "#e0f2fe"),
    Notification.Status.GETTING_RECIPIENTS: ("#075985", "#e0f2fe"),
    Notification.Status.CONFIRMATION_PENDING: ("#6d28d9", "#ede9fe"),
    Notification.Status.SENT: ("#166534", "#dcfce7"),
    Notification.Status.FAILED: ("#991b1b", "#fee2e2"),
    Notification.RecipientResolutionStatus.NOT_REQUIRED: ("#374151", "#f3f4f6"),
    Notification.RecipientResolutionStatus.PENDING: ("#854d0e", "#fef3c7"),
    Notification.RecipientResolutionStatus.PROCESSING: ("#075985", "#e0f2fe"),
    Notification.RecipientResolutionStatus.RESOLVED: ("#166534", "#dcfce7"),
    Notification.RecipientResolutionStatus.FAILED: ("#991b1b", "#fee2e2"),
    ProviderCallback.Status.RECEIVED: ("#075985", "#e0f2fe"),
    ProviderCallback.Status.PROCESSING: ("#6d28d9", "#ede9fe"),
    ProviderCallback.Status.PROCESSED: ("#166534", "#dcfce7"),
    ProviderCallback.Status.FAILED: ("#991b1b", "#fee2e2"),
    SystemCallback.Status.PENDING: ("#854d0e", "#fef3c7"),
    SystemCallback.Status.SENDING: ("#075985", "#e0f2fe"),
    SystemCallback.Status.SENT: ("#166534", "#dcfce7"),
    SystemCallback.Status.FAILED: ("#991b1b", "#fee2e2"),
    NotificationTry.Status.PROCESSING: ("#075985", "#e0f2fe"),
    NotificationTry.Status.CONFIRMATION_PENDING: ("#6d28d9", "#ede9fe"),
    NotificationTry.Status.SUCCESS: ("#166534", "#dcfce7"),
    NotificationTry.Status.FAILED: ("#991b1b", "#fee2e2"),
}


def status_badge(value):
    color, background = STATUS_COLORS.get(value, ("#374151", "#f3f4f6"))
    return format_html(
        '<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
        'font-weight:600;color:{};background:{};">{}</span>',
        color,
        background,
        value,
    )


def truncate_value(value, length=75):
    text = str(value)
    return text if len(text) <= length else text[:length] + "..."


def apply_scope_filter(queryset, system_id=None, organization_remote_code=None):
    if not system_id and not organization_remote_code:
        return queryset
    eligible_ids = []
    for item in queryset.prefetch_related("systems"):
        system_ids = {str(system.id) for system in item.systems.all()}
        remote_codes = item.organization_remote_codes or []
        if (
            (not system_ids and not remote_codes)
            or str(system_id or "") in system_ids
            or organization_remote_code in remote_codes
        ):
            eligible_ids.append(item.id)
    return queryset.filter(id__in=eligible_ids)


class BaseReadonlyInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ProviderConfigEntryInline(admin.TabularInline):
    model = ProviderConfigEntry
    fields = ("definition", "value")
    autocomplete_fields = ("definition",)
    extra = 1
    verbose_name_plural = "Config entries"

    class Media:
        js = ("core/admin/dependent_autocomplete.js",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "definition":
            field.widget.attrs.update({
                "data-forward-field": "#id_provider",
                "data-forward-param": "provider",
            })
        return field

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if obj is not None:
            formset.form.base_fields["definition"].queryset = ProviderConfigDefinition.objects.filter(
                provider=obj.provider,
            ).order_by("name")
        return formset


class ProviderConfigDefinitionInline(admin.TabularInline):
    model = ProviderConfigDefinition
    fields = ("name", "key", "value_type", "description")
    extra = 0
    show_change_link = True
    verbose_name_plural = "Config definitions"


class ProviderConfigInline(admin.TabularInline):
    model = ProviderConfig
    fields = ("name", "description", "priority", "is_active")
    extra = 0
    show_change_link = True
    verbose_name_plural = "Named configurations (open one to edit its entries)"


class ProviderCallbackInline(BaseReadonlyInline):
    model = ProviderCallback
    fields = ("id", "status_colored", "short_data", "processed_at", "date_created")
    readonly_fields = fields

    def status_colored(self, obj):
        return status_badge(obj.status)

    status_colored.short_description = "Status"

    def short_data(self, obj):
        return truncate_value(obj.data)

    short_data.short_description = "Data"


class SystemCallbackInline(BaseReadonlyInline):
    model = SystemCallback
    fields = (
        "id",
        "status_colored",
        "attempts",
        "response_status_code",
        "next_retry_at",
        "sent_at",
        "date_created",
    )
    readonly_fields = fields

    def status_colored(self, obj):
        return status_badge(obj.status)

    status_colored.short_description = "Status"


class NotificationSystemCallbackInline(SystemCallbackInline):
    fk_name = "notification"


class NotificationTryInline(BaseReadonlyInline):
    model = NotificationTry
    fields = (
        "delivery_cycle", "provider", "provider_config", "status_colored", "error", "date_created", "date_modified",
    )
    readonly_fields = fields

    def status_colored(self, obj):
        return status_badge(obj.status)

    status_colored.short_description = "Status"


@admin.register(NotificationType)
class NotificationTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "active_provider_count", "date_modified", "date_created")
    search_fields = ("id", "name", "description")
    readonly_fields = ("id", "date_created", "date_modified")
    date_hierarchy = "date_created"
    ordering = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("provider_set")

    def active_provider_count(self, obj):
        return obj.provider_set.filter(is_active=True).count()

    active_provider_count.short_description = "Active Providers"


@admin.register(System)
class SystemAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "description",
        "is_active",
        "is_internal",
        "callback_enabled",
        "webhook_url",
        "email_signature",
        "sms_signature",
        "date_modified",
        "date_created",
    )
    list_filter = ("is_active", "is_internal", "callback_enabled", "date_created", "date_modified")
    search_fields = ("id", "name", "description", "email_signature", "sms_signature", "api_key", "webhook_url")
    readonly_fields = ("id", "api_key", "date_created", "date_modified")
    fields = (
        "id",
        "name",
        "description",
        "api_key",
        "is_active",
        "is_internal",
        "email_signature",
        "sms_signature",
        "callback_enabled",
        "webhook_url",
        "date_created",
        "date_modified",
    )
    inlines = (SystemCallbackInline,)
    date_hierarchy = "date_created"
    ordering = ("name",)


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "description",
        "notification_type",
        "scope_summary",
        "priority",
        "class_name",
        "is_active",
        "date_modified",
        "date_created",
    )
    list_filter = (
        "notification_type",
        "sends_callbacks",
        "verify_callback_signature",
        "is_active",
        "date_created",
        "date_modified",
    )
    search_fields = ("id", "name", "slug", "description", "class_name", "notification_type__name")
    readonly_fields = ("id", "date_created", "date_modified")
    fields = (
        "id",
        "name",
        "slug",
        "description",
        "notification_type",
        "systems",
        "organization_remote_codes",
        "priority",
        "class_name",
        "callback_verification_config",
        "sends_callbacks",
        "verify_callback_signature",
        "is_active",
        "date_created",
        "date_modified",
    )
    autocomplete_fields = ("notification_type",)
    filter_horizontal = ("systems",)
    inlines = (ProviderConfigDefinitionInline, ProviderConfigInline, ProviderCallbackInline)
    date_hierarchy = "date_created"
    ordering = ("notification_type__name", "priority", "name")
    list_select_related = ("notification_type",)
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("systems")

    def scope_summary(self, obj):
        systems = [system.name for system in obj.systems.all()]
        remote_codes = obj.organization_remote_codes or []
        if not systems and not remote_codes:
            return "Global"
        return "; ".join(filter(None, [
            f"Systems: {', '.join(systems)}" if systems else "",
            f"Organization codes: {', '.join(remote_codes)}" if remote_codes else "",
        ]))

    scope_summary.short_description = "Scope"

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        notification_type_id = request.GET.get("notification_type")
        system_id = request.GET.get("system")
        organization_remote_code = request.GET.get("organization_remote_code")
        override_id = request.GET.get("override")
        if override_id:
            override = NotificationOverride.objects.filter(pk=override_id).values(
                "notification_type_id", "system_id", "organization_remote_code",
            ).first()
            if override:
                notification_type_id = notification_type_id or override["notification_type_id"]
                system_id = system_id or override["system_id"]
                organization_remote_code = (
                    organization_remote_code or override["organization_remote_code"]
                )
        if notification_type_id:
            queryset = queryset.filter(notification_type_id=notification_type_id)
        queryset = apply_scope_filter(queryset, system_id, organization_remote_code)
        return queryset, may_have_duplicates

    def short_callback_verification_config(self, obj):
        return truncate_value(obj.callback_verification_config)

    short_callback_verification_config.short_description = "Verification Config"


@admin.register(ProviderConfig)
class ProviderConfigAdmin(admin.ModelAdmin):
    list_display = (
        "name", "provider", "description", "scope_summary", "priority", "is_active",
        "date_modified",
    )
    list_filter = ("provider__notification_type", "provider", "is_active")
    search_fields = ("id", "name", "description", "provider__name", "provider__slug")
    autocomplete_fields = ("provider",)
    filter_horizontal = ("systems",)
    readonly_fields = ("id", "date_created", "date_modified")
    fields = (
        "id",
        "name",
        "description",
        "provider",
        "systems",
        "organization_remote_codes",
        "priority",
        "is_active",
        "date_created",
        "date_modified",
    )
    inlines = (ProviderConfigEntryInline,)
    ordering = ("provider__notification_type__name", "provider__priority", "priority", "name")
    list_select_related = ("provider", "provider__notification_type")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("systems")

    def scope_summary(self, obj):
        systems = [system.name for system in obj.systems.all()]
        remote_codes = obj.organization_remote_codes or []
        if not systems and not remote_codes:
            return "Global"
        return "; ".join(filter(None, [
            f"Systems: {', '.join(systems)}" if systems else "",
            f"Organization codes: {', '.join(remote_codes)}" if remote_codes else "",
        ]))

    scope_summary.short_description = "Scope"

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        provider_id = request.GET.get("provider")
        if provider_id:
            queryset = queryset.filter(provider_id=provider_id)
        queryset = apply_scope_filter(
            queryset, request.GET.get("system"), request.GET.get("organization_remote_code"),
        )
        return queryset, may_have_duplicates


@admin.register(ProviderConfigDefinition)
class ProviderConfigDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "provider", "value_type", "description", "date_modified")
    list_filter = ("provider", "value_type")
    search_fields = ("id", "name", "key", "description", "provider__name")
    autocomplete_fields = ("provider",)
    readonly_fields = ("id", "date_created", "date_modified")
    ordering = ("provider__name", "name")
    list_select_related = ("provider",)

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        provider_id = request.GET.get("provider")
        if provider_id:
            queryset = queryset.filter(provider_id=provider_id)
        return queryset, may_have_duplicates


class NotificationOverrideProviderInline(admin.TabularInline):
    model = NotificationOverrideProvider
    fields = ("provider", "priority", "use_other_configs")
    autocomplete_fields = ("provider",)
    extra = 0
    show_change_link = True
    verbose_name_plural = "Providers in fallback order (open a row to choose its configs)"

    class Media:
        js = ("core/admin/dependent_autocomplete.js",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "provider":
            field.widget.attrs.update({
                "data-forward-field": "#id_notification_type",
                "data-forward-param": "notification_type",
                "data-forward-system-field": "#id_system",
                "data-forward-system-param": "system",
                "data-forward-organization-field": "#id_organization_remote_code",
                "data-forward-organization-param": "organization_remote_code",
            })
        return field

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if obj is not None:
            formset.form.base_fields["provider"].queryset = apply_scope_filter(Provider.objects.filter(
                notification_type=obj.notification_type,
            ), obj.system_id, obj.organization_remote_code).order_by("priority", "name")
        return formset


@admin.register(NotificationOverride)
class NotificationOverrideAdmin(admin.ModelAdmin):
    list_display = ("scope", "notification_type", "use_other_providers", "date_modified", "date_created")
    list_filter = ("notification_type", "use_other_providers", "system")
    search_fields = ("id", "system__name", "organization_remote_code")
    autocomplete_fields = ("system", "notification_type")
    readonly_fields = ("id", "date_created", "date_modified")
    fields = (
        "id", "system", "organization_remote_code", "notification_type", "use_other_providers",
        "date_created", "date_modified",
    )
    inlines = (NotificationOverrideProviderInline,)
    list_select_related = ("system", "notification_type")

    def scope(self, obj):
        return obj.organization_remote_code or obj.system

    scope.short_description = "Applies to"


class NotificationOverrideConfigInline(admin.TabularInline):
    model = NotificationOverrideConfig
    fields = ("provider_config", "priority")
    autocomplete_fields = ("provider_config",)
    extra = 0

    class Media:
        js = ("core/admin/dependent_autocomplete.js",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "provider_config":
            field.widget.attrs.update({
                "data-forward-field": "#id_provider",
                "data-forward-param": "provider",
            })
        return field

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if obj is not None:
            formset.form.base_fields["provider_config"].queryset = apply_scope_filter(ProviderConfig.objects.filter(
                provider=obj.provider,
            ), obj.override.system_id, obj.override.organization_remote_code).order_by("priority", "name")
            formset.form.base_fields["provider_config"].widget.attrs.update({
                "data-forward-system-value": str(obj.override.system_id or ""),
                "data-forward-organization-value": obj.override.organization_remote_code,
            })
        return formset


@admin.register(NotificationOverrideProvider)
class NotificationOverrideProviderAdmin(admin.ModelAdmin):
    list_display = ("override", "provider", "priority", "use_other_configs", "date_modified")
    list_filter = ("override__notification_type", "provider", "use_other_configs")
    search_fields = ("id", "override__system__name", "override__organization_remote_code", "provider__name")
    autocomplete_fields = ("override", "provider")
    readonly_fields = ("id", "date_created", "date_modified")
    inlines = (NotificationOverrideConfigInline,)
    list_select_related = ("override", "override__notification_type", "provider")

    class Media:
        js = ("core/admin/dependent_autocomplete.js",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "provider":
            field.widget.attrs.update({
                "data-forward-field": "#id_override",
                "data-forward-param": "override",
            })
        return field

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        override_id = obj.override_id if obj else request.GET.get("override") or request.POST.get("override")
        if override_id:
            override = NotificationOverride.objects.filter(pk=override_id).first()
            if override:
                form.base_fields["provider"].queryset = apply_scope_filter(Provider.objects.filter(
                    notification_type=override.notification_type,
                ), override.system_id, override.organization_remote_code).order_by("priority", "name")
        return form


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "system",
        "organization_remote_code",
        "description",
        "notification_type",
        "subject",
        "short_body",
        "is_active",
        "date_modified",
        "date_created",
    )
    list_filter = ("system", "organization_remote_code", "notification_type", "is_active", "date_created", "date_modified")
    search_fields = ("id", "name", "code", "system__name", "organization_remote_code", "description", "notification_type__name", "subject", "body")
    readonly_fields = ("id", "date_created", "date_modified")
    fields = (
        "id",
        "name",
        "code",
        "system",
        "organization_remote_code",
        "description",
        "notification_type",
        "subject",
        "body",
        "is_active",
        "date_created",
        "date_modified",
    )
    autocomplete_fields = ("system", "notification_type")
    date_hierarchy = "date_created"
    list_select_related = ("system", "notification_type")
    ordering = ("system__name", "organization_remote_code", "notification_type__name", "name")

    def short_body(self, obj):
        return truncate_value(obj.body)

    short_body.short_description = "Body"


class ThreadNotificationInline(BaseReadonlyInline):
    model = Notification
    fields = ("id", "status", "recipients", "sent_time", "date_created")
    readonly_fields = fields


@admin.register(EmailThread)
class EmailThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "system", "provider", "sender_address", "date_created")
    list_filter = ("system", "provider", "date_created")
    search_fields = ("id", "name", "system__name", "sender_address")
    readonly_fields = ("id", "name", "system", "provider", "sender_address", "date_created", "date_modified")
    inlines = (ThreadNotificationInline,)
    list_select_related = ("system", "provider")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "delivery_cycle",
        "system",
        "organization_remote_code",
        "unique_identifier",
        "email_thread",
        "notification_type",
        "provider",
        "provider_config",
        "template",
        "status_colored",
        "recipient_resolution_status_colored",
        "short_user_ids",
        "short_recipients",
        "short_context",
        "sent_time",
        "system_callback_action",
        "date_modified",
        "date_created",
    )
    list_filter = (
        "status",
        "recipient_resolution_status",
        "system",
        "organization_remote_code",
        "notification_type",
        "provider",
        "provider_config",
        "template",
        "sent_time",
        "date_created",
        "date_modified",
    )
    search_fields = (
        "id",
        "system__name",
        "unique_identifier",
        "notification_type__name",
        "user_ids",
        "recipients",
        "template__name",
        "provider__name",
        "provider__slug",
        "status",
        "recipient_resolution_status",
        "recipient_resolution_error",
        "email_thread__id",
        "email_thread__name",
        "email_message_id",
    )
    readonly_fields = (
        "id",
        "delivery_cycle",
        "status_colored",
        "recipient_resolution_status_colored",
        "send_system_callback_button",
        "email_thread",
        "email_message_id",
        "email_provider_message_id",
        "email_references",
        "date_created",
        "date_modified",
    )
    fields = (
        "id",
        "delivery_cycle",
        "system",
        "organization_remote_code",
        "unique_identifier",
        "notification_type",
        "user_ids",
        "recipients",
        "recipient_resolution_status",
        "recipient_resolution_status_colored",
        "recipient_resolution_error",
        "template",
        "provider",
        "provider_config",
        "context",
        "email_thread",
        "email_message_id",
        "email_provider_message_id",
        "email_references",
        "sent_time",
        "status",
        "status_colored",
        "send_system_callback_button",
        "date_created",
        "date_modified",
    )
    autocomplete_fields = ("system", "notification_type", "template", "provider", "provider_config")
    inlines = (NotificationTryInline, NotificationSystemCallbackInline)
    actions = ("retry_notification_processing", "send_system_callback_for_terminal_notifications")
    date_hierarchy = "date_created"
    list_select_related = ("system", "notification_type", "template", "provider", "provider_config")

    class Media:
        js = ("core/admin/dependent_autocomplete.js",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "provider":
            field.widget.attrs.update({
                "data-forward-field": "#id_notification_type",
                "data-forward-param": "notification_type",
                "data-forward-system-field": "#id_system",
                "data-forward-system-param": "system",
                "data-forward-organization-field": "#id_organization_remote_code",
                "data-forward-organization-param": "organization_remote_code",
            })
        elif db_field.name == "provider_config":
            field.widget.attrs.update({
                "data-forward-field": "#id_provider",
                "data-forward-param": "provider",
                "data-forward-system-field": "#id_system",
                "data-forward-system-param": "system",
                "data-forward-organization-field": "#id_organization_remote_code",
                "data-forward-organization-param": "organization_remote_code",
            })
        return field

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "system",
            "notification_type",
            "template",
            "provider",
            "provider_config",
            "email_thread",
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/send-system-callback/",
                self.admin_site.admin_view(self.send_system_callback_view),
                name="core_notification_send_system_callback",
            ),
        ]
        return custom_urls + urls

    def status_colored(self, obj):
        return status_badge(obj.status)

    status_colored.short_description = "Status"
    status_colored.admin_order_field = "status"

    def recipient_resolution_status_colored(self, obj):
        return status_badge(obj.recipient_resolution_status)

    recipient_resolution_status_colored.short_description = "Recipient Resolution"
    recipient_resolution_status_colored.admin_order_field = "recipient_resolution_status"

    def short_user_ids(self, obj):
        return truncate_value(obj.user_ids, 50)

    short_user_ids.short_description = "User IDs"

    def short_recipients(self, obj):
        return truncate_value(obj.recipients, 50)

    short_recipients.short_description = "Recipients"

    def short_context(self, obj):
        return truncate_value(obj.context)

    short_context.short_description = "Context"

    def can_send_system_callback(self, obj):
        return (
            obj.status in TERMINAL_NOTIFICATION_STATUSES
            and obj.system.callback_enabled
            and bool(obj.system.webhook_url)
        )

    def system_callback_action(self, obj):
        if not self.can_send_system_callback(obj):
            return "-"
        return self._system_callback_link(obj, "Send callback")

    system_callback_action.short_description = "System Callback"

    def send_system_callback_button(self, obj):
        if not obj or not self.can_send_system_callback(obj):
            return "Available only for terminal notifications with system webhook callbacks enabled."
        return self._system_callback_link(obj, "Send system callback")

    send_system_callback_button.short_description = "System callback action"

    @staticmethod
    def _system_callback_link(obj, label):
        url = reverse("admin:core_notification_send_system_callback", args=[obj.pk])
        return format_html('<a class="button" href="{}">{}</a>', url, label)

    def send_system_callback_view(self, request, object_id):
        notification = self.get_object(request, object_id)
        if notification is None:
            self.message_user(request, "Notification not found.", level=messages.ERROR)
            return redirect("..")

        if not self.can_send_system_callback(notification):
            self.message_user(
                request,
                "System callback can only be sent for terminal notifications with webhook callbacks enabled.",
                level=messages.WARNING,
            )
        else:
            NotificationManager().update_notification_status(
                notification_id=notification.id,
                status=notification.status,
                sent_time=notification.sent_time,
                provider=notification.provider,
            )
            self.message_user(request, "System callback queued.", level=messages.SUCCESS)

        return redirect(reverse("admin:core_notification_change", args=[object_id]))

    @admin.action(description="Retry notification processing")
    def retry_notification_processing(self, request, queryset):
        count = 0
        for notification in queryset:
            with transaction.atomic():
                notification = Notification.objects.select_for_update().get(pk=notification.pk)
                notification.delivery_cycle += 1
                notification.status = Notification.Status.PENDING
                notification.provider = None
                notification.provider_config = None
                notification.sent_time = None
                notification.email_message_id = ""
                notification.email_provider_message_id = ""
                notification.email_references = []
                notification.save(update_fields=[
                    "delivery_cycle",
                    "status",
                    "provider",
                    "provider_config",
                    "sent_time",
                    "email_message_id",
                    "email_provider_message_id",
                    "email_references",
                    "date_modified",
                ])
                transaction.on_commit(
                    lambda notification_id=str(notification.id): process_existing_notification.delay(notification_id)
                )
            count += 1
        self.message_user(
            request,
            f"Started a fresh delivery cycle for {count} notification(s). Previous tries were retained.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Send system callback for terminal notifications")
    def send_system_callback_for_terminal_notifications(self, request, queryset):
        count = 0
        skipped = 0
        for notification in queryset.select_related("system", "provider"):
            if not self.can_send_system_callback(notification):
                skipped += 1
                continue
            NotificationManager().update_notification_status(
                notification_id=notification.id,
                status=notification.status,
                sent_time=notification.sent_time,
                provider=notification.provider,
            )
            count += 1
        self.message_user(
            request,
            f"Queued {count} system callback(s). Skipped {skipped} notification(s).",
            level=messages.SUCCESS if count else messages.WARNING,
        )


@admin.register(ProviderConfigFailureAlert)
class ProviderConfigFailureAlertAdmin(admin.ModelAdmin):
    list_display = (
        "provider", "provider_config", "notification", "trigger_try", "recipients", "date_created",
    )
    list_filter = ("provider", "provider_config", "date_created")
    search_fields = (
        "provider__name", "provider_config__name", "provider_config__description",
        "notification__id", "trigger_try__id", "recipients",
    )
    readonly_fields = (
        "id", "provider", "provider_config", "trigger_try", "notification", "recipients",
        "date_created", "date_modified",
    )
    list_select_related = ("provider", "provider_config", "notification", "trigger_try")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProviderCallback)
class ProviderCallbackAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider",
        "provider_slug",
        "status_colored",
        "short_data",
        "short_state",
        "short_error",
        "processed_at",
        "date_modified",
        "date_created",
    )
    list_filter = ("status", "provider", "provider__notification_type", "processed_at", "date_created", "date_modified")
    search_fields = (
        "id",
        "provider__name",
        "provider__slug",
        "provider__notification_type__name",
        "data",
        "state",
        "headers",
        "error",
    )
    readonly_fields = ("id", "status_colored", "date_created", "date_modified")
    fields = (
        "id",
        "provider",
        "status",
        "status_colored",
        "data",
        "headers",
        "state",
        "error",
        "processed_at",
        "date_created",
        "date_modified",
    )
    autocomplete_fields = ("provider",)
    actions = ("retry_provider_callback_processing",)
    date_hierarchy = "date_created"
    list_select_related = ("provider", "provider__notification_type")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("provider", "provider__notification_type")

    def status_colored(self, obj):
        return status_badge(obj.status)

    status_colored.short_description = "Status"
    status_colored.admin_order_field = "status"

    def provider_slug(self, obj):
        return obj.provider.slug

    provider_slug.short_description = "Provider Slug"
    provider_slug.admin_order_field = "provider__slug"

    def short_data(self, obj):
        return truncate_value(obj.data)

    short_data.short_description = "Data"

    def short_state(self, obj):
        return truncate_value(obj.state)

    short_state.short_description = "State"

    def short_error(self, obj):
        return truncate_value(obj.error)

    short_error.short_description = "Error"

    @admin.action(description="Retry provider callback processing")
    def retry_provider_callback_processing(self, request, queryset):
        count = 0
        for callback in queryset:
            process_provider_callback.delay(str(callback.id))
            count += 1
        self.message_user(request, f"Queued {count} provider callback(s) for processing.", level=messages.SUCCESS)


@admin.register(SystemCallback)
class SystemCallbackAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "system",
        "notification",
        "status_colored",
        "attempts",
        "response_status_code",
        "short_payload",
        "short_error",
        "next_retry_at",
        "sent_at",
        "date_modified",
        "date_created",
    )
    list_filter = (
        "status",
        "system",
        "response_status_code",
        "next_retry_at",
        "sent_at",
        "date_created",
        "date_modified",
    )
    search_fields = (
        "id",
        "system__name",
        "notification__id",
        "notification__unique_identifier",
        "payload",
        "error",
        "response_body",
    )
    readonly_fields = ("id", "status_colored", "date_created", "date_modified")
    fields = (
        "id",
        "system",
        "notification",
        "payload",
        "status",
        "status_colored",
        "attempts",
        "next_retry_at",
        "response_status_code",
        "response_body",
        "error",
        "sent_at",
        "date_created",
        "date_modified",
    )
    autocomplete_fields = ("system", "notification")
    actions = ("retry_system_callbacks",)
    date_hierarchy = "date_created"
    list_select_related = ("system", "notification", "notification__notification_type")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "system",
            "notification",
            "notification__notification_type",
        )

    def status_colored(self, obj):
        return status_badge(obj.status)

    status_colored.short_description = "Status"
    status_colored.admin_order_field = "status"

    def short_payload(self, obj):
        return truncate_value(obj.payload)

    short_payload.short_description = "Payload"

    def short_error(self, obj):
        return truncate_value(obj.error)

    short_error.short_description = "Error"

    @admin.action(description="Retry selected system callbacks")
    def retry_system_callbacks(self, request, queryset):
        count = 0
        for callback in queryset:
            send_system_callback.delay(str(callback.id))
            count += 1
        self.message_user(request, f"Queued {count} system callback(s) for sending.", level=messages.SUCCESS)
