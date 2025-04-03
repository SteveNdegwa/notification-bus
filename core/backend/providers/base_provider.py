from abc import ABC, abstractmethod
from typing import Dict, List


class BaseProvider(ABC):
    """
    Abstract base class for all notification providers (e.g., SMTP, Twilio, Firebase)
    """

    def __init__(self, provider_config: dict):
        # Store configuration dictionary (e.g., API keys, host, port)
        self.config = provider_config

    @abstractmethod
    def validate_config(self) -> bool:
        """
        Check if all necessary configuration values are present and valid.
        This prevents runtime errors due to missing credentials or settings.
        """
        pass

    @abstractmethod
    def send(self, recipients: List[str], content: Dict[str, str]) -> bool:
        """
        Send the notification to the recipient with the given content.
        Must return True if successful, False otherwise.
        """
        pass

