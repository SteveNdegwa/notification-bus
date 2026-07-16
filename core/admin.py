from django.contrib import admin, messages
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
    SystemProviderConfig,
    ProviderCallback,
    SystemCallback,
)
from core.tasks import process_existing_notification, process_provider_callback, send_system_callback


TERMINAL_NOTIFICATION_STATUSES = {
    Notification.Status.SENT,
    Notification.Status.FAILED,
}

STATUS_COLORS = {
    Notification.Status.PENDING: ("#854d0e", "#fef3c7"),
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


class BaseReadonlyInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class SystemProviderConfigForSystemInline(admin.TabularInline):
    model = SystemProviderConfig
    extra = 0
    autocomplete_fields = ("provider",)
    fields = ("provider", "short_config", "date_created", "date_modified")
    readonly_fields = ("short_config", "date_created", "date_modified")
    show_change_link = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("provider", "system")

    def short_config(self, obj):
        return truncate_value(obj.config)

    short_config.short_description = "Config"


class SystemProviderConfigForProviderInline(admin.TabularInline):
    model = SystemProviderConfig
    extra = 0
    autocomplete_fields = ("system",)
    fields = ("system", "short_config", "date_created", "date_modified")
    readonly_fields = ("short_config", "date_created", "date_modified")
    show_change_link = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("provider", "system")

    def short_config(self, obj):
        return truncate_value(obj.config)

    short_config.short_description = "Config"


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
    inlines = (SystemProviderConfigForSystemInline, SystemCallbackInline)
    date_hierarchy = "date_created"
    ordering = ("name",)


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "description",
        "notification_type",
        "priority",
        "class_name",
        "short_config",
        "short_callback_verification_config",
        "sends_callbacks",
        "verify_callback_signature",
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
        "priority",
        "class_name",
        "default_config",
        "callback_verification_config",
        "sends_callbacks",
        "verify_callback_signature",
        "is_active",
        "date_created",
        "date_modified",
    )
    autocomplete_fields = ("notification_type",)
    inlines = (SystemProviderConfigForProviderInline, ProviderCallbackInline)
    date_hierarchy = "date_created"
    ordering = ("notification_type__name", "priority", "name")
    list_select_related = ("notification_type",)
    prepopulated_fields = {"slug": ("name",)}

    def short_config(self, obj):
        return truncate_value(obj.default_config)

    short_config.short_description = "Default Config"

    def short_callback_verification_config(self, obj):
        return truncate_value(obj.callback_verification_config)

    short_callback_verification_config.short_description = "Verification Config"


@admin.register(SystemProviderConfig)
class SystemProviderConfigAdmin(admin.ModelAdmin):
    list_display = ("system", "provider", "provider_type", "short_config", "date_created", "date_modified")
    list_filter = ("system", "provider", "provider__notification_type", "date_created", "date_modified")
    search_fields = ("id", "system__name", "provider__name", "provider__slug", "provider__notification_type__name")
    ordering = ("system__name", "provider__name")
    readonly_fields = ("id", "date_created", "date_modified")
    fields = ("id", "system", "provider", "config", "date_created", "date_modified")
    autocomplete_fields = ("system", "provider")
    date_hierarchy = "date_created"
    list_select_related = ("system", "provider", "provider__notification_type")

    def provider_type(self, obj):
        return obj.provider.notification_type

    provider_type.short_description = "Type"

    def short_config(self, obj):
        return truncate_value(obj.config)

    short_config.short_description = "Config"


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "system",
        "description",
        "notification_type",
        "subject",
        "short_body",
        "is_active",
        "date_modified",
        "date_created",
    )
    list_filter = ("system", "notification_type", "is_active", "date_created", "date_modified")
    search_fields = ("id", "name", "system__name", "description", "notification_type__name", "subject", "body")
    readonly_fields = ("id", "date_created", "date_modified")
    fields = (
        "id",
        "name",
        "system",
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
    ordering = ("system__name", "notification_type__name", "name")

    def short_body(self, obj):
        return truncate_value(obj.body)

    short_body.short_description = "Body"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "system",
        "unique_identifier",
        "notification_type",
        "provider",
        "template",
        "status_colored",
        "recipient_resolution_status_colored",
        "short_user_ids",
        "short_recipients",
        "short_context",
        "short_send_exception",
        "sent_time",
        "system_callback_action",
        "date_modified",
        "date_created",
    )
    list_filter = (
        "status",
        "recipient_resolution_status",
        "system",
        "notification_type",
        "provider",
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
        "send_exception",
    )
    readonly_fields = (
        "id",
        "status_colored",
        "recipient_resolution_status_colored",
        "send_system_callback_button",
        "send_exception",
        "date_created",
        "date_modified",
    )
    fields = (
        "id",
        "system",
        "unique_identifier",
        "notification_type",
        "user_ids",
        "recipients",
        "recipient_resolution_status",
        "recipient_resolution_status_colored",
        "recipient_resolution_error",
        "template",
        "provider",
        "context",
        "send_exception",
        "sent_time",
        "status",
        "status_colored",
        "send_system_callback_button",
        "date_created",
        "date_modified",
    )
    autocomplete_fields = ("system", "notification_type", "template", "provider")
    inlines = (NotificationSystemCallbackInline,)
    actions = ("retry_notification_processing", "send_system_callback_for_terminal_notifications")
    date_hierarchy = "date_created"
    list_select_related = ("system", "notification_type", "template", "provider")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "system",
            "notification_type",
            "template",
            "provider",
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

    def short_send_exception(self, obj):
        return truncate_value(obj.send_exception)

    short_send_exception.short_description = "Send Exception"

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
            process_existing_notification.delay(str(notification.id))
            count += 1
        self.message_user(request, f"Queued {count} notification(s) for processing.", level=messages.SUCCESS)

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
