"""Fork-side runner for MyDL's Odysseus brain-store client contract."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_ENV = "MYDL_ODYSSEUS_BRAIN_CLIENT_CONTRACT"
CONTRACT_VERSION = "mydl.odysseus.brain-client-contract.v1"
BRAIN_STORE_KEY_ID = "mydl.odysseus.brain-store.v1"
BRAIN_RECORD_SCHEMA = "mydl.odysseus.brain.record.v1"

ENVELOPE_KEYS = frozenset(
    {
        "version",
        "record_id",
        "key_id",
        "nonce",
        "ciphertext",
        "auth_tag",
        "associated_data",
        "conflict",
    },
)
ASSOCIATED_DATA_KEYS = frozenset(
    {"schema", "record_id", "user_identity", "created_device_id"},
)
CONFLICT_KEYS = frozenset(
    {
        "home_server_epoch",
        "logical_clock",
        "updated_at_ms",
        "parent_record_version",
        "deleted",
    },
)
FORBIDDEN_PLAINTEXT_KEYS = frozenset(
    {
        "plaintext",
        "payload",
        "summary",
        "title",
        "label",
        "embedding",
        "prompt",
        "memory",
        "brain_key",
        "brainKey",
        "brainStoreKey",
        "brain_store_key",
        "brain_store_key_hex",
        "mnemonic",
        "seed",
    },
)
BASE64URL_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
)


class ContractError(Exception):
    """Raised for contract setup, validation, or response mismatches."""


@dataclass(frozen=True, slots=True)
class Contract:
    contract_version: str
    brain_store_url: str
    brain_store_bearer: str
    brain_store_key_id: str
    mcp_bearer: str
    test_envelope: dict[str, Any]
    expected: dict[str, Any]


def _expect_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{field} must be an object")
    return value


def _expect_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{field} must be non-empty text")
    return value


def _expect_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{field} must be a non-negative integer")
    return value


def _assert_exact_keys(value: dict[str, Any], allowed: frozenset[str], field: str) -> None:
    if set(value) != allowed:
        raise ContractError(f"{field} has invalid keys")


def _reject_plaintext_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key in FORBIDDEN_PLAINTEXT_KEYS:
                raise ContractError("encrypted envelope contains forbidden cleartext metadata")
            _reject_plaintext_keys(child)
    elif isinstance(value, list):
        for item in value:
            _reject_plaintext_keys(item)


def _decode_base64url(value: Any, field: str, *, allow_empty: bool = False) -> bytes:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ContractError(f"{field} must be unpadded base64url text")
    if not value and allow_empty:
        return b""
    if "=" in value or any(char not in BASE64URL_ALPHABET for char in value):
        raise ContractError(f"{field} must be unpadded base64url text")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (binascii.Error, ValueError) as exc:
        raise ContractError(f"{field} must be unpadded base64url text") from exc


def _validate_associated_data(value: Any, record_id: str) -> None:
    associated = _expect_object(value, "associated_data")
    _assert_exact_keys(associated, ASSOCIATED_DATA_KEYS, "associated_data")
    if associated["schema"] != BRAIN_RECORD_SCHEMA:
        raise ContractError("associated_data schema is unsupported")
    if _expect_text(associated["record_id"], "associated_data.record_id") != record_id:
        raise ContractError("associated_data record id mismatch")
    _expect_text(associated["user_identity"], "associated_data.user_identity")
    _expect_text(associated["created_device_id"], "associated_data.created_device_id")


def _validate_conflict(value: Any) -> bool:
    conflict = _expect_object(value, "conflict")
    _assert_exact_keys(conflict, CONFLICT_KEYS, "conflict")
    _expect_non_negative_int(conflict["home_server_epoch"], "conflict.home_server_epoch")
    _expect_non_negative_int(conflict["logical_clock"], "conflict.logical_clock")
    _expect_non_negative_int(conflict["updated_at_ms"], "conflict.updated_at_ms")
    parent = conflict["parent_record_version"]
    if parent is not None:
        _expect_text(parent, "conflict.parent_record_version")
    if not isinstance(conflict["deleted"], bool):
        raise ContractError("conflict.deleted must be a boolean")
    return conflict["deleted"]


def validate_envelope(envelope: Any) -> dict[str, Any]:
    """Validate and return one encrypted portable brain record envelope."""
    _reject_plaintext_keys(envelope)
    envelope = _expect_object(envelope, "envelope")
    _assert_exact_keys(envelope, ENVELOPE_KEYS, "envelope")
    if envelope["version"] != 1:
        raise ContractError("envelope version is unsupported")
    record_id = _expect_text(envelope["record_id"], "record_id")
    if envelope["key_id"] != BRAIN_STORE_KEY_ID:
        raise ContractError("envelope key id is unsupported")
    nonce = _decode_base64url(envelope["nonce"], "nonce")
    if len(nonce) != 12:
        raise ContractError("nonce has invalid length")
    tag = _decode_base64url(envelope["auth_tag"], "auth_tag")
    if len(tag) != 16:
        raise ContractError("auth tag has invalid length")
    ciphertext = _decode_base64url(
        envelope["ciphertext"],
        "ciphertext",
        allow_empty=True,
    )
    _validate_associated_data(envelope["associated_data"], record_id)
    deleted = _validate_conflict(envelope["conflict"])
    if not ciphertext and not deleted:
        raise ContractError("empty ciphertext is only valid for tombstones")
    return envelope


def validate_loopback_http_url(url: Any) -> str:
    text = _expect_text(url, "url")
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ContractError("url must use loopback HTTP")
    host = parsed.hostname
    if host.lower() == "localhost":
        return text
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ContractError("url must use loopback HTTP") from exc
    if not address.is_loopback:
        raise ContractError("url must use loopback HTTP")
    return text


def load_contract(path: str | os.PathLike[str] | None = None) -> Contract:
    raw_path = str(path) if path is not None else os.environ.get(CONTRACT_ENV)
    if not raw_path:
        raise ContractError("contract file is required")
    try:
        raw_contract = Path(raw_path).read_text(encoding="utf-8")
        parsed = json.loads(raw_contract)
    except OSError as exc:
        raise ContractError("contract file could not be read") from exc
    except json.JSONDecodeError as exc:
        raise ContractError("contract JSON is invalid") from exc

    data = _expect_object(parsed, "contract")
    try:
        contract = Contract(
            contract_version=_expect_text(data["contract_version"], "contract_version"),
            brain_store_url=validate_loopback_http_url(data["brain_store_url"]),
            brain_store_bearer=_expect_text(
                data["brain_store_bearer"],
                "credential",
            ),
            brain_store_key_id=_expect_text(data["brain_store_key_id"], "key id"),
            mcp_bearer=_expect_text(data["mcp_bearer"], "MCP credential"),
            test_envelope=validate_envelope(data["test_envelope"]),
            expected=_expect_object(data["expected"], "expected"),
        )
    except KeyError as exc:
        raise ContractError("contract is missing a required field") from exc

    if contract.contract_version != CONTRACT_VERSION:
        raise ContractError("contract version is unsupported")
    if contract.brain_store_key_id != BRAIN_STORE_KEY_ID:
        raise ContractError("contract key id is unsupported")
    if contract.brain_store_bearer == contract.mcp_bearer:
        raise ContractError("credentials must be distinct")
    return contract


def _request_json(
    url: str,
    method: str,
    bearer: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise ContractError("request failed") from exc
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ContractError("response JSON is invalid") from exc
    if not isinstance(decoded, dict):
        raise ContractError("response JSON has invalid shape")
    return status, decoded


def _assert_expected(
    expected: dict[str, Any],
    section: str,
    *,
    method: str,
    status: int,
    body: dict[str, Any],
) -> None:
    section_expected = _expect_object(expected.get(section), "expected response")
    if section_expected.get("method") != method:
        raise ContractError("expected response method mismatch")
    if section_expected.get("status") != status:
        raise ContractError("response status mismatch")
    if section_expected.get("body") != body:
        raise ContractError("response body mismatch")


def run_contract(contract: Contract) -> None:
    status, body = _request_json(
        contract.brain_store_url,
        "GET",
        contract.brain_store_bearer,
    )
    _assert_expected(
        contract.expected,
        "initial_get",
        method="GET",
        status=status,
        body=body,
    )

    status, body = _request_json(
        contract.brain_store_url,
        "POST",
        contract.brain_store_bearer,
        {"envelopes": [contract.test_envelope]},
    )
    _assert_expected(
        contract.expected,
        "post",
        method="POST",
        status=status,
        body=body,
    )

    status, body = _request_json(
        contract.brain_store_url,
        "GET",
        contract.brain_store_bearer,
    )
    _assert_expected(
        contract.expected,
        "final_get",
        method="GET",
        status=status,
        body=body,
    )


def main() -> None:
    try:
        contract = load_contract()
        run_contract(contract)
    except ContractError as exc:
        print(f"Odysseus brain client contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("Odysseus brain client contract OK")


if __name__ == "__main__":
    main()
