# Generated manually for system API keys and callback tracking.

import secrets
import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def populate_system_api_keys(apps, schema_editor):
    System = apps.get_model('core', 'System')
    APIClient = apps.get_model('api', 'APIClient')

    for system in System.objects.all():
        api_client = APIClient.objects.filter(system=system, is_active=True).order_by('-date_created').first()
        system.api_key = api_client.api_key if api_client else secrets.token_urlsafe(40)
        system.save(update_fields=['api_key'])


def populate_provider_slugs(apps, schema_editor):
    Provider = apps.get_model('core', 'Provider')
    used_slugs = set()

    for provider in Provider.objects.all().order_by('date_created'):
        base_slug = slugify(provider.name) or slugify(provider.class_name) or "provider"
        slug = base_slug
        suffix = 2
        while slug in used_slugs or Provider.objects.filter(slug=slug).exclude(id=provider.id).exists():
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        provider.slug = slug
        provider.save(update_fields=['slug'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
        ('core', '0017_delete_state_remove_system_default_from_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='system',
            name='api_key',
            field=models.CharField(blank=True, editable=False, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='system',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(populate_system_api_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='system',
            name='api_key',
            field=models.CharField(blank=True, editable=False, max_length=255, unique=True),
        ),
        migrations.RemoveField(
            model_name='system',
            name='callback_type',
        ),
        migrations.RemoveField(
            model_name='system',
            name='queue_name',
        ),
        migrations.RemoveField(
            model_name='system',
            name='webhook_api_key',
        ),
        migrations.AddField(
            model_name='provider',
            name='slug',
            field=models.SlugField(blank=True, max_length=100, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='provider',
            name='callback_verification_config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='provider',
            name='sends_callbacks',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='provider',
            name='verify_callback_signature',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(populate_provider_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='provider',
            name='slug',
            field=models.SlugField(max_length=100, unique=True),
        ),
        migrations.AddField(
            model_name='notification',
            name='user_ids',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='notification',
            name='recipient_resolution_status',
            field=models.CharField(
                choices=[
                    ('Not Required', 'Not Required'),
                    ('Pending', 'Pending'),
                    ('Processing', 'Processing'),
                    ('Resolved', 'Resolved'),
                    ('Failed', 'Failed'),
                ],
                default='Not Required',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='recipient_resolution_error',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='notification',
            name='status',
            field=models.CharField(
                choices=[
                    ('Pending', 'Pending'),
                    ('Getting Recipients', 'Getting Recipients'),
                    ('Sent', 'Sent'),
                    ('Failed', 'Failed'),
                    ('Confirmation Pending', 'Confirmation Pending'),
                ],
                default='Pending',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='ProviderCallback',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(
                    choices=[
                        ('Received', 'Received'),
                        ('Processing', 'Processing'),
                        ('Processed', 'Processed'),
                        ('Failed', 'Failed'),
                    ],
                    default='Received',
                    max_length=20,
                )),
                ('data', models.JSONField(default=dict)),
                ('headers', models.JSONField(default=dict)),
                ('state', models.JSONField(blank=True, default=dict)),
                ('error', models.TextField(blank=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.provider')),
            ],
            options={
                'ordering': ('-date_created',),
            },
        ),
        migrations.CreateModel(
            name='SystemCallback',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('payload', models.JSONField(default=dict)),
                ('status', models.CharField(
                    choices=[
                        ('Pending', 'Pending'),
                        ('Sending', 'Sending'),
                        ('Sent', 'Sent'),
                        ('Failed', 'Failed'),
                    ],
                    default='Pending',
                    max_length=20,
                )),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('next_retry_at', models.DateTimeField(blank=True, null=True)),
                ('response_status_code', models.PositiveIntegerField(blank=True, null=True)),
                ('response_body', models.TextField(blank=True)),
                ('error', models.TextField(blank=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('notification', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='core.notification',
                )),
                ('system', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.system')),
            ],
            options={
                'ordering': ('-date_created',),
            },
        ),
    ]
