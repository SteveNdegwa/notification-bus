from django.db import migrations, models


def copy_organization_remote_codes(apps, schema_editor):
    alias = schema_editor.connection.alias
    Organization = apps.get_model("core", "Organization")
    Notification = apps.get_model("core", "Notification")
    NotificationOverride = apps.get_model("core", "NotificationOverride")
    Provider = apps.get_model("core", "Provider")
    ProviderConfig = apps.get_model("core", "ProviderConfig")
    Template = apps.get_model("core", "Template")

    organizations = {
        organization.id: organization
        for organization in Organization.objects.using(alias).all()
    }

    for provider in Provider.objects.using(alias).prefetch_related("organizations"):
        provider.organization_remote_codes = sorted({
            organization.identifier
            for organization in provider.organizations.all()
        })
        provider.save(update_fields=["organization_remote_codes"])

    for config in ProviderConfig.objects.using(alias).prefetch_related("organizations"):
        config.organization_remote_codes = sorted({
            organization.identifier
            for organization in config.organizations.all()
        })
        config.save(update_fields=["organization_remote_codes"])

    for model in (Notification, Template):
        for item in model.objects.using(alias).exclude(organization_id=None):
            item.organization_remote_code = organizations[item.organization_id].identifier
            item.save(update_fields=["organization_remote_code"])

    for override in NotificationOverride.objects.using(alias).exclude(organization_id=None):
        organization = organizations[override.organization_id]
        override.system_id = organization.system_id
        override.organization_remote_code = organization.identifier
        override.save(update_fields=["system_id", "organization_remote_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_provider_failure_alerts"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="organization_remote_codes",
            field=models.JSONField(blank=True, default=list, help_text="Optional organization remote-code allow-list. Leave both scope fields empty to make this provider available globally. A system scope applies to every organization in that system."),
        ),
        migrations.AddField(
            model_name="providerconfig",
            name="organization_remote_codes",
            field=models.JSONField(blank=True, default=list, help_text="Optional organization remote-code allow-list. When config scopes are set, the notification system or organization remote code must match one of them in addition to satisfying the provider's scope."),
        ),
        migrations.AddField(
            model_name="notificationoverride",
            name="organization_remote_code",
            field=models.CharField(blank=True, help_text="Optional organization remote code. Organization rules take precedence over system rules.", max_length=255),
        ),
        migrations.AddField(
            model_name="notification",
            name="organization_remote_code",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="template",
            name="organization_remote_code",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(copy_organization_remote_codes, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="notificationoverride",
            name="override_has_system_or_organization",
        ),
        migrations.RemoveConstraint(
            model_name="notificationoverride",
            name="unique_system_notification_override",
        ),
        migrations.RemoveConstraint(
            model_name="notificationoverride",
            name="unique_organization_notification_override",
        ),
        migrations.RemoveConstraint(
            model_name="template",
            name="unique_template_code_per_system",
        ),
        migrations.RemoveConstraint(
            model_name="template",
            name="unique_template_code_per_organization",
        ),
        migrations.RemoveConstraint(
            model_name="template",
            name="unique_global_template_code",
        ),
        migrations.RemoveField(
            model_name="provider",
            name="organizations",
        ),
        migrations.RemoveField(
            model_name="providerconfig",
            name="organizations",
        ),
        migrations.RemoveField(
            model_name="notificationoverride",
            name="organization",
        ),
        migrations.RemoveField(
            model_name="notification",
            name="organization",
        ),
        migrations.RemoveField(
            model_name="template",
            name="organization",
        ),
        migrations.DeleteModel(
            name="Organization",
        ),
        migrations.AlterField(
            model_name="notificationoverride",
            name="system",
            field=models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="notification_overrides", to="core.system"),
        ),
        migrations.AlterModelOptions(
            name="notificationoverride",
            options={"ordering": ("system__name", "organization_remote_code", "notification_type__name")},
        ),
        migrations.AddConstraint(
            model_name="notificationoverride",
            constraint=models.UniqueConstraint(condition=models.Q(("organization_remote_code", "")), fields=("system", "notification_type"), name="unique_system_notification_override"),
        ),
        migrations.AddConstraint(
            model_name="notificationoverride",
            constraint=models.UniqueConstraint(condition=models.Q(("organization_remote_code", ""), _negated=True), fields=("system", "organization_remote_code", "notification_type"), name="unique_organization_notification_override"),
        ),
        migrations.AddConstraint(
            model_name="template",
            constraint=models.UniqueConstraint(condition=models.Q(("organization_remote_code", ""), ("system__isnull", False)), fields=("system", "code"), name="unique_template_code_per_system"),
        ),
        migrations.AddConstraint(
            model_name="template",
            constraint=models.UniqueConstraint(condition=models.Q(("organization_remote_code", ""), _negated=True), fields=("system", "organization_remote_code", "code"), name="unique_template_code_per_organization"),
        ),
        migrations.AddConstraint(
            model_name="template",
            constraint=models.UniqueConstraint(condition=models.Q(("organization_remote_code", ""), ("system__isnull", True)), fields=("code",), name="unique_global_template_code"),
        ),
        migrations.AddConstraint(
            model_name="template",
            constraint=models.CheckConstraint(condition=models.Q(("organization_remote_code", ""), ("system__isnull", False), _connector="OR"), name="organization_template_has_system"),
        ),
    ]
