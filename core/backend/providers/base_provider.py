from abc import ABC, abstractmethod
from typing import Dict

from core.models import Provider


class BaseProvider(ABC):
    def __init__(self, provider: Provider):
        self.provider = provider
        self.config = provider.config
        self.client = None
        self.initialize()

    @abstractmethod
    def initialize(self) -> None:
        pass

    @abstractmethod
    def send(self, recipient: str, content: Dict[str, str]) -> bool:
        pass

    @abstractmethod
    def validate_config(self) -> bool:
        pass
