# Generated manually for system-scoped templates and notification send exceptions.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_remove_organisation'),
    ]

    operations = [
        migrations.AddField(
            model_name='template',
            name='system',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to='core.system',
            ),
        ),
        migrations.AlterField(
            model_name='template',
            name='name',
            field=models.CharField(max_length=100),
        ),
        migrations.AddConstraint(
            model_name='template',
            constraint=models.UniqueConstraint(
                condition=models.Q(system__isnull=False),
                fields=('system', 'name'),
                name='unique_template_name_per_system',
            ),
        ),
        migrations.AddConstraint(
            model_name='template',
            constraint=models.UniqueConstraint(
                condition=models.Q(system__isnull=True),
                fields=('name',),
                name='unique_global_template_name',
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='send_exception',
            field=models.TextField(blank=True),
        ),
    ]
