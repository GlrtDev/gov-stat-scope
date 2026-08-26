from backend.app.adapters.base import DataSourceClient
from backend.app.adapters.fred import FredClient
from backend.app.adapters.gus import GUSClient
from backend.models import DataSource


def get_adapter(source: DataSource) -> DataSourceClient:
    """
    Factory function to instantiate and return the appropriate data adapter client.
    
    Args:
        source (DataSource): The enum value indicating which data source adapter to load.
        
    Returns:
        DataSourceClient: An instantiated adapter class ready for data fetching.
        
    Raises:
        ValueError: If the provided source is not supported.
    """
    if source == DataSource.GUS:
        return GUSClient()
    elif source == DataSource.FRED:
        return FredClient()
    else:
        raise ValueError(f"Unsupported data source: {source}")