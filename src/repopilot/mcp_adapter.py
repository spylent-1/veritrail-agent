"""Explicit allow-lists and schema validation for external tools."""

import json
import re

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from mcp import types

from repopilot.config import StrictModel
from repopilot.errors import RepoPilotError
from repopilot.mcp_connection import McpConnection
from repopilot.models import JsonObject
from repopilot.safety import PreparedAction
from repopilot.tools import ToolDefinition, ToolOutput, ToolRegistry
from repopilot.workspace import digest

KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "title",
    "description",
    "default",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "anyOf",
    "oneOf",
    "$schema",
}


def model_tool_name(server: str, tool: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}_{tool}")[:42]
    return f"mcp_{slug}_{digest(f'{server}:{tool}'.encode())[:8]}"


def supported_schema(schema: JsonObject) -> Draft202012Validator:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise RepoPilotError("MCP_SCHEMA", "MCP input schema is invalid.") from None
    if schema.get("type") != "object" or len(json.dumps(schema).encode()) > 12000:
        raise RepoPilotError("MCP_SCHEMA", "Only bounded object input schemas are supported.")

    def check(node: JsonObject) -> None:
        if not isinstance(node, dict):
            raise RepoPilotError("MCP_SCHEMA", "Boolean schemas are not supported by this adapter.")
        if set(node) - KEYWORDS:
            raise RepoPilotError(
                "MCP_SCHEMA", "MCP schema uses unsupported keywords; not registered."
            )
        for child in node.get("properties", {}).values():
            check(child)
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                check(node[key])
        for key in ("anyOf", "oneOf"):
            for child in node.get(key, []):
                check(child)

    check(schema)
    return Draft202012Validator(schema)


class McpAdapter:
    def __init__(
        self,
        name: str,
        connection: McpConnection,
        registry: ToolRegistry,
        output_bytes: int = 16384,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", name):
            raise RepoPilotError("MCP_NAME", "Server name must be a short ASCII identifier.")
        self.name = name
        self.connection = connection
        self.registry = registry
        self.output_bytes = output_bytes

    def start_action(self) -> PreparedAction:
        async def commit() -> ToolOutput:
            discovered = await self.connection.open()
            allowed = self.connection.settings.tools
            definitions = [self._definition(tool) for tool in discovered if tool.name in allowed]
            if set(allowed) - {tool.name for tool in discovered}:
                raise RepoPilotError("MCP_TOOL_MISSING", "An allow-listed tool was not discovered.")
            if len({d.name for d in definitions}) != len(definitions):
                raise RepoPilotError("DUPLICATE_TOOL", "MCP returned duplicate tool names.")
            for definition in definitions:
                self.registry.register(definition)
            return ToolOutput({"server": self.name, "registered": [d.name for d in definitions]})

        settings = self.connection.settings
        intent = {
            "kind": "mcp_start",
            "server": self.name,
            "executable": settings.executable,
            "args": settings.args,
            "allowed_tools": settings.tools,
        }
        return PreparedAction(
            "external",
            f"Start trusted MCP server:\n{intent}\nThis process runs with your OS permissions.",
            intent,
            commit,
            settings.timeout + 5,
        )

    def _definition(self, tool: types.Tool) -> ToolDefinition:
        validator = supported_schema(tool.input_schema)

        def validate(args: JsonObject) -> JsonObject:
            try:
                validator.validate(args)
                json.dumps(args, allow_nan=False)
            except (ValidationError, ValueError):
                raise RepoPilotError(
                    "INVALID_ARGUMENTS", "Arguments violate the external schema."
                ) from None
            return args

        async def prepare(args: JsonObject) -> PreparedAction:
            async def commit() -> ToolOutput:
                result = await self.connection.call(tool.name, args)
                if result.is_error:
                    raise RepoPilotError("MCP_TOOL_ERROR", "External server reported a tool error.")
                texts = [part.text for part in result.content if part.type == "text"]
                ignored = [part.type for part in result.content if part.type != "text"]
                encoded = "\n".join(texts).encode()
                data: JsonObject = {
                    "server": self.name,
                    "tool": tool.name,
                    "text": encoded[: self.output_bytes].decode("utf-8", errors="ignore"),
                    "ignored_content_types": ignored,
                }
                truncated = len(encoded) > self.output_bytes
                if result.structured_content is not None:
                    if len(json.dumps(result.structured_content).encode()) <= self.output_bytes:
                        data["structured"] = result.structured_content
                    else:
                        data["structured_omitted"] = True
                        truncated = True
                return ToolOutput(data, truncated=truncated)

            return PreparedAction(
                "external",
                f"Invoke {self.name}:{tool.name}\nArguments: {args}",
                {"kind": "mcp_call", "server": self.name, "tool": tool.name, "arguments": args},
                commit,
                self.connection.settings.timeout + 1,
            )

        async def no_bypass(args: JsonObject) -> ToolOutput:
            raise RepoPilotError("UNSAFE_TOOL", "External tools require host approval.")

        return ToolDefinition(
            model_tool_name(self.name, tool.name),
            f"External tool {self.name}:{tool.name}. {tool.description or ''}",
            StrictModel,
            no_bypass,
            "external",
            prepare,
            tool.input_schema,
            validate,
        )
