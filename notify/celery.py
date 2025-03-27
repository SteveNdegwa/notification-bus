import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'notify.settings')

""" declare celery app """
app = Celery("notify")
app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.task_default_queue = "notification_queue"
app.autodiscover_tasks()