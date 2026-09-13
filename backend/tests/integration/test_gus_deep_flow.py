# backend/tests/test_gus_deep_flow.py
import httpx
import pytest

from app.adapters.gus import GUSClient


def _json(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={"Content-Type": "application/json"},
    )


@pytest.mark.asyncio
async def test_deep_real_flow_pszenica_price_in_poland_2017():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        calls.append(f"{request.method} {url}")

        # 1. top-level subjects: get_subjects.json -> K15 "CENY"
        if url.path.endswith("/subjects") and url.params.get("level") == "0":
            return _json({
                "results": [
                    {
                        "id": "K15",
                        "name": "CENY",
                        "hasVariables": False,
                        "hasChildren": True,
                    }
                ]
            })

        # 2. children of K15: get_subjects_K15.json -> G186
        if url.path.endswith("/subjects/K15/subjects"):
            return _json({
                "results": [
                    {
                        "id": "G186",
                        "name": "CENY W ROLNICTWIE",
                        "hasVariables": False,
                        "hasChildren": True,
                    }
                ]
            })

        # 3. children of G186: get_subjects_G186.json -> P2967
        if url.path.endswith("/subjects/G186/subjects"):
            return _json({
                "results": [
                    {
                        "id": "P2967",
                        "name": "Przeciętne ceny skupu ważniejszych produktów rolnych (dane miesięczne)",
                        "hasVariables": True,
                        "hasChildren": False,
                    }
                ]
            })

        # 4. variable search must use the correct Polish word: pszenica
        if url.path.endswith("/variables/search"):
            assert url.params.get("subject-id") == "P2967"
            assert url.params.get("name") == "pszenica"
            assert url.params.get("page-size") == "50"

            return _json({
                "results": [
                    {
                        "id": "218206",
                        "name": "Przeciętne ceny skupu pszenicy",
                        "subjectId": "P2967",
                    }
                ]
            })

        # 5. data for variable 218206, Poland, 2017
        if url.path.endswith("/data/218206"):
            return _json({
                "results": [
                    {
                        "id": "218206",
                        "name": "Przeciętne ceny skupu pszenicy",
                        "values": [
                            {
                                "year": 2017,
                                "val": 65.64,
                            }
                        ],
                    }
                ]
            })

        return _json(
            {
                "error": f"Unexpected request: {request.method} {url}",
            },
            status=404,
        )

    transport = httpx.MockTransport(handler)
    client = GUSClient(api_key="test-key")

    # Replace the real HTTP client with a mocked one.
    old_client = client.client
    client.client = httpx.AsyncClient(
        transport=transport,
        base_url="https://bdl.stat.gov.pl/api/v1",
    )
    await old_client.aclose()

    try:
        result = await client.query_data(
            query="średnia cena pszenicy w Polsce w 2017",
            region="Polska",
            years=[2017],
        )
    finally:
        await client.client.aclose()

    # Final answer: in Poland in 2017 pszenica cost 65.64 zł per 1 dt.
    assert result.data_points[0].value == 65.64
    assert "pol" in result.region.lower()
    assert "2017" in result.time_period

    # Verify the real deep flow was actually executed.
    assert any("subjects?level=0" in call for call in calls)
    assert any("/subjects/K15/subjects" in call for call in calls)
    assert any("/subjects/G186/subjects" in call for call in calls)
    assert any("/variables/search" in call for call in calls)
    assert any("/data/218206" in call for call in calls)