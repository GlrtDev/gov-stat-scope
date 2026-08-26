from abc import ABC, abstractmethod
from typing import Any, Dict

from backend.app.adapters.schemas import NormalizedSeries


class DataSourceClient(ABC):
    """
    Abstract base class for all government data source adapters.
    """

    @abstractmethod
    async def resolve_query(self, query: str) -> str:
        """
        Resolves a natural language query or metric name into a source-specific resource ID.
        """
        pass

    @abstractmethod
    async def fetch_data(self, resource_id: str, **kwargs: Any) -> Dict[str, Any]:
        """
        Fetches raw data from the external API using the resolved resource ID.
        """
        pass

    @abstractmethod
    def normalize_response(self, raw_data: Dict[str, Any], **kwargs: Any) -> NormalizedSeries:
        """
        Transforms the raw API JSON response into the common NormalizedSeries schema.
        """
        pass