from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List

from models import MCPClient
from models import config

import sqlite3
import os
import json

# client = OpenAI()
app = FastAPI()
# Setup templates directory
templates = Jinja2Templates(directory="templates")

# Serve static files (if needed)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Load the JSON file
with open("config.json", "r") as f:
    json_data = json.load(f)

# Convert JSON data to MCPClientConfig
mcp_client_config = config.MCPClientConfig(
    mcpServers={
        name: config.MCPServerConfig(**server_config)
        for name, server_config in json_data["mcpServers"].items()
    }
)

def get_license_key():
    conn = sqlite3.connect("mcp_config.db")
    cursor = conn.cursor()

    cursor.execute("SELECT license_key FROM config LIMIT 1")
    row = cursor.fetchone()

    conn.close()
    return row[0] if row else "No License Key"
# MCP Server Model
class MCPServer(BaseModel):
    name: str
    command: str
    args: List[str]
# Response Model
class MCPServerResponse(BaseModel):
    count: int
    servers: List[MCPServer]

llm_client_config = config.LLMClientConfig(
    api_key= get_license_key(),
    base_url="https://api.openai.com/v1",
)

llm_request_config = config.LLMRequestConfig(model=os.environ["MODEL_NAME"])

client = MCPClient(
    mcp_client_config,
    llm_client_config,
    llm_request_config,
)

@app.get("/", response_model=MCPServerResponse)
async def get_home(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})

@app.get("/get_settings", response_model=MCPServerResponse)
async def get_config(request: Request):
    servers_Stdio = [
    {"name": name, "command": server.command, "args": server.args}
    for name, server in mcp_client_config.mcpServers.items()
    if hasattr(server, "command")  # Ensures 'command' exists in the object
    ]

    servers_SSE = [
    {"name": name, "command": server.command, "args": server.args}
    for name, server in mcp_client_config.mcpServers.items()
    if not hasattr(server, "command")  # Ensures 'command' exists in the object
    ]

    servers = list(mcp_client_config.mcpServers.keys())
    return templates.TemplateResponse("settings.html", {"request": request,"totalcount": len(servers), "Stdio_Servers": len(servers_Stdio), "SSE_Servers": len(servers_SSE),"license_key": get_license_key()})

@app.post("/save_config")
async def save_config(request: Request):
    data = await request.json()
    license_key = data.get("licenseKey")

    conn = sqlite3.connect("mcp_config.db")
    cursor = conn.cursor()

    # Insert or update license key
    cursor.execute("DELETE FROM config")  # Ensure only one key exists
    cursor.execute("INSERT INTO config (license_key) VALUES (?)", (license_key,))

    conn.commit()
    conn.close()

    return {"message": "Configuration saved successfully!"}

@app.websocket("/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    servers = list(mcp_client_config.mcpServers.keys())
    print(servers)

    await client.connect_to_multiple_servers(servers)
    try:
        while True:
            data = await websocket.receive_text()
            messages = [
                {"role": "user", "content": str(data)}
            ]
            messages_out = await client.process_messages(messages)
            tool_names = []
            print(messages_out)
            if not messages_out:
                continue
            for msg in messages_out:
                tool_calls = msg.get("tool_calls", [])
                for tool_call in tool_calls:
                    tool_names.append(tool_call["function"]["name"])

            if tool_names:
                await websocket.send_text(f"Tools used: {', '.join(tool_names)}")
            for msg in messages_out:
                print(msg.get("content"))
                assistant_content = msg.get("content")
            await websocket.send_text(assistant_content)
    except WebSocketDisconnect:
        print("Client disconnected")