from __future__ import annotations

import httpx
import pytest

from app.adapters.gus import GUSClient


@pytest.mark.asyncio
async def test_deep_real_flow_pszenica_price_in_poland_2017(
    mock_gus_api_responses
) -> None:
    client = GUSClient(api_key="test-key")

    try:
        result = await client.query_data(
            query="średnia cena pszenicy w Polsce w 2017",
            region="Polska",
            years=[2017],
        )
    finally:
        await client._client.aclose()

    assert result.name == "Przeciętne ceny skupu pszenicy"
    assert result.data_points[0].year == 2017
    assert result.data_points[0].value == pytest.approx(65.64)

    assert any(
        "/data/by-variable/218207" in call
        for call in mock_gus_api_responses
    )