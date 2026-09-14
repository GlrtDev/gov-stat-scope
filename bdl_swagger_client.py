#!/usr/bin/env python3
"""Swagger-driven client for the BDL API.

Reads an OpenAPI 3 description, lists/calls endpoints, and saves each response as:

    get_<endpoint>_<param>_<value>.json

Examples:
    python bdl_swagger_client.py list
    python bdl_swagger_client.py call "/aggregates/{id}" --param id=1
    python bdl_swagger_client.py call "/data/by-variable/{var-id}" --param var-id=364763
    python bdl_swagger_client.py interactive
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import quote

try:
    import httpx
except ImportError:
    raise SystemExit("This script requires httpx - install it with: pip install httpx")

DEFAULT_BASE_URL = "https://bdl.stat.gov.pl/api/v1"
DEFAULT_SPEC = "swagger.json"
DEFAULT_OUTPUT_DIR = "responses"

PATH_PARAM_RE = re.compile(r"{([^}]+)}")
SUPPORTED_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def load_spec(source: str) -> dict[str, Any]:
    """Load OpenAPI JSON from a local file or an http(s) URL."""
    if source.startswith(("http://", "https://")):
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(source)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"failed to load spec from {source}: {exc}") from exc

    path = Path(source)
    if not path.exists():
        raise RuntimeError(f"OpenAPI spec not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in spec {path}: {exc}") from exc


@dataclass
class Endpoint:
    path: str
    method: str
    summary: str = ""
    operation_id: str = ""
    parameters: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def iter_endpoints(spec: dict[str, Any]) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(path, str) or not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in SUPPORTED_METHODS or not isinstance(operation, dict):
                continue
            endpoints.append(
                Endpoint(
                    path=path,
                    method=method.lower(),
                    summary=str(operation.get("summary", "")),
                    operation_id=str(operation.get("operationId", "")),
                    parameters=list(operation.get("parameters", [])),
                    tags=list(operation.get("tags", [])),
                )
            )
    return endpoints


def find_endpoint(endpoints: Sequence[Endpoint], selection: str) -> Endpoint:
    """Allow selecting by path, normalized path, or operationId."""
    selection = selection.strip()
    normalized = selection.lower().lstrip("/")

    for endpoint in endpoints:
        if selection == endpoint.path or selection == endpoint.operation_id:
            return endpoint
        if normalized == endpoint.path.lower().lstrip("/"):
            return endpoint
        if endpoint.operation_id and selection.lower() == endpoint.operation_id.lower():
            return endpoint

    raise KeyError(f"endpoint not found from selection: {selection}")


def path_param_names(path: str) -> list[str]:
    return PATH_PARAM_RE.findall(path)


def endpoint_param_schema(endpoint: Endpoint, name: str) -> dict[str, Any]:
    for parameter in endpoint.parameters:
        if parameter.get("name") == name:
            schema = parameter.get("schema")
            return schema if isinstance(schema, dict) else {}
    return {}


def parse_param_pairs(items: Sequence[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid --param (expected key=value): {item}")
        key, value = item.split("=", 1)
        if key in params:
            existing = params[key]
            params[key] = [existing, value] if not isinstance(existing, list) else [*existing, value]
        else:
            params[key] = value
    return params


def parse_header_pairs(items: Sequence[str]) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for item in items:
        if ":" not in item:
            raise ValueError(f"invalid --header (expected 'Name: value'): {item}")
        name, _, value = item.partition(":")
        headers.append((name.strip(), value.strip()))
    return headers


def normalize_param_values(endpoint: Endpoint, params: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue

        if isinstance(value, list):
            cleaned = [v for v in value if v is not None and str(v).strip() != ""]
            if cleaned:
                normalized[key] = cleaned
            continue

        if isinstance(value, str) and value.strip() == "":
            continue

        schema = endpoint_param_schema(endpoint, key)
        if schema.get("type") == "array" and isinstance(value, str):
            values = [part.strip() for part in value.split(",") if part.strip()]
            if values:
                normalized[key] = values
        else:
            normalized[key] = value

    return normalized


def ensure_required_params(endpoint: Endpoint, params: dict[str, Any]) -> None:
    missing: list[str] = []

    for name in path_param_names(endpoint.path):
        if name not in params:
            missing.append(name)

    for parameter in endpoint.parameters:
        if (
            parameter.get("in") == "query"
            and parameter.get("required")
            and parameter.get("name") not in params
        ):
            missing.append(parameter["name"])

    if missing:
        raise ValueError(f"missing required parameter(s): {', '.join(dict.fromkeys(missing))}")


def fill_path_params(path: str, params: dict[str, Any]) -> str:
    result = path
    for name in path_param_names(path):
        value = params.get(name)
        if value is None:
            raise ValueError(f"missing path parameter: {name}")
        if isinstance(value, list):
            if not value:
                raise ValueError(f"missing path parameter: {name}")
            value = value[0]
        result = result.replace("{" + name + "}", quote(str(value), safe=""))
    return result


def build_headers(
    params: dict[str, Any],
    user_headers: Sequence[tuple[str, str]],
) -> httpx.Headers:
    headers = httpx.Headers()
    for name, value in user_headers:
        if value != "":
            headers[name] = value

    lang_value = params.get("lang")
    if isinstance(lang_value, list):
        lang_value = lang_value[0] if lang_value else None
    if lang_value and headers.get("Accept-Language") is None:
        headers["Accept-Language"] = str(lang_value)

    format_value = params.get("format")
    if isinstance(format_value, list):
        format_value = format_value[0] if format_value else None
    if format_value and headers.get("Accept") is None:
        content_type = {
            "json": "application/json",
            "jsonapi": "application/vnd.api+json",
            "xml": "application/xml",
        }.get(str(format_value), "application/json")
        headers["Accept"] = content_type

    if headers.get("Accept") is None:
        headers["Accept"] = "application/json"

    if headers.get("User-Agent") is None:
        headers["User-Agent"] = "bdl-swagger-client/1.0"

    return headers


def build_query_params(endpoint: Endpoint, params: dict[str, Any]) -> list[tuple[str, str]]:
    path_names = set(path_param_names(endpoint.path))
    pairs: list[tuple[str, str]] = []

    for key, value in params.items():
        if key in path_names:
            continue
        if isinstance(value, list):
            for item in value:
                if item is not None:
                    pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))

    return pairs


def call_endpoint(
    base_url: str,
    endpoint: Endpoint,
    params: dict[str, Any],
    user_headers: Sequence[tuple[str, str]],
    timeout: float,
    api_key: Optional[str] = None,
    api_key_header_name: str = "X-ClientId",
) -> httpx.Response:
    params = normalize_param_values(endpoint, params)
    ensure_required_params(endpoint, params)

    path = fill_path_params(endpoint.path, params)
    url = f"{base_url.rstrip('/')}{path}"
    headers = build_headers(params, user_headers)

    # API key as a header instead of a query parameter.
    if api_key and headers.get(api_key_header_name) is None:
        headers[api_key_header_name] = api_key

    query_params = build_query_params(endpoint, params)

    return httpx.request(
        endpoint.method.upper(),
        url,
        params=query_params,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    )


def response_suffix(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    return ".xml" if "xml" in content_type else ".json"


def sanitize_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")
    return token or "value"


def build_filename_stem(endpoint: Endpoint, params: dict[str, Any]) -> str:
    path_name = re.sub(r"[^A-Za-z0-9]+", "_", endpoint.path.strip("/")).strip("_")
    parts = [endpoint.method.lower(), path_name]
    path_names = set(path_param_names(endpoint.path))

    for key, value in params.items():
        if value is None:
            continue

        if key in path_names:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if item is not None and str(item).strip() != "":
                    parts.append(sanitize_token(item))
            continue

        if isinstance(value, list):
            values = [
                sanitize_token(item)
                for item in value
                if item is not None and str(item).strip() != ""
            ]
            if values:
                parts.append(f"{sanitize_token(key)}_{'_'.join(values)}")
        else:
            if str(value).strip() != "":
                parts.append(f"{sanitize_token(key)}_{sanitize_token(value)}")

    return "_".join(parts)

def redact_url(url: str, api_key: Optional[str]) -> str:
    if api_key:
        return url.replace(api_key, "***")
    return url

def save_response(
    response: httpx.Response,
    output_dir: str | Path,
    filename_stem: str,
    custom_filename: Optional[str] = None,
) -> Path:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    if custom_filename:
        output_path = Path(custom_filename)
        if len(output_path.parts) == 1:
            output_path = output_root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not output_path.suffix:
            output_path = output_path.with_suffix(response_suffix(response))
    else:
        output_path = output_root / f"{filename_stem}{response_suffix(response)}"

    if "json" in response.headers.get("content-type", "").lower():
        try:
            body = json.dumps(response.json(), indent=2, ensure_ascii=False)
        except ValueError:
            body = response.text
    else:
        body = response.text

    output_path.write_text(body, encoding="utf-8")
    return output_path


def _format_enum_hint(schema: dict[str, Any]) -> str:
    enum = schema.get("enum")
    if not enum:
        return ""
    shown = ", ".join(str(item) for item in enum)
    if len(shown) > 80:
        shown = shown[:77] + "..."
    return f" [{shown}]"


def prompt_params(endpoint: Endpoint) -> Optional[tuple[dict[str, Any], list[tuple[str, str]]]]:
    params: dict[str, Any] = {}
    headers: list[tuple[str, str]] = []

    print(f"\nEndpoint: {endpoint.method.upper()} {endpoint.path}")
    if endpoint.summary:
        print(endpoint.summary)

    for name in path_param_names(endpoint.path):
        raw = input(f"  path {name} (required): ").strip()
        if not raw:
            print("  call skipped")
            return None
        params[name] = raw

    for parameter in endpoint.parameters:
        location = parameter.get("in")
        name = str(parameter.get("name", ""))
        schema = parameter.get("schema", {})
        if not isinstance(schema, dict):
            schema = {}

        if location == "header":
            hint = _format_enum_hint(schema)
            raw = input(f"  header {name}{hint} (empty to skip): ").strip()
            if raw:
                headers.append((name, raw))
            continue

        if location != "query":
            continue

        required = bool(parameter.get("required"))
        default = schema.get("default")
        hint = _format_enum_hint(schema)
        if default is not None:
            hint += f" (default={default})"

        prompt = f"  query {name}{hint}"
        if required:
            prompt += " [required]"
        prompt += ": "

        raw = input(prompt).strip()
        if not raw and default is not None:
            raw = str(default)
        if not raw and required:
            print("  call skipped")
            return None
        if raw:
            params[name] = raw

    return params, headers


def cmd_list(args: argparse.Namespace) -> None:
    try:
        spec = load_spec(args.spec)
    except RuntimeError as exc:
        fail(str(exc))

    endpoints = iter_endpoints(spec)
    tag_filter = args.tag.lower() if args.tag else None

    for endpoint in endpoints:
        if tag_filter and not any(tag.lower() == tag_filter for tag in endpoint.tags):
            continue
        print(f"{endpoint.method.upper():6} {endpoint.path:45} {endpoint.summary}")


def cmd_call(args: argparse.Namespace) -> None:
    try:
        spec = load_spec(args.spec)
    except RuntimeError as exc:
        fail(str(exc))

    endpoints = iter_endpoints(spec)

    try:
        endpoint = find_endpoint(endpoints, args.endpoint)
    except KeyError as exc:
        fail(str(exc))

    try:
        params = normalize_param_values(endpoint, parse_param_pairs(args.param))
        headers = parse_header_pairs(args.header)
    except ValueError as exc:
        fail(str(exc))

    try:
        response = call_endpoint(
            args.base_url,
            endpoint,
            params,
            headers,
            args.timeout,
            api_key=args.api_key,
            api_key_header_name=args.api_key_header_name,
        )
    except ValueError as exc:
        fail(str(exc))
    except httpx.HTTPError as exc:
        fail(f"request failed: {exc}")

    stem = build_filename_stem(endpoint, params)

    try:
        output_path = save_response(response, args.output_dir, stem, args.output)
    except OSError as exc:
        fail(f"failed to save response: {exc}")

    print(f"{response.status_code} {endpoint.method.upper()} {redact_url(str(response.url), args.api_key)}")
    print(f"saved: {output_path}")


def cmd_interactive(args: argparse.Namespace) -> None:
    try:
        spec = load_spec(args.spec)
    except RuntimeError as exc:
        fail(str(exc))

    api_key = args.api_key
    if api_key is None:
        try:
            entered = input("API key (empty to skip): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        api_key = entered or None

    endpoints = iter_endpoints(spec)
    if not endpoints:
        fail("no endpoints found in spec")

    while True:
        print("\nAvailable endpoints:")
        for idx, endpoint in enumerate(endpoints, 1):
            print(f"{idx:>3}. {endpoint.method.upper():6} {endpoint.path:45} {endpoint.summary}")
        print("  0. Quit")

        try:
            selection = input("Select endpoint number (0 to quit): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if selection == "0":
            return
        if not selection.isdigit() or not (1 <= int(selection) <= len(endpoints)):
            print("  invalid selection")
            continue

        endpoint = endpoints[int(selection) - 1]
        result = prompt_params(endpoint)
        if result is None:
            continue

        params, headers = result
        params = normalize_param_values(endpoint, params)

        try:
            response = call_endpoint(
                args.base_url,
                endpoint,
                params,
                headers,
                args.timeout,
                api_key=api_key,
                api_key_header_name=args.api_key_header_name,
            )
        except ValueError as exc:
            print(f"  {exc}")
            continue
        except httpx.HTTPError as exc:
            print(f"  request failed: {exc}")
            continue

        stem = build_filename_stem(endpoint, params)

        try:
            output_path = save_response(response, args.output_dir, stem)
        except OSError as exc:
            print(f"  failed to save response: {exc}")
            continue

        print(f"\n{response.status_code} {endpoint.method.upper()} {redact_url(str(response.url), api_key)}")
        print(f"saved: {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bdl_swagger_client",
        description="Swagger-driven BDL API client. Reads the OpenAPI spec, sends requests, and saves responses.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_options(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--spec",
            default=DEFAULT_SPEC,
            help=f"Path or URL to OpenAPI spec (default: {DEFAULT_SPEC})",
        )
        subparser.add_argument(
            "--base-url",
            default=os.getenv("BDL_API_URL", DEFAULT_BASE_URL),
            help=f"Base URL without trailing slash (default: {DEFAULT_BASE_URL})",
        )
        subparser.add_argument(
            "--output-dir",
            default=DEFAULT_OUTPUT_DIR,
            help=f"Directory for saved responses (default: {DEFAULT_OUTPUT_DIR})",
        )
        subparser.add_argument(
            "--timeout",
            type=float,
            default=30.0,
            help="HTTP timeout in seconds (default: 30)",
        )
        subparser.add_argument(
            "--api-key",
            default=os.getenv("BDL_API_KEY"),
            help="API key; sent as a header (default: from BDL_API_KEY env)",
        )
        subparser.add_argument(
            "--api-key-header-name",
            default="X-ClientId",
            help="Header name for the API key (default: X-ClientId)",
        )

    list_parser = subparsers.add_parser("list", help="List all endpoints from the spec")
    add_common_options(list_parser)
    list_parser.add_argument("--tag", help="Filter endpoints by tag, e.g. Variables")

    call_parser = subparsers.add_parser("call", help="Call one endpoint")
    add_common_options(call_parser)
    call_parser.add_argument(
        "endpoint",
        help="Endpoint path or operationId, e.g. /aggregates/{id}, DataByVariableGet",
    )
    call_parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Query/path parameter in key=value form; repeat for multi-value parameters",
    )
    call_parser.add_argument(
        "--header",
        action="append",
        default=[],
        help='Request header in "Name: value" form; repeat as needed',
    )
    call_parser.add_argument(
        "--output",
        help="Custom output filename (default: generated from endpoint and params)",
    )

    interactive_parser = subparsers.add_parser("interactive", help="Interactive endpoint browser")
    add_common_options(interactive_parser)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "call":
        cmd_call(args)
    elif args.command == "interactive":
        cmd_interactive(args)


if __name__ == "__main__":
    main()