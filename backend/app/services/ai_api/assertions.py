from __future__ import annotations

import json
import re
from typing import Any

from app.services.ai_api.schemas import APIAssertion, AssertionResult, HTTPExchange


def evaluate_assertions(
    assertions: list[APIAssertion],
    exchange: HTTPExchange,
) -> tuple[AssertionResult, ...]:
    return tuple(_evaluate_one(assertion, exchange) for assertion in assertions)


def _evaluate_one(assertion: APIAssertion, exchange: HTTPExchange) -> AssertionResult:
    if assertion.type == "status_code":
        passed = exchange.status_code == int(assertion.expected)
        return AssertionResult(
            type=assertion.type,
            passed=passed,
            message="状态码符合预期" if passed else f"状态码不符合预期：实际 {exchange.status_code}",
            expected=int(assertion.expected),
            actual=exchange.status_code,
        )

    if assertion.type in {"json_path_exists", "json_path_equals"}:
        loaded, data = _load_json(exchange.response_text)
        if not loaded:
            return AssertionResult(
                type=assertion.type,
                passed=False,
                message="响应体不是可解析 JSON",
                expected=assertion.expected,
                actual=None,
                path=assertion.path,
            )
        found, value = _json_path_get(data, assertion.path or "")
        if assertion.type == "json_path_exists":
            return AssertionResult(
                type=assertion.type,
                passed=found,
                message="JSON 路径存在" if found else f"JSON 路径不存在：{assertion.path}",
                actual=value,
                path=assertion.path,
            )
        passed = found and value == assertion.expected
        return AssertionResult(
            type=assertion.type,
            passed=passed,
            message="JSON 路径值符合预期" if passed else f"JSON 路径值不符合预期：{assertion.path}",
            expected=assertion.expected,
            actual=value if found else None,
            path=assertion.path,
        )

    if assertion.type == "body_contains":
        needle = str(assertion.contains if assertion.contains is not None else assertion.expected)
        passed = needle in exchange.response_text
        return AssertionResult(
            type=assertion.type,
            passed=passed,
            message="响应体包含预期文本" if passed else f"响应体不包含预期文本：{needle}",
            expected=needle,
            actual="matched" if passed else "not_found",
        )

    if assertion.type == "header_exists":
        wanted = str(assertion.name or "").lower()
        actual_names = {key.lower(): value for key, value in exchange.response_headers.items()}
        passed = wanted in actual_names
        return AssertionResult(
            type=assertion.type,
            passed=passed,
            message="响应 header 存在" if passed else f"响应 header 不存在：{assertion.name}",
            actual=actual_names.get(wanted),
            name=assertion.name,
        )

    return AssertionResult(type=assertion.type, passed=False, message="未知断言类型")


def _load_json(text: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except Exception:
        return False, None


def _json_path_get(data: Any, path: str) -> tuple[bool, Any]:
    if path == "$":
        return True, data
    if not path.startswith("$."):
        return False, None
    current = data
    for token in _tokenize_json_path(path[2:]):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False, None
            current = current[token]
            continue
        if not isinstance(current, dict) or token not in current:
            return False, None
        current = current[token]
    return True, current


def _tokenize_json_path(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in path.split("."):
        if not part:
            continue
        match = re.match(r"^([^\[]+)((?:\[\d+\])*)$", part)
        if not match:
            tokens.append(part)
            continue
        tokens.append(match.group(1))
        for index in re.findall(r"\[(\d+)\]", match.group(2)):
            tokens.append(int(index))
    return tokens
