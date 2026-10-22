import logging
import json

from django.core.handlers.wsgi import WSGIRequest
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.backend.callback_manager import ProviderCallbackHandler
from core.backend.services import ProviderService
from core.backend.notification_manager import NotificationManager, ThreadNotFound
from core.models import (
    NotificationOverride,
    NotificationOverrideConfig,
    NotificationOverrideProvider,
    Provider,
    ProviderConfig,
    ProviderConfigDefinition,
    ProviderConfigEntry,
)
from core.tasks import process_existing_notification

logger = logging.getLogger(__name__)

class NotifyAPIsManager:
    @staticmethod
    @csrf_exempt
    def queue_send_notification(request: WSGIRequest) -> JsonResponse:
        """
        Queue a notification to be sent asynchronously.

        This view function handles HTTP POST requests to queue a notification for sending.
        It expects the request body to contain JSON data with the notification details.
        Resolve or create an optional email thread and save the notification before
        queueing delivery through Celery. Return the persisted notification ID.

        :param request: The HTTP request object.
        :type request: WSGIRequest
        :return: A JSON response indicating the result of the operation.
        :rtype: JsonResponse
        """
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")

            system = getattr(request, "system", None)
            if system is None:
                return JsonResponse(
                    {"code": "401.000.000", "message": "Missing authenticated system"},
                    status=401
                )
            data["system"] = str(system.id)

            manager = NotificationManager()
            with transaction.atomic():
                thread = manager.save_email_thread(data, system)
                notification = manager.save_notification(data, email_thread=thread, raise_errors=True)

            result = {
                "code": "100.000.000",
                "message": "Notification queued successfully",
                "notification_id": str(notification.id),
            }
            if notification.email_thread_id:
                result["email_thread_id"] = str(notification.email_thread_id)

            try:
                transaction.on_commit(lambda: process_existing_notification.delay(str(notification.id)))
            except Exception:
                logger.exception("Unable to queue notification %s", notification.id)
                result.update({
                    "code": "999.999.999",
                    "message": "Notification saved but queue unavailable; retry from admin or run reconciliation",
                })
                return JsonResponse(result, status=503)

            return JsonResponse(result)
        except ThreadNotFound as ex:
            return JsonResponse({"message": str(ex)}, status=404)
        except (ValueError, KeyError, ValidationError) as ex:
            return JsonResponse({"message": str(ex)}, status=400)
        except Exception as ex:
            logger.exception("NotifyAPIsManager - queue_send_notification exception: %s" % ex)
            return JsonResponse(
                {"code": "999.999.999", "message": "Send notification failed with an exception"},
                status=500
            )

    @csrf_exempt
    def provider_callback(self, request, provider_slug: str):
        """
        Receive a provider callback, persist it, and queue asynchronous processing.
        """
        try:
            provider = ProviderService().get(slug=provider_slug, is_active=True, sends_callbacks=True)
            if provider is None:
                return JsonResponse({"message": "Provider not found"}, status=404)

            data = json.loads(request.body)
            headers = {key: value for key, value in request.headers.items()}
            callback = ProviderCallbackHandler.receive(provider=provider, data=data, headers=headers)

            return JsonResponse({"message": "Callback received", "callback_id": str(callback.id)})
        except Exception as ex:
            logger.exception("NotifyAPIsManager - provider_callback exception: %s" % ex)
            return JsonResponse({"message": "Internal server error"}, status=500)


def _definition_data(definition):
    return {
        "id": str(definition.id),
        "name": definition.name,
        "key": definition.key,
        "value_type": definition.value_type,
        "description": definition.description,
    }


def _provider_data(provider, *, include_definitions=False):
    data = {
        "id": str(provider.id),
        "name": provider.name,
        "slug": provider.slug,
        "description": provider.description,
        "notification_type": provider.notification_type.name,
        "is_active": provider.is_active,
    }
    if include_definitions:
        data["config_definitions"] = [
            _definition_data(definition)
            for definition in provider.config_definitions.all().order_by("name")
        ]
    return data


