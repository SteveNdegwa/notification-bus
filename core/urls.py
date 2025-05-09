from django.urls import path
from .views import NotifyAPIsManager

urlpatterns = [
    path("send-notification/", NotifyAPIsManager().queue_send_notification, name="send_notification"),
    path("simple-api-sms-callback/", NotifyAPIsManager().simple_api_sms_callback, name="simple_api_sms_callback"),
]