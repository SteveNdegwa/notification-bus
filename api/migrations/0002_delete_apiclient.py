# Generated manually after moving API keys to core.System.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_system_api_key_provider_callbacks_and_tracking'),
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.DeleteModel(
            name='APIClient',
        ),
    ]
