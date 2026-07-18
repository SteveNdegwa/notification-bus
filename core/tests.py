import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import RequestFactory, SimpleTestCase

from core.backend.providers.onfon_sms_provider import OnfonSMSProvider
from core.backend.providers.providers_registry import PROVIDER_CLASSES
from core.models import Notification
from core.views import NotifyAPIsManager


class OnfonSMSProviderTests(SimpleTestCase):
    def setUp(self):
        self.config = {
            "api_key": "api-key",
            "client_id": "client-id",
            "access_key": "access-key",
            "base_url": "https://api.example.com/",
            "sender_id": "Spin",
        }

    def test_provider_is_registered(self):
        self.assertIs(PROVIDER_CLASSES["OnfonSMSProvider"], OnfonSMSProvider)

    def test_validate_config_requires_expected_keys(self):
        provider = OnfonSMSProvider({key: value for key, value in self.config.items() if key != "api_key"})

        self.assertFalse(provider.validate_config())
        self.assertEqual(provider.last_exception, "Missing config keys: api_key")

    @patch("core.backend.providers.onfon_sms_provider.requests.post")
    def test_send_posts_bulk_sms_payload(self, mock_post):
        response = Mock()
        response.json.return_value = {"ErrorCode": 0}
        mock_post.return_value = response
        provider = OnfonSMSProvider(self.config)

        status = provider.send(["254700000001", "254700000002"], {"body": "Hello"})

        self.assertEqual(status, Notification.Status.SENT)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.example.com/sms/SendBulkSMS")
        self.assertEqual(kwargs["headers"]["AccessKey"], "access-key")
        self.assertEqual(kwargs["json"]["SenderId"], "Spin")
        self.assertEqual(kwargs["json"]["ApiKey"], "api-key")
        self.assertEqual(kwargs["json"]["ClientId"], "client-id")
        self.assertEqual(
            kwargs["json"]["MessageParameters"],
            [
                {"Number": "254700000001", "Text": "Hello"},
                {"Number": "254700000002", "Text": "Hello"},
            ],
        )

    @patch("core.backend.providers.onfon_sms_provider.requests.post")
    def test_send_returns_failed_for_api_error(self, mock_post):
        response = Mock()
        response.json.return_value = {"ErrorCode": 101, "ErrorMessage": "Invalid sender"}
        mock_post.return_value = response
        provider = OnfonSMSProvider(self.config)

        status = provider.send(["254700000001"], {"body": "Hello"})

        self.assertEqual(status, Notification.Status.FAILED)
        self.assertEqual(provider.last_exception, "API error: Invalid sender")


class SendNotificationEndpointTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.system_id = uuid4()

    @patch("core.views.process_existing_notification")
    @patch("core.views.NotificationManager")
    def test_send_notification_saves_then_queues_existing_notification(self, mock_manager_class, mock_task):
        notification_id = uuid4()
        mock_manager = mock_manager_class.return_value
        mock_manager.save_notification.return_value = SimpleNamespace(id=notification_id)
        request = self.factory.post(
            "/send-notification/",
            data=json.dumps({
                "notification_type": "sms",
                "context": {"body": "Hello"},
                "recipients": ["+254700000001"],
            }),
            content_type="application/json",
        )
        request.system = SimpleNamespace(id=self.system_id)

        response = NotifyAPIsManager.queue_send_notification(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["notification_id"], str(notification_id))
        mock_manager.save_notification.assert_called_once()
        saved_data = mock_manager.save_notification.call_args.args[0]
        self.assertEqual(saved_data["system"], str(self.system_id))
        self.assertTrue(mock_manager.save_notification.call_args.kwargs["raise_exception"])
        mock_task.delay.assert_called_once_with(str(notification_id))

    @patch("core.views.process_existing_notification")
    @patch("core.views.NotificationManager")
    def test_send_notification_returns_validation_error_message(self, mock_manager_class, mock_task):
        mock_manager_class.return_value.save_notification.side_effect = ValueError("Invalid notification type")
        request = self.factory.post(
            "/send-notification/",
            data=json.dumps({"notification_type": "unknown", "context": {}}),
            content_type="application/json",
        )
        request.system = SimpleNamespace(id=self.system_id)

        response = NotifyAPIsManager.queue_send_notification(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["message"], "Invalid notification type")
        mock_task.delay.assert_not_called()

    def test_send_notification_returns_bad_request_for_invalid_json(self):
        request = self.factory.post(
            "/send-notification/",
            data="{",
            content_type="application/json",
        )
        request.system = SimpleNamespace(id=self.system_id)

        response = NotifyAPIsManager.queue_send_notification(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["message"], "Invalid JSON payload")
