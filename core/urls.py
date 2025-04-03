from django.urls import path
from .views import NotifyAPIsManager

urlpatterns = [
    path("send-notification/", NotifyAPIsManager().queue_send_notification, name="send_notification"),
    path("response/", NotifyAPIsManager().sms_response, name="sms_response"),
]