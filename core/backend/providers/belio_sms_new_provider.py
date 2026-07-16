import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

from core.backend.providers.base_provider import BaseProvider
from core.models import Notification, System

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_BUFFER_SECONDS = 60
DEFAULT_TOKEN_EXPIRY_SECONDS = 3600
REQUEST_TIMEOUT_SECONDS = 30


class BelioSmsNewProvider(BaseProvider):
    def __init__(self, provider_config: dict):
        super().__init__(provider_config)
        self._bearer_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._token_lock = threading.Lock()
        self.auth_url = ""
        self.api_base_url = ""

    def validate_config(self) -> bool:
        required_keys = ["client_id", "client_secret", "sms_service_id", "auth_url", "api_base_url"]
        missing_keys = [key for key in required_keys if key not in self.config]

        if missing_keys:
            self.set_last_exception("Missing config keys: %s" % ", ".join(missing_keys))
            logger.error("BelioSmsNewProvider - %s", self.last_exception)
            return False

        self.auth_url = self.config["auth_url"]
        self.api_base_url = self.config["api_base_url"]

        return True

    def _has_valid_token(self) -> bool:
        return bool(self._bearer_token and self._token_expires_at > time.time() + TOKEN_EXPIRY_BUFFER_SECONDS)

    def _get_bearer_token(self) -> str:
        with self._token_lock:
            if self._has_valid_token():
                return self._bearer_token

            try:
                payload = {
                    "client_id": self.config["client_id"],
                    "client_secret": self.config["client_secret"],
                    "grant_type": "client_credentials",
                }
                headers = {"Content-Type": "application/x-www-form-urlencoded"}

                response = requests.post(
                    self.auth_url,
                    data=payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()

                token_data = response.json()
                self._bearer_token = token_data["access_token"]
                expires_in = token_data.get("expires_in", DEFAULT_TOKEN_EXPIRY_SECONDS)
                self._token_expires_at = time.time() + expires_in

                return self._bearer_token

            except requests.exceptions.RequestException as e:
                logger.error("BelioSmsNewProvider - Token generation failed: %s", e)
                raise Exception(f"Failed to generate bearer token: {e}")
            except KeyError as e:
                logger.error("BelioSmsNewProvider - Invalid token response format: %s", e)
                raise Exception(f"Invalid token response format: {e}")
    
    def send(self, recipients: List[str], content: Dict[str, str]) -> str:
        try:
            if not self.validate_config():
                return Notification.Status.FAILED

            message = content.get("body", "")
            unique_identifier = content.get("unique_identifier", "")

            if not message:
                self.set_last_exception("Empty message body")
                logger.error("BelioSmsNewProvider - %s", self.last_exception)
                return Notification.Status.FAILED

            if not recipients:
                self.set_last_exception("No recipients provided")
                logger.error("BelioSmsNewProvider - %s", self.last_exception)
                return Notification.Status.FAILED

            bearer_token = self._get_bearer_token()
            send_url = f"{self.api_base_url}/message/{self.config['sms_service_id']}"
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            }

            messages = [{"text": message, "phone": phone} for phone in recipients]
            payload = {
                "type": "SendToEach",
                "messages": messages,
            }
            callback_url = self.config.get("callback_url")
            if callback_url:
                receipt_request = {
                    "correlator": unique_identifier,
                    "callbackUrl": callback_url,
                }
                payload["receiptRequest"] = receipt_request

            response = requests.post(send_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()

            return Notification.Status.SENT

        except requests.exceptions.RequestException as e:
            logger.error("BelioSmsNewProvider - SMS sending failed: %s", e)
            self.set_last_exception(e)
            return Notification.Status.FAILED
        except Exception as e:
            logger.exception("BelioSmsNewProvider - Unexpected error: %s", e)
            self.set_last_exception(e)
            return Notification.Status.FAILED

    def clear_token(self) -> None:
        with self._token_lock:
            self._bearer_token = None
            self._token_expires_at = 0

    def handle_callback(self, data: Dict, headers: Dict) -> Dict:
        delivery_status = data.get("deliveryStatus", "")
        notification_id = data.get("correlator", "")
        sent_time = data.get("timestamp")
        status = Notification.Status.SENT if delivery_status in ["DeliveredToTerminal", "Delivered"] else Notification.Status.FAILED

        return {
            "notification_id": notification_id,
            "status": status,
            "sent_time": sent_time if status == Notification.Status.SENT else None,
            "state": {
                "delivery_status": delivery_status,
            }
        }

    def check_bundles(self) -> None:
        try:
            if not self.validate_config():
                return

            bearer_token = self._get_bearer_token()
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            }
            url = f"{self.api_base_url}/bundle/SMS"
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            response_data = response.json()

            results = response_data.get('result', [])
            if not results:
                return

            low_balance_items = []
            near_expiry_items = []

            low_threshold = 50
            expiry_alert_days = 7
            today = datetime.now().date()

            has_issue = False

            for bundle in results:
                band = bundle['band']
                balances = bundle.get('balances', [])

                for bal in balances:
                    channel = bal['channel']
                    allotted = bal['allotted']
                    balance = bal['balance']
                    expiry_str = bal['expiry'][:10]
                    try:
                        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    days_left = (expiry_date - today).days

                    # Low balance check
                    if balance < 0.7 * allotted and balance <= low_threshold and balance % 5 == 0:
                        has_issue = True
                        low_balance_items.append({
                            "band": band,
                            "channel": channel,
                            "balance": balance,
                            "allotted": allotted,
                        })

                    # Near expiry check
                    if 0 < days_left <= expiry_alert_days:
                        has_issue = True
                        near_expiry_items.append({
                            "band": band,
                            "channel": channel,
                            "days_left": days_left,
                            "expiry_date": expiry_date.strftime("%d %B %Y"),
                        })

            # Only send email if there's at least one issue across any bundle
            if not has_issue:
                return

            context = {
                "first_name": "Admins",
                "provider": "Belio SMS",
                "low_balance_items": low_balance_items,
                "near_expiry_items": near_expiry_items,
            }

            system = System.objects.get(is_internal=True)
            notification_data = {
                "system": str(system.id),
                "notification_type": "email",
                "recipients": ["it@spinmobile.co.ke", "business@spinmobile.co.ke"],
                "template": "email_smsbundle_low_balance",
                "context": context,
                "unique_identifier": f"sms_bundle_multi_alert_belio_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            }

            from core.tasks import send_notification
            send_notification.delay(notification_data)
        except Exception as ex:
            logger.exception("BelioSmsNewProvider - check_bundles exception: %s", ex)
