# Generated manually after removing organisations from notifications.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_system_api_key_provider_callbacks_and_tracking'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='notification',
            name='organisation',
        ),
        migrations.DeleteModel(
            name='Organisation',
        ),
    ]
