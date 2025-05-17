from django.urls import path
from .views import NotifyAPIsManager

urlpatterns = [
    path("send-notification/", NotifyAPIsManager().queue_send_notification, name="send_notification"),
    path("simple-api-sms-callback/", NotifyAPIsManager().belio_sms_provider_callback, name="simple_api_sms_callback"),
]