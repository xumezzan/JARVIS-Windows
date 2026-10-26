"""Bounded, reviewed MCP configuration and a deliberately small JSON Schema subset."""

import json
import math
import re
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, field_validator, model_validator

from jarvis.mcp.names import capability_name
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy, origin
from jarvis.tools.base import ToolError, ToolModel

MAX_WIRE_BYTES = 262144
MAX_ARGUMENT_BYTES = 8000
MAX_RESULT_BYTES = 16000
RemoteName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:/-]{0,79}$")]
Value = str | int | float | bool | None


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def validate_schema(schema: dict[str, JsonValue]) -> None:
    """No code generation, regexes, references, remote schemas or recursive types."""
    if len(encoded(schema)) > 8192 or schema.get("type") != "object":
        raise ValueError("MCP schema must be a small flat object.")
    if set(schema) - {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "title",
        "description",
    }:
        raise ValueError("Unsupported MCP schema keyword.")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if (
        not isinstance(properties, dict)
        or len(properties) > 16
        or not isinstance(required, list)
        or any(not isinstance(item, str) or item not in properties for item in required)
        or len(set(str(item) for item in required)) != len(required)
        or schema.get("additionalProperties", False) is not False
    ):
        raise ValueError("MCP arguments must have explicit field names.")
    for name, rule in properties.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) or not isinstance(rule, dict):
            raise ValueError("Invalid MCP field.")
        if set(rule) - {
            "type",
            "title",
            "description",
            "enum",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
        } or rule.get("type") not in ("string", "integer", "number", "boolean", "null"):
            raise ValueError("Only primitive MCP fields are supported.")
        if any(not isinstance(rule[key], str) for key in ("title", "description") if key in rule):
            raise ValueError("Invalid schema text.")
        for key in ("minLength", "maxLength"):
            bound = rule.get(key)
            if key in rule and (
                not isinstance(bound, int) or isinstance(bound, bool) or not 0 <= bound <= 2000
            ):
                raise ValueError("Invalid string bound.")
        for key in ("minimum", "maximum"):
            number = rule.get(key)
            if key in rule and (
                not isinstance(number, (int, float))
                or isinstance(number, bool)
                or not math.isfinite(number)
            ):
                raise ValueError("Invalid number bound.")
        if "enum" in rule and (
            not isinstance(rule["enum"], list)
            or not 1 <= len(rule["enum"]) <= 32
            or any(isinstance(item, (list, dict)) for item in rule["enum"])
        ):
            raise ValueError("Invalid enumeration.")


