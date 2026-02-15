"""Backend adapters for different agent runtimes."""

from .base import AgentBackend
from .factory import create_backend

__all__ = ["AgentBackend", "create_backend"]

