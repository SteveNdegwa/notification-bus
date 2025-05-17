from core.backend.providers.africas_talking_sms_provider import AfricasTalkingSMSProvider
from core.backend.providers.firebase_push_provider import FirebasePushProvider
from core.backend.providers.gmail_smtp_server import GmailSMTPServer
from core.backend.providers.belio_sms_provider import BelioSMSProvider

PROVIDER_CLASSES = {
    "GmailSMTPServer": GmailSMTPServer,
    "FirebasePushProvider": FirebasePushProvider,
    "AfricasTalkingSMSProvider": AfricasTalkingSMSProvider,
    "BelioSMSProvider": BelioSMSProvider
}