"""Registering reviewed MCP tools, one registry entry per tool the owner passed.

Two boundaries meet here. The level comes from the review and may only be tightened by the
permission matrix, exactly like every other connector. The argument names come from the
reviewed manifest and are enforced by the policy hook, which is pure and synchronous and
therefore refuses in simulation precisely what it would refuse in execution - so a model
cannot discover an unreviewed field by trying it.
"""

from jarvis.connectors.base import tool_name
from jarvis.connectors.mcp.connector import McpConnector
from jarvis.connectors.mcp.manifest import McpTool
from jarvis.connectors.mcp.models import McpArguments, McpResult
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.tools.base import ExecutionContext, ToolSpec
from jarvis.tools.registry import ToolRegistry

TIMEOUT = 30
CANCELLATION = "Отмена прекращает ожидание ответа; работу на стороне сервера не отзывает."
IDEMPOTENCY = "Автоматического повтора нет; эффект на стороне сервера не проверяется."


def register_mcp(
    registry: ToolRegistry,
    connector: McpConnector,
    matrix: PermissionMatrix | None = None,
) -> None:
    policy = matrix or PermissionMatrix()
    capabilities = {capability.name: capability for capability in connector.capabilities()}

    def entry(tool: McpTool) -> ToolSpec[McpArguments, McpResult]:
        allowed = set(tool.arguments)

        def only_reviewed(args: McpArguments) -> None:
            unknown = set(args.arguments) - allowed
            if unknown:
                raise ValueError("Аргумент вне проверенного набора инструмента.")

        async def check(args: McpArguments, context: ExecutionContext) -> bool:
            return await connector.still_reviewed(context)

        async def run(args: McpArguments, context: ExecutionContext) -> McpResult:
            return await connector.call(tool, args, context)

        async def verify(args: McpArguments, result: McpResult, context: ExecutionContext) -> bool:
            await context.checkpoint()
            # An external effect is not observable from here; a well-formed answer is.
            return isinstance(result, McpResult) and result.tool == tool.remote

        capability = capabilities[tool.name]
        return ToolSpec(
            tool_name(connector.service, tool.name),
            capability.description,
            policy.effective(connector.service, capability),
            McpArguments,
            McpResult,
            check,
            run,
            verify,
            timeout_seconds=TIMEOUT,
            cancellation=CANCELLATION,
            idempotency=IDEMPOTENCY,
            policy=only_reviewed,
        )

    for tool in connector.server.tools:
        registry.register(entry(tool))
