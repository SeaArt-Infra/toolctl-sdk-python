from __future__ import annotations

import json
import time
import unittest

from fastapi.testclient import TestClient

from sea_tools_server_sdk import (
    MACHINE_STATUS_BUSY,
    MACHINE_STATUS_ERROR,
    MACHINE_STATUS_FATAL,
    MACHINE_STATUS_IDLE,
    MACHINE_STATUS_PRE,
    MACHINE_TASK_STATUS_EXCEPT,
    MACHINE_TASK_STATUS_FAIL,
    MACHINE_TASK_STATUS_REPEATED,
    MACHINE_TASK_STATUS_SUCCESS,
    MonitoringConfig,
    PubSubMetricsPublisher,
    ResourceMonitor,
    SystemMetricsCollector,
    toolctl,
)


class ResourceMonitoringTests(unittest.TestCase):
    def test_exports_comfy_agent_status_constants(self) -> None:
        self.assertEqual(MACHINE_STATUS_PRE, -1)
        self.assertEqual(MACHINE_STATUS_IDLE, 0)
        self.assertEqual(MACHINE_STATUS_BUSY, 1)
        self.assertEqual(MACHINE_STATUS_FATAL, 2)
        self.assertEqual(MACHINE_STATUS_ERROR, 3)
        self.assertEqual(MACHINE_TASK_STATUS_SUCCESS, 0)
        self.assertEqual(MACHINE_TASK_STATUS_FAIL, 1)
        self.assertEqual(MACHINE_TASK_STATUS_REPEATED, 2)
        self.assertEqual(MACHINE_TASK_STATUS_EXCEPT, 3)

    def test_collects_resource_metrics_payload(self) -> None:
        collector = SystemMetricsCollector(service_name="test-tool", instance_id="instance-1", labels={"env": "test"})

        payload = collector.collect()

        self.assertEqual(payload["id"], "instance-1")
        self.assertEqual(payload["machine_id"], "instance-1")
        self.assertEqual(payload["status"], 0)
        self.assertEqual(payload["instance_id"], "instance-1")
        self.assertEqual(payload["labels"]["env"], "test")
        self.assertIn("ip", payload)
        self.assertIn("hostname", payload)
        self.assertIn("routes", payload)
        self.assertIn("send_time", payload)
        self.assertIn("cpu", payload)
        self.assertIn("memory", payload)
        self.assertIn("process", payload)

    def test_collects_status_from_provider(self) -> None:
        collector = SystemMetricsCollector(
            service_name="test-tool",
            instance_id="instance-1",
            status_provider=lambda: 1,
        )

        payload = collector.collect()

        self.assertEqual(payload["status"], 1)

    def test_resource_monitor_publishes_json_payload(self) -> None:
        class FakePublisher:
            def __init__(self) -> None:
                self.messages: list[tuple[bytes, dict[str, str] | None]] = []

            def publish(self, data: bytes, attributes: dict[str, str] | None = None, ordering_key: str | None = None):
                self.messages.append((data, attributes))
                return {"ok": True}

        publisher = FakePublisher()
        monitor = ResourceMonitor(
            config=MonitoringConfig(
                service_name="test-tool",
                interval_seconds=0.01,
                instance_id="instance-1",
                publish_immediately=True,
            ),
            publisher=publisher,
        )

        monitor.start()
        deadline = time.time() + 1
        while not publisher.messages and time.time() < deadline:
            time.sleep(0.01)
        monitor.stop()

        self.assertGreaterEqual(len(publisher.messages), 1)
        data, attributes = publisher.messages[0]
        payload = json.loads(data.decode("utf-8"))
        self.assertEqual(payload["category"], "test-tool")
        self.assertEqual(payload["status"], 0)
        self.assertEqual(attributes["event_type"], "machine_status")
        self.assertEqual(attributes["service"], "test-tool")

    def test_pubsub_metrics_publisher_accepts_full_topic_path(self) -> None:
        class FakeClient:
            def topic_path(self, project_id: str, topic: str) -> str:
                return f"projects/{project_id}/topics/{topic}"

        topic = PubSubMetricsPublisher._resolve_topic_path(
            FakeClient(),
            "projects/project-a/topics/tool-metrics",
        )

        self.assertEqual(topic, "projects/project-a/topics/tool-metrics")

    def test_pubsub_metrics_publisher_builds_topic_path_from_project(self) -> None:
        class FakeClient:
            def topic_path(self, project_id: str, topic: str) -> str:
                return f"projects/{project_id}/topics/{topic}"

        topic = PubSubMetricsPublisher._resolve_topic_path(FakeClient(), "tool-metrics", "project-a")

        self.assertEqual(topic, "projects/project-a/topics/tool-metrics")

    def test_pubsub_metrics_publisher_publishes_bytes(self) -> None:
        class FakeFuture:
            def result(self, timeout: float | None = None) -> str:
                return "message-1"

        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, bytes, dict[str, str]]] = []

            def publish(self, topic: str, data: bytes, **kwargs: str) -> FakeFuture:
                self.calls.append((topic, data, kwargs))
                return FakeFuture()

        client = FakeClient()
        publisher = PubSubMetricsPublisher(
            topic="projects/project-a/topics/tool-metrics",
            client=client,
        )

        result = publisher.publish(b"{}", attributes={"event_type": "resource.metrics"})

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, "message-1")
        self.assertEqual(
            client.calls,
            [("projects/project-a/topics/tool-metrics", b"{}", {"event_type": "resource.metrics"})],
        )

    def test_tool_app_lifecycle_starts_resource_monitor(self) -> None:
        payloads: list[dict] = []
        app = toolctl.start(title="lifecycle-tool")
        app.enable_resource_monitoring(
            publish=payloads.append,
            interval_seconds=0.01,
            publish_immediately=True,
        )

        with TestClient(app.fastapi) as client:
            response = client.get("/health")
            deadline = time.time() + 1
            while not payloads and time.time() < deadline:
                time.sleep(0.01)

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["category"], "lifecycle-tool")
        self.assertIn("cpu", payloads[0])

    def test_tool_app_resource_monitor_status_tracks_active_request(self) -> None:
        app = toolctl.start(title="status-tool")
        monitor = app.enable_resource_monitoring(publish=lambda _payload: None)

        self.assertEqual(monitor.collect_once()["status"], 0)
        app._begin_tool_request()
        try:
            self.assertEqual(monitor.collect_once()["status"], 1)
        finally:
            app._end_tool_request()
