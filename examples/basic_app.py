"""Basic code-first tool service example."""

from __future__ import annotations

from sea_tools_server_sdk import toolctl


app = toolctl.start(title="basic-tools", version="0.1.0")


@app.tool(
    name="ping",
    description="Return the submitted message.",
    request_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
        },
        "required": ["message"],
    },
    tags=["demo"],
)
async def ping(payload: dict) -> dict:
    return {"ok": True, "message": payload["message"]}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080)
