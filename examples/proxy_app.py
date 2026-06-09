"""Proxy tool service example."""

from __future__ import annotations

from sea_tools_server_sdk import toolctl


app = toolctl.start(title="proxy-tools", version="0.1.0")
app.register_proxy_tool(
    name="get_video_metadata",
    description="Proxy video metadata requests to the video-tools service.",
    base_url="https://video.example.com",
    path="/tools/get_video_metadata",
    request_schema={
        "type": "object",
        "properties": {
            "video_url": {"type": "string"},
            "video_path": {"type": "string"},
        },
    },
    tags=["video", "proxy"],
)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8081)