def _config_data(config):
    return {
        "id": str(config.id),
        "name": config.name,
        "description": config.description,
        "provider": _provider_data(config.provider),
        "organization_remote_codes": config.organization_remote_codes,
        "priority": config.priority,
        "is_active": config.is_active,
        "configs": config.config,
    }


def _override_data(override):
    routes = []
    for route in override.providers.select_related("provider").prefetch_related(
        "configs__provider_config",
    ).order_by("priority", "provider__name"):
        routes.append({
            "id": str(route.id),
            "provider": _provider_data(route.provider),
            "priority": route.priority,
            "use_other_configs": route.use_other_configs,
            "provider_configs": [
                {
                    "id": str(link.provider_config_id),
                    "name": link.provider_config.name,
                    "priority": link.priority,
                }
                for link in route.configs.all().order_by("priority")
            ],
        })
    return {
        "id": str(override.id),
        "organization_remote_code": override.organization_remote_code,
        "notification_type": override.notification_type.name,
        "use_other_providers": override.use_other_providers,
        "providers": routes,
    }


def _request_json(request):
    try:
        data = json.loads(request.body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        raise ValueError("Malformed JSON") from ex
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return data


def _serialize_config_value(definition, value):
    value_type = definition.value_type
    if value_type == ProviderConfigDefinition.ValueType.STRING:
        if not isinstance(value, str):
            raise ValueError(f"Config '{definition.key}' must be a string")
        return value
    if value_type == ProviderConfigDefinition.ValueType.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Config '{definition.key}' must be an integer")
        return str(value)
    if value_type == ProviderConfigDefinition.ValueType.FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Config '{definition.key}' must be a number")
        return str(value)
    if value_type == ProviderConfigDefinition.ValueType.BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError(f"Config '{definition.key}' must be a boolean")
        return "true" if value else "false"
    try:
        return json.dumps(value)
    except (TypeError, ValueError) as ex:
        raise ValueError(f"Config '{definition.key}' must be valid JSON") from ex


@csrf_exempt
@require_GET
def provider_list(request):
    providers = Provider.objects.filter(is_active=True).select_related("notification_type").order_by(
        "notification_type__name", "priority", "name",
    )
    notification_type = request.GET.get("notification_type", "").strip().lower()
    if notification_type:
        providers = providers.filter(notification_type__name=notification_type)
    return JsonResponse({"providers": [_provider_data(provider) for provider in providers]})


@csrf_exempt
@require_GET
def provider_detail(request, provider_id):
    provider = Provider.objects.filter(id=provider_id, is_active=True).select_related(
        "notification_type",
    ).prefetch_related("config_definitions").first()
    if provider is None:
        return JsonResponse({"message": "Provider not found"}, status=404)
    return JsonResponse({"provider": _provider_data(provider, include_definitions=True)})


@csrf_exempt
@require_GET
def organization_override_list(request):
    remote_code = request.GET.get("organization_remote_code", "").strip()
    if not remote_code:
        return JsonResponse({"message": "organization_remote_code is required"}, status=400)
    overrides = NotificationOverride.objects.filter(
        system=request.system,
        organization_remote_code=remote_code,
    ).select_related("notification_type").order_by("notification_type__name")
    return JsonResponse({"overrides": [_override_data(override) for override in overrides]})


@csrf_exempt
@require_GET
def organization_provider_config_list(request):
    remote_code = request.GET.get("organization_remote_code", "").strip()
    if not remote_code:
        return JsonResponse({"message": "organization_remote_code is required"}, status=400)
    configs = ProviderConfig.objects.filter(is_active=True).select_related(
        "provider", "provider__notification_type",
    ).prefetch_related("entries__definition", "provider__systems").order_by(
        "provider__name", "priority", "name",
    )
    configs = [
        config for config in configs
        if remote_code in (config.organization_remote_codes or [])
        and config.provider.is_available_for(request.system.id, remote_code)
    ]
    return JsonResponse({"provider_configs": [_config_data(config) for config in configs]})


@csrf_exempt
@require_GET
def organization_provider_config_detail(request, config_id):
    remote_code = request.GET.get("organization_remote_code", "").strip()
    if not remote_code:
        return JsonResponse({"message": "organization_remote_code is required"}, status=400)
    config = ProviderConfig.objects.filter(id=config_id).select_related(
        "provider", "provider__notification_type",
    ).prefetch_related("entries__definition", "provider__systems").first()
    if (
        config is None
        or remote_code not in (config.organization_remote_codes or [])
        or not config.provider.is_available_for(request.system.id, remote_code)
    ):
        return JsonResponse({"message": "Provider config not found"}, status=404)
    return JsonResponse({"provider_config": _config_data(config)})


@csrf_exempt
@require_POST
def organization_override_create(request):
    try:
        data = _request_json(request)
        remote_code = data.get("organization_remote_code")
        provider_id = data.get("provider_id")
        supplied_configs = data.get("configs")
        if not isinstance(remote_code, str) or not remote_code.strip():
            raise ValueError("organization_remote_code is required")
        remote_code = remote_code.strip()
        if not provider_id:
            raise ValueError("provider_id is required")
        if not isinstance(supplied_configs, dict):
            raise ValueError("configs must be a JSON object")

        provider = Provider.objects.filter(id=provider_id, is_active=True).select_related(
            "notification_type",
        ).prefetch_related("config_definitions", "systems").first()
        if provider is None:
            return JsonResponse({"message": "Provider not found"}, status=404)
        if not provider.is_available_for(request.system.id, remote_code):
            return JsonResponse({"message": "Provider is not available for this organization"}, status=400)

        definitions = {item.key: item for item in provider.config_definitions.all()}
        missing = sorted(set(definitions) - set(supplied_configs))
        unknown = sorted(set(supplied_configs) - set(definitions))
        if missing or unknown:
            return JsonResponse({
                "message": "Configs must match the provider definitions",
                "errors": {"missing": missing, "unknown": unknown},
            }, status=400)
        serialized = {
            key: _serialize_config_value(definition, supplied_configs[key])
            for key, definition in definitions.items()
        }

        existing = [
            config for config in provider.configs.all()
            if remote_code in (config.organization_remote_codes or [])
        ]
        if existing:
            return JsonResponse({
                "message": "This organization already has a config for the provider",
                "provider_config_id": str(existing[0].id),
            }, status=409)

        existing_override = NotificationOverride.objects.filter(
            system=request.system,
            organization_remote_code=remote_code,
            notification_type=provider.notification_type,
        ).first()
        if existing_override and existing_override.providers.filter(provider=provider).exists():
            return JsonResponse({"message": "Provider is already configured in this override"}, status=409)
        base_name = f"{remote_code} {provider.name}"[:100]
        if ProviderConfig.objects.filter(provider=provider, name=base_name).exists():
            return JsonResponse({"message": "A provider config with the generated name already exists"}, status=409)

        with transaction.atomic():
            override, _ = NotificationOverride.objects.get_or_create(
                system=request.system,
                organization_remote_code=remote_code,
                notification_type=provider.notification_type,
            )
            config_priority = (provider.configs.aggregate(value=Max("priority"))["value"] or 0) + 1
            config = ProviderConfig.objects.create(
                provider=provider,
                name=base_name,
                description=f"Configuration for organization {remote_code}",
                organization_remote_codes=[remote_code],
                priority=config_priority,
            )
            ProviderConfigEntry.objects.bulk_create([
                ProviderConfigEntry(
                    provider_config=config,
                    definition=definition,
                    value=serialized[key],
                )
                for key, definition in definitions.items()
            ])
            route_priority = (override.providers.aggregate(value=Max("priority"))["value"] or 0) + 1
            route = NotificationOverrideProvider.objects.create(
                override=override,
                provider=provider,
                priority=route_priority,
            )
            NotificationOverrideConfig.objects.create(
                override_provider=route,
                provider_config=config,
                priority=0,
            )
        config = ProviderConfig.objects.select_related(
            "provider", "provider__notification_type",
        ).prefetch_related("entries__definition").get(id=config.id)
        return JsonResponse({
            "message": "Organization override created",
            "override": _override_data(override),
            "provider_config": _config_data(config),
        }, status=201)
    except (ValueError, ValidationError) as ex:
        return JsonResponse({"message": str(ex)}, status=400)
    except IntegrityError:
        return JsonResponse({"message": "The organization override already exists"}, status=409)
