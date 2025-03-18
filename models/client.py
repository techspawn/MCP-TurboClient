import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import asdict
from typing import Optional, Dict, List

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)
from openai.types.chat.chat_completion_message_tool_call_param import Function
from openai.types.shared_params.function_definition import FunctionDefinition

from .config import LLMClientConfig, LLMRequestConfig, MCPClientConfig

load_dotenv()


class MCPClient:
    def __init__(
        self,
        mpc_client_config: MCPClientConfig = MCPClientConfig(),
        llm_client_config: LLMClientConfig = LLMClientConfig(),
        llm_request_config: LLMRequestConfig = LLMRequestConfig("gpt-4o"),
    ):
        self.mpc_client_config = mpc_client_config
        self.llm_client_config = llm_client_config
        self.llm_request_config = llm_request_config
        self.llm_client = AsyncOpenAI(**asdict(self.llm_client_config))
        self.sessions: Dict[str, ClientSession] = {}
        self.server_connections = {}
        self.tool_to_server_mapping = {}
        self.exit_stack = AsyncExitStack()
        self.connected_servers = []

        print("CLIENT CREATED")

    async def connect_to_server(self, server_name: str):
        """Connect to an MCP server using its configuration name"""

        if server_name not in self.mpc_client_config.mcpServers:
            raise ValueError(
                f"Server '{server_name}' not found in MCP client configuration"
            )

        # Skip if already connected
        if server_name in self.sessions:
            print(f"Already connected to server '{server_name}'")
            return

        mcp_server_config = self.mpc_client_config.mcpServers[server_name]
        if not mcp_server_config.enabled:
            raise ValueError(f"Server '{server_name}' is disabled")

        stdio_server_params = StdioServerParameters(**asdict(mcp_server_config))

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(stdio_server_params)
        )
        stdio, write = stdio_transport
        session = await self.exit_stack.enter_async_context(
            ClientSession(stdio, write)
        )

        await session.initialize()  # type: ignore
        self.sessions[server_name] = session
        self.server_connections[server_name] = (stdio, write)
        self.connected_servers.append(server_name)

        # List available tools
        response = await session.list_tools()  # type: ignore

        # Map each tool to this server
        for tool in response.tools:
            self.tool_to_server_mapping[tool.name] = server_name

        print(f"CLIENT CONNECTED to {server_name}")
        print(f"AVAILABLE TOOLS from {server_name}:", [tool.name for tool in response.tools])
    async def connect_to_multiple_servers(self, server_names: List[str]):
        """Connect to multiple MCP servers"""
        for server_name in server_names:
            await self.connect_to_server(server_name)
    async def process_tool_call(self, tool_call) -> ChatCompletionToolMessageParam:
        match tool_call.type:
            case "function":
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                # Find the server that owns this tool
                server_name = self.tool_to_server_mapping.get(tool_name)
                if not server_name:
                    raise ValueError(f"Tool '{tool_name}' is not registered with any connected server")

                session = self.sessions.get(server_name)
                if not session:
                    raise ValueError(f"Not connected to server '{server_name}'")

                call_tool_result = await session.call_tool(tool_name, tool_args)  # type: ignore

                if call_tool_result.isError:
                    raise ValueError("An error occurred while calling the tool.")

                results = []
                for result in call_tool_result.content:
                    match result.type:
                        case "text":
                            results.append(result.text)
                        case "image":
                            raise NotImplementedError("Image content is not supported")
                        case "resource":
                            raise NotImplementedError(
                                "Embedded resource is not supported"
                            )
                        case _:
                            raise ValueError(f"Unknown content type: {result.type}")

                return ChatCompletionToolMessageParam(
                    role="tool",
                    content=json.dumps({**tool_args, tool_name: results}),
                    tool_call_id=tool_call.id,
                )

            case _:
                raise ValueError(f"Unknown tool call type: {tool_call.type}")

    async def process_messages(
        self,
        messages: list[ChatCompletionMessageParam],
        llm_request_config: LLMRequestConfig | None = None,
    ) -> list[ChatCompletionMessageParam]:
        # Set up tools and LLM request config
        if not self.sessions:
            raise RuntimeError("Not connected to any server")

        # Collect tools from all connected servers
        all_tools = []
        for server_name, session in self.sessions.items():
            server_tools = (await session.list_tools()).tools
            all_tools.extend(server_tools)

        tools = [
            ChatCompletionToolParam(
                type="function",
                function=FunctionDefinition(
                    name=tool.name,
                    description=tool.description if tool.description else "",
                    parameters=tool.inputSchema,
                ),
            )
            for tool in all_tools
        ]

        llm_request_config = LLMRequestConfig(
            **{
                **asdict(self.llm_request_config),
                **(asdict(llm_request_config) if llm_request_config else {}),
            }
        )

        if not messages:
            return messages

        last_message_role = messages[-1]["role"]

        # Check if we need to get a final response after tool execution
        if last_message_role == "tool":
            print("Getting final response after tool execution")
            response = await self.llm_client.chat.completions.create(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                **asdict(llm_request_config),
            )

            finish_reason = response.choices[0].finish_reason

            if finish_reason == "stop":
                messages.append(
                    ChatCompletionAssistantMessageParam(
                        role="assistant",
                        content=response.choices[0].message.content,
                    )
                )
                return messages
            elif finish_reason == "tool_calls":
                # Handle nested tool calls if needed
                tool_calls = response.choices[0].message.tool_calls
                assert tool_calls is not None
                messages.append(
                    ChatCompletionAssistantMessageParam(
                        role="assistant",
                        content=response.choices[0].message.content or "",
                        tool_calls=[
                            ChatCompletionMessageToolCallParam(
                                id=tool_call.id,
                                function=Function(
                                    arguments=tool_call.function.arguments,
                                    name=tool_call.function.name,
                                ),
                                type=tool_call.type,
                            )
                            for tool_call in tool_calls
                        ],
                    )
                )
                tasks = [
                    asyncio.create_task(self.process_tool_call(tool_call))
                    for tool_call in tool_calls
                ]
                messages.extend(await asyncio.gather(*tasks))
                return await self.process_messages(messages, llm_request_config)
            else:
                # Handle other finish reasons
                print(f"Warning: Unexpected finish reason after tool execution: {finish_reason}")
                return messages

        # Process user messages normally
        elif last_message_role == "user":
            response = await self.llm_client.chat.completions.create(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                **asdict(llm_request_config),
            )
            finish_reason = response.choices[0].finish_reason

            match finish_reason:
                case "stop":
                    messages.append(
                        ChatCompletionAssistantMessageParam(
                            role="assistant",
                            content=response.choices[0].message.content,
                        )
                    )
                    return messages

                case "tool_calls":
                    tool_calls = response.choices[0].message.tool_calls
                    assert tool_calls is not None
                    messages.append(
                        ChatCompletionAssistantMessageParam(
                            role="assistant",
                            content=response.choices[0].message.content or "",
                            tool_calls=[
                                ChatCompletionMessageToolCallParam(
                                    id=tool_call.id,
                                    function=Function(
                                        arguments=tool_call.function.arguments,
                                        name=tool_call.function.name,
                                    ),
                                    type=tool_call.type,
                                )
                                for tool_call in tool_calls
                            ],
                        )
                    )
                    tasks = [
                        asyncio.create_task(self.process_tool_call(tool_call))
                        for tool_call in tool_calls
                    ]
                    messages.extend(await asyncio.gather(*tasks))
                    return await self.process_messages(messages, llm_request_config)

                case "length":
                    raise ValueError("Length limit reached")
                case "content_filter":
                    raise ValueError("Content filter triggered")
                case "function_call":
                    raise NotImplementedError("Function call not implemented")
                case _:
                    raise ValueError(f"Unknown finish reason: {finish_reason}")

        # Handle assistant messages and others
        elif last_message_role == "assistant":
            # Check if this assistant message has tool calls that need processing
            if any(msg.get("tool_calls") for msg in messages if msg.get("role") == "assistant"):
                return messages
            else:
                # If it's a regular assistant message without tool calls, just return it
                return messages
        else:
            # Handle unknown message role
            print(f"Warning: Unhandled message role: {last_message_role}")
            return messages

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()
        self.sessions.clear()
        self.server_connections.clear()
        self.tool_to_server_mapping.clear()
        self.connected_servers.clear()
