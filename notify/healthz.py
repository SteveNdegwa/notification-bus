import os

from django.db import connection
from django.http import JsonResponse


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/healthz':
            with connection.cursor() as cursor:
                cursor.execute("select 1")
                one = cursor.fetchone()[0]
            return JsonResponse({
                "success": True,
                "db": one,
                "app_name": os.environ.get('APP_NAME'),
                "env": os.environ.get('ENV'),
            })
        return self.get_response(request)
