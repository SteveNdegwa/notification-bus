from django.test import Client, TestCase

from core.models import (
    NotificationOverride,
    NotificationType,
    Provider,
    ProviderConfig,
    ProviderConfigDefinition,
    System,
)


class OrganizationRoutingAPITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.system = System.objects.create(name="IAMS", description="")
        self.notification_type = NotificationType.objects.create(name="sms", description="")
        self.provider = Provider.objects.create(
            name="SMS Gateway",
            description="",
            notification_type=self.notification_type,
            slug="sms-gateway",
            class_name="SMSProvider",
        )
        ProviderConfigDefinition.objects.create(
            provider=self.provider,
            name="API key",
            key="api_key",
            value_type=ProviderConfigDefinition.ValueType.STRING,
        )
        ProviderConfigDefinition.objects.create(
            provider=self.provider,
            name="Retries",
            key="retries",
            value_type=ProviderConfigDefinition.ValueType.INTEGER,
        )
        self.headers = {"HTTP_X_API_KEY": self.system.api_key}

    def test_provider_detail_returns_config_definitions(self):
        response = self.client.get(
            f"/api/core/providers/{self.provider.id}/",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        definitions = response.json()["provider"]["config_definitions"]
        self.assertEqual({item["key"] for item in definitions}, {"api_key", "retries"})

    def test_create_override_validates_and_creates_scoped_config(self):
        response = self.client.post(
            "/api/core/organization-overrides/create/",
            data={
                "organization_remote_code": "agency-1",
                "provider_id": str(self.provider.id),
                "configs": {"api_key": "secret", "retries": 3},
            },
            content_type="application/json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 201, response.content)
        config = ProviderConfig.objects.get(provider=self.provider)
        self.assertEqual(config.organization_remote_codes, ["agency-1"])
        self.assertEqual(config.config, {"api_key": "secret", "retries": 3})
        self.assertTrue(NotificationOverride.objects.filter(
            system=self.system,
            organization_remote_code="agency-1",
            notification_type=self.notification_type,
        ).exists())

        list_response = self.client.get(
            "/api/core/organization-provider-configs/",
            {"organization_remote_code": "agency-1"},
            **self.headers,
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["provider_configs"][0]["id"], str(config.id))

    def test_create_override_rejects_missing_and_wrong_typed_configs(self):
        response = self.client.post(
            "/api/core/organization-overrides/create/",
            data={
                "organization_remote_code": "agency-1",
                "provider_id": str(self.provider.id),
                "configs": {"api_key": "secret"},
            },
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"]["missing"], ["retries"])

        response = self.client.post(
            "/api/core/organization-overrides/create/",
            data={
                "organization_remote_code": "agency-1",
                "provider_id": str(self.provider.id),
                "configs": {"api_key": "secret", "retries": "three"},
            },
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ProviderConfig.objects.exists())
