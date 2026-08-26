import pytest
from typing import Any, Dict

@pytest.fixture
def mock_gus_search_response() -> Dict[str, Any]:
    return {
        "results": [
            {"id": "12345", "name": "Population", "subjectId": "sub1"}
        ]
    }

@pytest.fixture
def mock_gus_data_response() -> Dict[str, Any]:
    return {
        "results": [
            {
                "id": "unit1",
                "name": "Poland",
                "values": [
                    {"year": "2020", "val": 38000000, "attrId": 1},
                    {"year": "2021", "val": 37900000, "attrId": 1}
                ]
            }
        ]
    }

@pytest.fixture
def mock_fred_search_response() -> Dict[str, Any]:
    return {
        "seriess": [
            {"id": "TEST_SERIES", "title": "Test Economic Metric"}
        ]
    }

@pytest.fixture
def mock_fred_data_response() -> Dict[str, Any]:
    return {
        "metadata": {"title": "Real Gross Domestic Product"},
        "observations": [
            {"date": "2020-01-01", "value": "21000.5"},
            {"date": "2020-04-01", "value": "."},  # Missing value test
            {"date": "2020-07-01", "value": "21500.2"}
        ]
    }