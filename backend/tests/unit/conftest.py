from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List

import httpx
import pytest

from app.adapters.gus import GUSClient

_TEST_ROOT = Path(__file__).resolve().parents[1]

# Prefer a shared resources folder, but fall back to the existing integration one.
_RESOURCE_DIR = _TEST_ROOT / "resources" / "gus_api_responses"
if not _RESOURCE_DIR.is_dir():
    _RESOURCE_DIR = _TEST_ROOT / "integration" / "gus_api_responses"

def _read_json(name: str) -> Any:
    path = _RESOURCE_DIR / name

    if not path.exists():
        available = ", ".join(sorted(p.name for p in _RESOURCE_DIR.glob("*.json"))) or "(none)"
        raise FileNotFoundError(
            f"Missing GUS API response fixture: {name!r} in {_RESOURCE_DIR}. "
            f"Available fixtures: {available}"
        )

    with path.open(encoding="utf-8") as fh:
        raw = fh.read()

    # Fixture files may include a curl command block before the JSON payload.
    json_start = raw.find("{")
    if json_start == -1:
        json_start = raw.find("[")
    if json_start == -1:
        raise ValueError(f"Fixture {path} does not contain a JSON object or array")

    return json.loads(raw[json_start:])


def _first_existing(candidates: List[str]) -> str:
    for name in candidates:
        if (_RESOURCE_DIR / name).exists():
            return name

    return candidates[0]


def _fixture_for_request(request: httpx.Request) -> Any:
    path = request.url.path
    params = request.url.params

    if path == "/subjects":
        return _read_json("get_subjects.json")

    # /subjects/{subject_id}/subjects
    match = re.fullmatch(r"/subjects/(?P<subject_id>[^/]+)", path)
    if match:
        subject_id = match.group("subject_id")
        name = _first_existing(
            [
                f"get_subjects_{subject_id}.json",
                f"get_subjects_{subject_id.lower()}.json",
                f"get_subjects_{subject_id.upper()}.json",
            ]
        )
        return _read_json(name)

    if path == "/variables/search":
        subject_id = params.get("subject-id", "")
        name = params.get("name", "").lower()

        subject_variants = [subject_id, subject_id.lower(), subject_id.upper()]
        name_variants = [name, name.replace(" ", "_")]

        candidates = [
            f"get_variable_search_{sid}_{n}.json"
            for sid in subject_variants
            for n in name_variants
        ]

        return _read_json(_first_existing(candidates))

    # /data/by-variable/{variable_id}
    match = re.fullmatch(r"/data/by-variable/(?P<var_id>\d+)", path)
    if match:
        var_id = match.group("var_id")
        stem = f"get_data_by_variable_{var_id}"

        if "year" in params and "unit-level" in params:
            candidates = [
                f"{stem}_y{params['year']}_unit{params['unit-level']}.json",
                f"{stem}_y{params['year']}.json",
                f"{stem}_unit{params['unit-level']}.json",
                f"{stem}.json",
            ]
        elif "year" in params:
            candidates = [
                f"{stem}_y{params['year']}.json",
                f"{stem}.json",
            ]
        elif "unit-level" in params:
            candidates = [
                f"{stem}_unit{params['unit-level']}.json",
                f"{stem}.json",
            ]
        else:
            candidates = [f"{stem}.json"]

        return _read_json(_first_existing(candidates))

    # Generic endpoints
    endpoint_map = {
        "/aggregates": "get_aggregates.json",
        "/attributes": "get_attributes.json",
        "/levels": "get_levels.json",
        "/measures": "get_measures.json",
    }

    if path in endpoint_map:
        return _read_json(endpoint_map[path])

    raise FileNotFoundError(
        f"No GUS API response fixture matched {request.method} {request.url}"
    )


@pytest.fixture
def mock_gus_api_responses(monkeypatch):
    calls: List[str] = []

    async def _mocked_request(self, method: str, path: str, **kwargs: Any) -> Any:
        full_path = path if path.startswith("/") else f"/{path}"
        request = httpx.Request(
            method,
            f"http://mock.local{full_path}",
            params=kwargs.get("params") or {},
        )

        calls.append(f"{method} {request.url}")

        return _fixture_for_request(request)

    monkeypatch.setattr(GUSClient, "_request", _mocked_request)

    return calls