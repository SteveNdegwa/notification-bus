from abc import ABC, abstractmethod
from typing import Dict, List


class BaseProvider(ABC):
    """
    Abstract base class for all notification providers (e.g., SMTP, Twilio, Firebase)
    """

    def __init__(self, provider_config: dict):
        # Store configuration dictionary (e.g., API keys, host, port)
        self.config = provider_config
        self.last_exception = ""

    def set_last_exception(self, error) -> str:
        """
        Store the provider failure reason so callers can persist it.
        """
        self.last_exception = str(error)
        return self.last_exception

    @abstractmethod
    def validate_config(self) -> bool:
        """
        Check if all necessary configuration values are present and valid.
        This prevents runtime errors due to missing credentials or settings.
        """
        pass

    @abstractmethod
    def send(self, recipients: List[str], content: Dict[str, str]) -> str:
        """
        Send the notification to the recipient with the given content.
        Returns send notification status
        """
        pass

    def handle_callback(self, data: Dict, headers: Dict) -> Dict:
        """
        Process a provider callback.
        Providers that send callbacks should override this method.
        """
        raise NotImplementedError("Callback handling is not implemented for this provider.")

    def verify_callback_signature(self, data: Dict, headers: Dict) -> bool:
        """
        Verify a provider callback signature.
        Providers with custom signature schemes should override this method.
        """
        return True
