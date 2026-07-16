from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.backend.providers.onfon_sms_provider import OnfonSMSProvider
from core.backend.providers.providers_registry import PROVIDER_CLASSES
from core.models import Notification


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
