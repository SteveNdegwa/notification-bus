from django.urls import path
from .views import NotifyAPIsManager

urlpatterns = [
    path("send-notification/", NotifyAPIsManager().queue_send_notification, name="send_notification"),
    path("callbacks/<slug:provider_slug>/", NotifyAPIsManager().provider_callback, name="provider_callback"),
]
