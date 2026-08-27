import pytest
from unittest.mock import AsyncMock, patch

from app.adapters.gus import GUSClient, GUSNotFoundError
from app.adapters.schemas import DataSource
from typing import Any, Dict

@pytest.mark.asyncio
async def test_gus_resolve_query(mock_gus_search_response: Dict[str, Any]) -> None:
    with patch.object(GUSClient, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_gus_search_response
        client = GUSClient()
        
        result = await client.resolve_query("population")
        
        assert result == "12345"
        assert mock_request.call_count == 1
        await client.close()

@pytest.mark.asyncio
async def test_gus_resolve_query_not_found() -> None:
    with patch.object(GUSClient, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"results": []}
        client = GUSClient()
        
        with pytest.raises(GUSNotFoundError):
            await client.resolve_query("unknown_metric")
            
        await client.close()

def test_gus_normalize_response(mock_gus_data_response: Dict[str, Any]) -> None:
    client = GUSClient()
    normalized = client.normalize_response(mock_gus_data_response, metric_name="Population Test")
    
    assert normalized.source == DataSource.GUS
    assert normalized.region == "Poland"
    assert normalized.metric_name == "Population Test"
    assert len(normalized.values) == 2
    assert normalized.values[0].date == "2020-01-01"
    assert normalized.values[0].value == 38000000.0
    assert normalized.time_period == "2020-01-01 to 2021-01-01"