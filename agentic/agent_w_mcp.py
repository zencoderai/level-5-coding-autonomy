import asyncio
import json
import sys
from contextlib import AsyncExitStack
from typing import Any, Dict, List

from anthropic import Anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()


class MCPClient:
    def __init__(self):
        self.sessions: List[ClientSession] = []
        self.exit_stack = AsyncExitStack()
        self.anthropic = Anthropic()

    async def connect_to_servers(self, server_configs: Dict[str, Any]):
        stdio_transports = []
        for server_config in server_configs.values():
            print(server_config)
            server_params = StdioServerParameters(
                command=server_config["command"],
                args=server_config["args"],
                env=None
            )
            stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
            stdio_transports.append(stdio_transport)
        for stdio_transport in stdio_transports:
            read, write = stdio_transport
            session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            self.sessions.append(session)

        for session in self.sessions:
            await session.initialize()
            response = await session.list_tools()
            tools = response.tools
            print("\nConnected to server with tools:", [tool.name for tool in tools])

    async def process_query(self, user_query: str, max_iter: int = 10) -> str:
        messages = [
            {
                "role": "user",
                "content": user_query
            }
        ]
        # load tools
        tool2session = {}
        available_tools = []
        for session in self.sessions:
            response = await session.list_tools()
            tool_names = [tool.name for tool in response.tools]
            for name in tool_names:
                tool2session[name] = session
            available_tools += [{
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema
            } for tool in response.tools]

        response = self.anthropic.messages.create(
            model="claude-3-7-sonnet-20250219",
            max_tokens=1000,
            messages=messages,
            tools=available_tools
        )

        counter = 1
        while counter < max_iter:
            print(f"Loop: {counter}\n\n")

            tool_results = []
    
            for content in response.content:
                if content.type == "text":
                    messages.append({
                        "role": "assistant",
                        "content": content.text
                    })
                    print("Agent thinking: ", content.text)
                elif content.type == "tool_use":
                    tool_name = content.name
                    tool_args = content.input
    
                    result = await tool2session[tool_name].call_tool(tool_name, tool_args)
                    tool_results.append({"call": tool_name, "result": result})
                    print(f"[Calling tool {tool_name} with args {tool_args}]")
                    print(f"Result: {result.content[0].text}")
    
                    if hasattr(content, 'text') and content.text:
                        messages.append({
                            "role": "assistant",
                            "content": content.text
                        })
                    messages.append({
                        "role": "user",
                        "content": result.content
                    })

            response = self.anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=messages,
                tools=available_tools
            )
    
            counter += 1
            if response.stop_reason == "end_turn":
                print("Agent final answer: ", response.content[0].text)
                break

    async def cleanup(self):
        await self.exit_stack.aclose()


async def main():
    server_config = json.load(open(sys.argv[1]))
    client = MCPClient()
    try:
        await client.connect_to_servers(server_config["mcpServers"])
        await client.process_query("Execute file fail.py and see if there are any errors. If there are, fix them")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        await client.cleanup()


if __name__ == "__main__":

    asyncio.run(main())