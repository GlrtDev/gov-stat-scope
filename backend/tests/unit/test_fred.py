import pytest
from unittest.mock import AsyncMock, patch
import os

from app.adapters.fred import FredClient, FredNotFoundError
from app.adapters.schemas import DataSource
from typing import Any, Dict

@pytest.fixture(autouse=True)
def set_fred_env() -> None:
    os.environ["FRED_API_KEY"] = "dummy_key"

@pytest.mark.asyncio
async def test_fred_resolve_query_common() -> None:
    client = FredClient()
    result = await client.resolve_query("gdp")
    assert result == "GDP"
    await client.close()

@pytest.mark.asyncio
async def test_fred_resolve_query_fallback(mock_fred_search_response: Dict[str, Any]) -> None:
    with patch.object(FredClient, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_fred_search_response
        client = FredClient()
        
        result = await client.resolve_query("obscure metric")
        
        assert result == "TEST_SERIES"
        assert mock_request.call_count == 1
        await client.close()

def test_fred_normalize_response(mock_fred_data_response: Dict[str, Any]) -> None:
    client = FredClient()
    normalized = client.normalize_response(mock_fred_data_response)
    
    assert normalized.source == DataSource.FRED
    assert normalized.region == "United States"
    assert normalized.metric_name == "Real Gross Domestic Product"
    # The "." missing value should be skipped
    assert len(normalized.values) == 2
    assert normalized.values[0].date == "2020-01-01"
    assert normalized.values[0].value == 21000.5
    assert normalized.values[1].date == "2020-07-01"
    assert normalized.time_period == "2020-01-01 to 2020-07-01"