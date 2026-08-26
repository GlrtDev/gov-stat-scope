import os
import pytest

from backend.app.adapters.gus import GUSClient, GUSNotFoundError

pytestmark = pytest.mark.skipif(
    not os.getenv("GUS_CLIENT_ID"),
    reason="GUS_CLIENT_ID not set in environment."
)

@pytest.mark.asyncio
async def test_gus_live_resolve_and_fetch() -> None:
    client = GUSClient()
    try:
        # Resolving "ludność" (population in Polish)
        variable_id = await client.resolve_query("ludność")
        assert variable_id.isdigit()
        
        # Fetching data for the resolved variable
        data = await client.fetch_time_range(variable_id, unit_id="000000000000", year_start=2020, year_end=2021)
        normalized = client.normalize_response(data)
        
        assert normalized.metric_name != ""
        assert len(normalized.values) > 0
    finally:
        await client.close()