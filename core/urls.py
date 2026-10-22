from django.urls import path
from . import views
from .views import NotifyAPIsManager

urlpatterns = [
    path("send-notification/", NotifyAPIsManager().queue_send_notification, name="send_notification"),
    path("callbacks/<slug:provider_slug>/", NotifyAPIsManager().provider_callback, name="provider_callback"),
    path("providers/", views.provider_list, name="provider_list"),
    path("providers/<uuid:provider_id>/", views.provider_detail, name="provider_detail"),
    path("organization-overrides/", views.organization_override_list, name="organization_override_list"),
    path("organization-overrides/create/", views.organization_override_create, name="organization_override_create"),
    path(
        "organization-provider-configs/",
        views.organization_provider_config_list,
        name="organization_provider_config_list",
    ),
    path(
        "organization-provider-configs/<uuid:config_id>/",
        views.organization_provider_config_detail,
        name="organization_provider_config_detail",
    ),
]
