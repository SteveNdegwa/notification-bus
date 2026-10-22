# Generated manually for identifying the notification service's internal system.

from django.db import migrations, models


def mark_existing_notify_system_internal(apps, schema_editor):
    System = apps.get_model('core', 'System')
    System.objects.filter(name='notify').update(is_internal=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_template_system_scope_notification_send_exception'),
    ]

    operations = [
        migrations.AddField(
            model_name='system',
            name='is_internal',
            field=models.BooleanField(
                default=False,
                help_text='Marks the system record used by this notification service for internal notifications.',
            ),
        ),
        migrations.RunPython(mark_existing_notify_system_internal, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='system',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_internal=True),
                fields=('is_internal',),
                name='unique_internal_system',
            ),
        ),
    ]
