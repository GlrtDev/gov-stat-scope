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
        parent_id = params.get("parent-id") or params.get("parentId")
        if parent_id:
            name = _first_existing(
                [
                    f"get_subjects_parent_id_{parent_id}.json",
                    f"get_subjects_parent_id_{parent_id.lower()}.json",
                    f"get_subjects_parent_id_{parent_id.upper()}.json",
                ]
            )
            return _read_json(name)
        return _read_json("get_subjects_page_0_page_size_100.json")

    # /subjects/{subject_id}
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
        name = (params.get("name") or "").strip().lower()
        page = params.get("page", "0")
        page_size = params.get("page-size", "10")
        fmt = (params.get("format") or "json").lower()

        subject_variants = [subject_id, subject_id.lower(), subject_id.upper()]
        fmt_variants = [fmt, "json"]
        name_variants = [name, name.replace(" ", "_")] if name else [""]

        if name:
            candidates = [
                f"get_variables_search_subject_id_{sid}_name_{n}_page_{page}_page_size_{page_size}_format_{f}.json"
                for sid in subject_variants
                for n in name_variants
                for f in fmt_variants
            ]
        else:
            candidates = [
                f"get_variables_search_subject_id_{sid}_page_{page}_page_size_{page_size}_format_{f}.json"
                for sid in subject_variants
                for f in fmt_variants
            ]

        # Legacy fallback for older fixture names.
        candidates.extend(
            f"get_variable_search_{sid}_{n}.json"
            for sid in subject_variants
            for n in name_variants
        )

        return _read_json(_first_existing(candidates))

    # /data/by-variable/{variable_id}
    match = re.fullmatch(r"/data/by-variable/(?P<var_id>\d+)", path)
    if match:
        var_id = match.group("var_id")

        # 1. Preserve the exact order of query params passed by the client.
        ordered_components = [f"var_id_{var_id}"]
        for key, value in params.items():
            norm_key = key.replace("-", "_")
            if norm_key == "var_id":
                continue
            ordered_components.append(f"{norm_key}_{value}")

        ordered_candidates = [
            "get_data_by_variable_" + "_".join(ordered_components) + ".json"
        ]

        # 2. Canonical order fallback for the standard GUS parameter names.
        canonical_components = [f"var_id_{var_id}"]
        seen = {"var_id"}

        canonical_keys = (
            "year",
            "unit-level",
            "aggregate-id",
            "page",
            "page-size",
            "format",
        )
        for key in canonical_keys:
            if key in params:
                norm_key = key.replace("-", "_")
                canonical_components.append(f"{norm_key}_{params[key]}")
                seen.add(norm_key)

        for key, value in params.items():
            norm_key = key.replace("-", "_")
            if norm_key not in seen:
                canonical_components.append(f"{norm_key}_{value}")
                seen.add(norm_key)

        canonical_candidates = [
            "get_data_by_variable_" + "_".join(canonical_components) + ".json"
        ]

        # 3. Legacy short-form names.
        legacy_candidates = []
        if "year" in params:
            legacy_candidates.append(
                f"get_data_by_variable_{var_id}_y{params['year']}.json"
            )
        if "unit-level" in params:
            legacy_candidates.append(
                f"get_data_by_variable_{var_id}_unit{params['unit-level']}.json"
            )
        if "year" in params and "unit-level" in params:
            legacy_candidates.append(
                f"get_data_by_variable_{var_id}_y{params['year']}_unit{params['unit-level']}.json"
            )
        legacy_candidates.append(f"get_data_by_variable_{var_id}.json")

        candidates = ordered_candidates + canonical_candidates + legacy_candidates

        return _read_json(_first_existing(candidates))

    # Generic endpoints
    endpoint_map = {
        "/aggregates": "get_aggregates.json",
        "/attributes": "get_attributes.json",
        "/levels": "get_levels.json",
        "/measures": "get_measures.json",
        "/units": "get_units_level_0_page_0_page_size_100_format_json.json",
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