from django.http import JsonResponse

from core.backend.services import SystemService


class APIKeyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        allowed_paths = [
            "/cia",
            "/healthz",
            "/favicon.ico",
            "/api/core/callbacks"
        ]
        if any(request.path.startswith(p) for p in allowed_paths):
            return self.get_response(request)

        api_key = request.headers.get("X-API-KEY")
        if not api_key:
            return JsonResponse({"error": "Missing API key"}, status=401)

        system = SystemService().get(api_key=api_key, is_active=True)
        if not system:
            return JsonResponse({"error": "Invalid API key"}, status=403)

        request.system = system

        return self.get_response(request)
