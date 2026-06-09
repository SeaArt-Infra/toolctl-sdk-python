from __future__ import annotations

import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .models import EnableResourceMonitoringOptions


MACHINE_STATUS_IDLE = 1
MACHINE_STATUS_BUSY = 2


class ResourceMonitor:
    def __init__(self, service_name: str, opts: EnableResourceMonitoringOptions, status_provider: Callable[[], int]) -> None:
        self.service_name = service_name
        self.opts = opts
        self.status_provider = status_provider
        self.instance_id = opts.instance_id or f"{service_name}-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enabled(self) -> bool:
        return self.opts.enabled

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.enabled() or self.running():
            return
        if self.opts.publish is None:
            raise ValueError("publish callback is required when monitoring is enabled")
        if self.opts.publish_immediately:
            self.publish_once()
        self._thread = threading.Thread(target=self._run, name=f"toolctl-monitor-{self.instance_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def collect_once(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "service_name": self.service_name,
            "machine_id": self.instance_id,
            "ip": _host_ip(),
            "status": self.status_provider(),
            "send_time": int(time.time() * 1000),
            "labels": dict(self.opts.labels),
        }

    def publish_once(self) -> dict[str, Any]:
        payload = self.collect_once()
        if self.opts.publish is not None:
            self.opts.publish(payload)
        return payload

    def _run(self) -> None:
        while not self._stop.wait(self.opts.interval):
            self.publish_once()


def _host_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


@dataclass(slots=True)
class PubSubMetricsPublisherOptions:
    topic: str
    credentials_json: str = ""
    project_id: str = ""
    publish_timeout_seconds: float = 30.0


class PubSubMetricsPublisher:
    def __init__(self, opts: PubSubMetricsPublisherOptions) -> None:
        if not opts.topic:
            raise ValueError("topic is required for PubSubMetricsPublisher")
        if not opts.topic.startswith("projects/") and not opts.project_id:
            raise ValueError("project_id is required when topic is not a full path")
        self.topic_path = opts.topic if opts.topic.startswith("projects/") else f"projects/{opts.project_id}/topics/{opts.topic}"
        self.credentials_json = opts.credentials_json
        self.publish_timeout_seconds = opts.publish_timeout_seconds or 30.0

    def publish(self, data: bytes, attributes: dict[str, str] | None = None, ordering_key: str = "") -> Any:
        raise NotImplementedError(
            "PubSubMetricsPublisher.publish requires Google auth support in the Python runtime; "
            "use EnableResourceMonitoringOptions(publish=...) to plug in your scheduler publisher."
        )
