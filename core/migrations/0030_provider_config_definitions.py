import uuid

import django.db.models.deletion
from django.db import migrations, models


def create_definitions(apps, schema_editor):
    alias = schema_editor.connection.alias
    Definition = apps.get_model("core", "ProviderConfigDefinition")
    Entry = apps.get_model("core", "ProviderConfigEntry")

    definitions = {}
    entries = Entry.objects.using(alias).select_related("provider_config").order_by("date_created", "id")
    for entry in entries:
        definition_key = (entry.provider_config.provider_id, entry.key)
        definition = definitions.get(definition_key)
        if definition is None:
            definition, _ = Definition.objects.using(alias).get_or_create(
                provider_id=entry.provider_config.provider_id,
                key=entry.key,
                defaults={
                    "name": entry.key.capitalize().replace("_", " "),
                    "value_type": entry.value_type,
                    "description": "",
                },
            )
            definitions[definition_key] = definition
        entry.definition_id = definition.id
        entry.save(update_fields=["definition_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_replace_organization_with_remote_code"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProviderConfigDefinition",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ("date_created", models.DateTimeField(auto_now_add=True)),
                ("date_modified", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("key", models.CharField(max_length=255)),
                ("value_type", models.CharField(choices=[("string", "Text"), ("integer", "Integer"), ("float", "Decimal number"), ("boolean", "Boolean"), ("json", "JSON")], default="string", max_length=10)),
                ("description", models.TextField(blank=True)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="config_definitions", to="core.provider")),
            ],
            options={"ordering": ("name",)},
        ),
        migrations.AddField(
            model_name="providerconfigentry",
            name="definition",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="entries", to="core.providerconfigdefinition"),
        ),
        migrations.RunPython(create_definitions, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="providerconfigentry",
            name="unique_provider_config_entry_key",
        ),
        migrations.RemoveField(
            model_name="providerconfigentry",
            name="key",
        ),
        migrations.RemoveField(
            model_name="providerconfigentry",
            name="value_type",
        ),
        migrations.AlterField(
            model_name="providerconfigentry",
            name="definition",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entries", to="core.providerconfigdefinition"),
        ),
        migrations.AlterModelOptions(
            name="providerconfigentry",
            options={"ordering": ("definition__name",)},
        ),
        migrations.AddConstraint(
            model_name="providerconfigdefinition",
            constraint=models.UniqueConstraint(fields=("provider", "name"), name="unique_provider_definition_name"),
        ),
        migrations.AddConstraint(
            model_name="providerconfigdefinition",
            constraint=models.UniqueConstraint(fields=("provider", "key"), name="unique_provider_definition_key"),
        ),
        migrations.AddConstraint(
            model_name="providerconfigentry",
            constraint=models.UniqueConstraint(fields=("provider_config", "definition"), name="unique_provider_config_entry_definition"),
        ),
    ]
