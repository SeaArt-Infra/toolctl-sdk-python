from __future__ import annotations

import unittest

from sea_tools_server_sdk import toolctl


class OpenAPIImportTests(unittest.TestCase):
    def test_register_tool_from_openapi(self) -> None:
        app = toolctl.start(title="import-tools", version="0.1.0")
        tool = app.register_tool_from_openapi(
            name="weather_lookup",
            base_url="https://api.example.com",
            spec={
                "openapi": "3.0.0",
                "paths": {
                    "/weather": {
                        "post": {
                            "operationId": "weatherLookup",
                            "summary": "Lookup weather",
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "city": {"type": "string"},
                                            },
                                            "required": ["city"],
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            },
            operation_id="weatherLookup",
        )

        self.assertEqual(tool.name, "weather_lookup")
        self.assertEqual(tool.path, "/tools/weather_lookup")
        self.assertEqual(tool.upstream_path, "/weather")
        self.assertEqual(tool.request_schema["required"], ["city"])


if __name__ == "__main__":
    unittest.main()
