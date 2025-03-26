from django.http import JsonResponse
from django.utils.timezone import now

from core.backend.notification_manager import NotificationManager


def test(request):
    context = {
        "subject": "Your OTP Code",
        "heading": "Verify Your Login",
        "otp": "382196",
        "action": "log in to your account",
        "expiry_value": 10,
        "expiry_period": "hour",
        "system_name": "B2C",
        "current_year": now().year,
    }

    data = {
        "system": "B2C",
        "notification_type": "email",
        "recipient": "stevencallistus19@gmail.com",
        "template_name": "email_login_otp",
        "context": context
    }
    noti = NotificationManager().save_notification(data)
    NotificationManager().send_notification(noti)
    return JsonResponse({"name": "success"})