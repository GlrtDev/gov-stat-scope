import os
import pytest

from app.adapters.fred import FredClient

pytestmark = pytest.mark.skipif(
    not os.getenv("FRED_API_KEY"),
    reason="FRED_API_KEY not set in environment."
)

@pytest.mark.asyncio
async def test_fred_live_resolve_and_fetch() -> None:
    client = FredClient()
    try:
        # 'cpi' is a hardcoded common metric, should resolve to CPIAUCSL
        series_id = await client.resolve_query("cpi")
        assert series_id == "CPIAUCSL"
        
        # Fetch bounded time range
        data = await client.fetch_time_range(series_id, observation_start="2023-01-01", observation_end="2023-03-01")
        normalized = client.normalize_response(data)
        
        assert normalized.metric_name.lower().find("consumer price index") != -1
        assert len(normalized.values) > 0
        assert normalized.values[0].value > 0
    finally:
        await client.close()