class McpToolDefinition(ToolModel):
    name: RemoteName
    description: str = Field(default="", max_length=500)
    input_schema: dict[str, JsonValue]

    @field_validator("input_schema")
    @classmethod
    def checked(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        validate_schema(value)
        return value


def manifest_digest(config: "McpServerConfig", tools: tuple[McpToolDefinition, ...]) -> str:
    return sha256(
        encoded(
            {
                "server_id": config.server_id,
                "transport": config.transport,
                "command": config.command,
                "url": config.url,
                "allowed_tools": config.allowed_tools,
                "tools": [
                    tool.model_dump(mode="json")
                    for tool in sorted(tools, key=lambda item: item.name)
                ],
            }
        )
    ).hexdigest()


class McpServerConfig(ToolModel):
    server_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str = Field(min_length=1, max_length=100)
    transport: Literal["stdio", "http"]
    command: tuple[str, ...] = Field(default=(), max_length=16, repr=False)
    url: str | None = Field(default=None, max_length=2048, repr=False)
    allowed_tools: tuple[RemoteName, ...] = Field(default=(), max_length=24)
    default_risk: Literal["SAFE", "CONFIRM"] = "CONFIRM"
    enabled: bool = True
    timeout_seconds: int = Field(default=20, ge=1, le=30)
    max_response_bytes: int = Field(default=MAX_WIRE_BYTES, ge=1024, le=MAX_WIRE_BYTES)
    reviewed_tools: tuple[McpToolDefinition, ...] = Field(default=(), max_length=32, repr=False)
    reviewed_digest: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")

    @model_validator(mode="after")
    def valid_configuration(self) -> "McpServerConfig":
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("Duplicate allowed MCP tool.")
        if self.transport == "stdio":
            if self.url is not None or not self.command or not Path(self.command[0]).is_absolute():
                raise ValueError("Stdio requires a fixed absolute executable and argv.")
            if any(
                not value or len(value) > 2048 or any(ord(c) < 32 for c in value)
                for value in self.command
            ):
                raise ValueError("Invalid MCP command.")
            executable = Path(self.command[0]).name.lower()
            if executable.removesuffix(".exe") in {
                "sh",
                "bash",
                "zsh",
                "cmd",
                "powershell",
                "pwsh",
                "wscript",
                "cscript",
            } or executable.endswith((".cmd", ".bat", ".ps1")):
                raise ValueError("Shell hosts are not MCP executables.")
        elif self.command or not self.url:
            raise ValueError("HTTP requires one fixed HTTPS URL.")
        else:
            policy = NetworkPolicy(mode=BrowserMode.LIVE)
            try:
                object.__setattr__(self, "url", policy.validate(self.url))
            except ToolError:
                raise ValueError("MCP requires public HTTPS.") from None
            parts = urlsplit(self.url)
            if parts.query or parts.fragment or parts.port not in (None, 443):
                raise ValueError("MCP URL has no query, fragment or custom port.")
            if (
                not re.fullmatch(r"/[A-Za-z0-9/._~-]*", parts.path or "/")
                or any(segment in (".", "..") for segment in parts.path.split("/"))
                or "//" in parts.path
            ):
                raise ValueError("MCP endpoint must be a literal absolute path.")
            if origin(self.url).endswith(":443"):
                raise ValueError("Use the canonical HTTPS origin.")
        names = [tool.name for tool in self.reviewed_tools]
        mapped = [capability_name(name) for name in names]
        if len(set(names)) != len(names) or len(set(mapped)) != len(mapped):
            raise ValueError("Ambiguous MCP tool names.")
        if any(len("mcp__" + self.server_id + "__" + name) > 64 for name in mapped):
            raise ValueError("MCP function name exceeds the provider limit.")
        return self

    @property
    def reviewed(self) -> bool:
        return (
            bool(self.reviewed_digest)
            and self.reviewed_digest == manifest_digest(self, self.reviewed_tools)
            and set(self.allowed_tools) <= {tool.name for tool in self.reviewed_tools}
        )

    def with_review(self, tools: tuple[McpToolDefinition, ...]) -> "McpServerConfig":
        candidate = type(self).model_validate(self.model_dump() | {"reviewed_tools": tools})
        if not set(candidate.allowed_tools) <= {tool.name for tool in tools}:
            raise ValueError("Configured tool is missing from the manifest.")
        return type(self).model_validate(
            candidate.model_dump() | {"reviewed_digest": manifest_digest(candidate, tools)}
        )


class McpArguments(ToolModel):
    arguments: dict[str, Value] = Field(default_factory=dict, max_length=16)

    @field_validator("arguments")
    @classmethod
    def bounded(cls, value: dict[str, Value]) -> dict[str, Value]:
        if len(encoded(value)) > MAX_ARGUMENT_BYTES:
            raise ValueError("MCP arguments exceed their UTF-8 budget.")
        if any(
            isinstance(item, str) and (len(item) > 2000 or "\x00" in item)
            for item in value.values()
        ):
            raise ValueError("MCP argument is too long.")
        return value


def validate_arguments(tool: McpToolDefinition, args: McpArguments) -> None:
    properties = tool.input_schema.get("properties", {})
    assert isinstance(properties, dict)
    required = tool.input_schema.get("required", [])
    assert isinstance(required, list)
    if (
        set(args.arguments) - properties.keys()
        or not {str(name) for name in required} <= args.arguments.keys()
    ):
        raise ValueError("MCP fields differ from the reviewed schema.")
    types: dict[str, tuple[type[object], ...]] = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "null": (type(None),),
    }
    for name, value in args.arguments.items():
        rule = properties[name]
        assert isinstance(rule, dict)
        if type(value) not in types[str(rule["type"])]:
            raise ValueError("MCP field type mismatch.")
        enumeration = rule.get("enum")
        if isinstance(enumeration, list) and not any(
            type(value) is type(item) and value == item for item in enumeration
        ):
            raise ValueError("MCP field is outside its enumeration.")
        if isinstance(value, str):
            minimum_length, maximum_length = rule.get("minLength", 0), rule.get("maxLength", 2000)
            assert isinstance(minimum_length, int) and isinstance(maximum_length, int)
            if not minimum_length <= len(value) <= maximum_length:
                raise ValueError("MCP string bound.")
        if type(value) in (int, float):
            assert isinstance(value, (int, float))
            minimum, maximum = rule.get("minimum"), rule.get("maximum")
            if (
                isinstance(minimum, (int, float))
                and value < minimum
                or isinstance(maximum, (int, float))
                and value > maximum
            ):
                raise ValueError("MCP number bound.")


class McpResult(ToolModel):
    tool: RemoteName
    state: Literal["answered"] = "answered"
    data: str = Field(max_length=MAX_RESULT_BYTES, repr=False)
    effect_verified: Literal[False] = False

    @field_validator("data")
    @classmethod
    def byte_bound(cls, value: str) -> str:
        if len(value.encode()) > MAX_RESULT_BYTES:
            raise ValueError("MCP result exceeds its UTF-8 budget.")
        return value
