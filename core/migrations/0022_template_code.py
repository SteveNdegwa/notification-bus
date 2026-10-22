from django.db import migrations, models


def set_template_codes_and_names(apps, schema_editor):
    Template = apps.get_model("core", "Template")
    for template in Template.objects.all().iterator():
        existing_name = template.name
        template.code = existing_name
        template.name = existing_name.replace("_", " ").capitalize()
        template.save(update_fields=["code", "name"])


def unset_template_codes_and_names(apps, schema_editor):
    Template = apps.get_model("core", "Template")
    for template in Template.objects.all().iterator():
        template.name = template.code
        template.save(update_fields=["name"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_system_is_internal"),
    ]

    operations = [
        migrations.AddField(
            model_name="template",
            name="code",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.RemoveConstraint(
            model_name="template",
            name="unique_template_name_per_system",
        ),
        migrations.RemoveConstraint(
            model_name="template",
            name="unique_global_template_name",
        ),
        migrations.RunPython(set_template_codes_and_names, unset_template_codes_and_names),
        migrations.AlterField(
            model_name="template",
            name="code",
            field=models.CharField(max_length=100),
        ),
        migrations.AddConstraint(
            model_name="template",
            constraint=models.UniqueConstraint(
                condition=models.Q(system__isnull=False),
                fields=("system", "code"),
                name="unique_template_code_per_system",
            ),
        ),
        migrations.AddConstraint(
            model_name="template",
            constraint=models.UniqueConstraint(
                condition=models.Q(system__isnull=True),
                fields=("code",),
                name="unique_global_template_code",
            ),
        ),
    ]
