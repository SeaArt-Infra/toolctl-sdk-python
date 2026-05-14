"""Resource monitoring helpers for tool services."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import resource
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

try:
    import psutil
except ImportError:  # pragma: no cover - fallback is exercised in minimal environments
    psutil = None


class MetricsPublisher(Protocol):
    """Minimal publisher contract used by ResourceMonitor."""

    def publish(
        self,
        data: bytes,
        attributes: dict[str, str] | None = None,
        ordering_key: str | None = None,
    ) -> Any:
        """Publish one metrics payload."""


PublishFn = Callable[[dict[str, Any]], Any]
StatusProvider = Callable[[], int | str]

MACHINE_STATUS_PRE = -1
MACHINE_STATUS_IDLE = 0
MACHINE_STATUS_BUSY = 1
MACHINE_STATUS_FATAL = 2
MACHINE_STATUS_ERROR = 3

MACHINE_TASK_STATUS_SUCCESS = 0
MACHINE_TASK_STATUS_FAIL = 1
MACHINE_TASK_STATUS_REPEATED = 2
MACHINE_TASK_STATUS_EXCEPT = 3


@dataclass(slots=True)
class MonitoringConfig:
    """Configuration for periodic resource monitoring."""

    service_name: str
    interval_seconds: float = 5.0
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    labels: dict[str, str] = field(default_factory=dict)
    publish_immediately: bool = False
    status_provider: StatusProvider | None = None


@dataclass(slots=True)
class MetricsPublishResult:
    """Result returned by SDK metrics publishers."""

    message_id: str
    success: bool
    error: str | None = None


class PubSubMetricsPublisher:
    """Google Pub/Sub publisher for resource metrics."""

    def __init__(
        self,
        *,
        topic: str,
        credentials_file: str | None = None,
        project_id: str | None = None,
        publish_timeout_seconds: float | None = 30.0,
        client: Any | None = None,
    ) -> None:
        if not topic:
            raise ValueError("topic is required for Pub/Sub metrics publishing.")

        self.topic = topic
        self.credentials_file = credentials_file
        self.project_id = project_id
        self.publish_timeout_seconds = publish_timeout_seconds
        self._publisher = client or self._create_publisher_client(credentials_file)
        self._topic_path = self._resolve_topic_path(self._publisher, topic, project_id)

    @staticmethod
    def _create_publisher_client(credentials_file: str | None) -> Any:
        try:
            from google.cloud import pubsub_v1
        except ImportError as exc:  # pragma: no cover - depends on optional runtime extra
            raise RuntimeError(
                "google-cloud-pubsub is required for PubSubMetricsPublisher."
            ) from exc

        if credentials_file:
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(credentials_file)
            return pubsub_v1.PublisherClient(credentials=credentials)
        return pubsub_v1.PublisherClient()

    @staticmethod
    def _resolve_topic_path(client: Any, topic: str, project_id: str | None = None) -> str:
        if topic.startswith("projects/"):
            return topic
        if not project_id:
            raise ValueError("project_id is required when Pub/Sub topic is not a full topic path.")
        return client.topic_path(project_id, topic)

    def publish(
        self,
        data: bytes,
        attributes: dict[str, str] | None = None,
        ordering_key: str | None = None,
    ) -> MetricsPublishResult:
        kwargs = dict(attributes or {})
        if ordering_key:
            kwargs["ordering_key"] = ordering_key

        try:
            future = self._publisher.publish(self._topic_path, data, **kwargs)
            message_id = future.result(timeout=self.publish_timeout_seconds)
            return MetricsPublishResult(message_id=str(message_id), success=True)
        except Exception as exc:  # noqa: BLE001
            return MetricsPublishResult(message_id="", success=False, error=str(exc))

    def close(self) -> None:
        close = getattr(self._publisher, "close", None)
        if callable(close):
            close()


class SystemMetricsCollector:
    """Collect host and current-process resource metrics without external deps."""

    def __init__(
        self,
        *,
        service_name: str,
        instance_id: str | None = None,
        labels: dict[str, str] | None = None,
        status_provider: StatusProvider | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.service_name = service_name
        self.instance_id = instance_id or uuid4().hex
        self.labels = dict(labels or {})
        self.status_provider = status_provider
        self._clock = clock
        self._started_at = clock()
        self._last_cpu_totals = self._read_linux_cpu_totals()
        self._last_process_time = time.process_time()
        self._last_wall_time = clock()
        self._process = psutil.Process(os.getpid()) if psutil is not None else None
        if psutil is not None:
            psutil.cpu_percent(interval=None)
        if self._process is not None:
            self._process.cpu_percent(interval=None)

    def collect(self) -> dict[str, Any]:
        """Return a JSON-serializable comfy-agent style heartbeat payload."""

        now = self._clock()
        ip = self._label("ip", "machine_ip", default=self._host_ip())
        machine_id = self._label("id", "machine_id", default=self.instance_id)
        category = self._label("category", default=self.service_name)
        port = self._label("port", default="8080")
        api = self._label("api", "start_api", "task_api", default="tools")
        mode_api = self._label("mode_api", "change_partition_api", default="change_partition")
        action_api = self._label("action_api", default="activity")
        task_url = self._label("task_url", "start_task_url", default=self._route_url(ip, port, api))
        task_express_url = self._label(
            "task_express_url",
            "change_partition_url",
            default=self._route_url(ip, port, mode_api),
        )
        cpu_percent = self._system_cpu_percent()
        process_cpu_percent = self._process_cpu_percent(now)
        memory = self._memory_metrics()
        process_memory = self._process_rss_bytes()
        payload: dict[str, Any] = {
            "id": machine_id,
            "ip": ip,
            "routes": {
                "start_task": task_url,
                "change_partition_url": task_express_url,
                "action_url": self._label("action_url", default=self._route_url(ip, port, action_api)),
            },
            "send_time": int(now * 1000),
            "region": self._label("region"),
            "machine_id": machine_id,
            "status": self._machine_status(),
            "category": category,
            "task_url": task_url,
            "instance_group": self._label("instance_group", default=self.service_name),
            "express": self._label("express", "mix_cluster", "tool", default=self.service_name),
            "task_express_url": task_express_url,
            "cloud": self._label("cloud"),
            "host_id": self._label("host_id"),
            "partition": self._label("partition"),
            "cpu": {
                "percent": cpu_percent,
                "load_avg": self._load_average(),
                "count": os.cpu_count(),
            },
            "memory": memory,
            "process": {
                "pid": os.getpid(),
                "cpu_percent": process_cpu_percent,
                "rss_bytes": process_memory,
                "uptime_seconds": max(0.0, now - self._started_at),
                "thread_count": threading.active_count(),
            },
            "instance_id": self.instance_id,
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
            },
            "hostname": socket.gethostname(),
        }
        if self.labels:
            payload["labels"] = self.labels
        return payload

    def _label(self, *keys: str, default: str = "") -> str:
        for key in keys:
            value = self.labels.get(key)
            if value is not None and value != "":
                return str(value)
        return default

    def _machine_status(self) -> int:
        if self.status_provider is not None:
            try:
                return int(self.status_provider())
            except Exception:  # noqa: BLE001
                return MACHINE_STATUS_IDLE
        raw_status = self.labels.get("status", MACHINE_STATUS_IDLE)
        try:
            return int(raw_status)
        except (TypeError, ValueError):
            return MACHINE_STATUS_IDLE

    @staticmethod
    def _route_url(ip: str, port: str, path: str) -> str:
        clean_path = str(path).strip("/")
        port_segment = f":{port}" if port else ""
        return f"http://{ip}{port_segment}/{clean_path}" if clean_path else f"http://{ip}{port_segment}"

    @staticmethod
    def _host_ip() -> str:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"

    def _system_cpu_percent(self) -> float | None:
        if psutil is not None:
            return round(float(psutil.cpu_percent(interval=None)), 2)

        current = self._read_linux_cpu_totals()
        previous = self._last_cpu_totals
        self._last_cpu_totals = current
        if current is None or previous is None:
            return None

        previous_idle, previous_total = previous
        current_idle, current_total = current
        total_delta = current_total - previous_total
        idle_delta = current_idle - previous_idle
        if total_delta <= 0:
            return None
        return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 2)

    def _process_cpu_percent(self, now: float) -> float | None:
        if self._process is not None:
            try:
                return round(float(self._process.cpu_percent(interval=None)), 2)
            except (psutil.Error, OSError):
                return None

        current_process_time = time.process_time()
        previous_process_time = self._last_process_time
        previous_wall_time = self._last_wall_time
        self._last_process_time = current_process_time
        self._last_wall_time = now

        elapsed = now - previous_wall_time
        if elapsed <= 0:
            return None
        cpu_count = os.cpu_count() or 1
        return round(max(0.0, (current_process_time - previous_process_time) / elapsed / cpu_count * 100.0), 2)

    @staticmethod
    def _read_linux_cpu_totals() -> tuple[int, int] | None:
        stat_path = Path("/proc/stat")
        if not stat_path.exists():
            return None
        try:
            first_line = stat_path.read_text(encoding="utf-8").splitlines()[0]
            parts = [int(value) for value in first_line.split()[1:]]
        except (OSError, ValueError, IndexError):
            return None
        if len(parts) < 4:
            return None

        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        return idle, total

    @staticmethod
    def _memory_metrics() -> dict[str, int | float | None]:
        if psutil is not None:
            memory = psutil.virtual_memory()
            return {
                "total_bytes": int(memory.total),
                "available_bytes": int(memory.available),
                "used_bytes": int(memory.used),
                "used_percent": round(float(memory.percent), 2),
            }

        linux_memory = SystemMetricsCollector._linux_memory_metrics()
        if linux_memory is not None:
            return linux_memory

        total = SystemMetricsCollector._system_total_memory_bytes()
        return {
            "total_bytes": total,
            "available_bytes": None,
            "used_bytes": None,
            "used_percent": None,
        }

    @staticmethod
    def _linux_memory_metrics() -> dict[str, int | float | None] | None:
        meminfo_path = Path("/proc/meminfo")
        if not meminfo_path.exists():
            return None
        values: dict[str, int] = {}
        try:
            for line in meminfo_path.read_text(encoding="utf-8").splitlines():
                key, raw_value = line.split(":", 1)
                amount_kb = int(raw_value.strip().split()[0])
                values[key] = amount_kb * 1024
        except (OSError, ValueError, IndexError):
            return None

        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total is None:
            return None
        used = total - available if available is not None else None
        used_percent = round(used / total * 100.0, 2) if used is not None and total else None
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "used_percent": used_percent,
        }

    @staticmethod
    def _system_total_memory_bytes() -> int | None:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, OSError, ValueError):
            return None
        if not isinstance(pages, int) or not isinstance(page_size, int):
            return None
        return pages * page_size

    @staticmethod
    def _process_rss_bytes() -> int | None:
        if psutil is not None:
            try:
                return int(psutil.Process(os.getpid()).memory_info().rss)
            except (psutil.Error, OSError):
                return None

        try:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except (OSError, ValueError):
            return None
        if platform.system() == "Darwin":
            return int(rss)
        return int(rss) * 1024

    @staticmethod
    def _load_average() -> dict[str, float] | None:
        try:
            one, five, fifteen = os.getloadavg()
        except (AttributeError, OSError):
            return None
        return {"1m": one, "5m": five, "15m": fifteen}


class ResourceMonitor:
    """Periodically collect resource metrics and publish them."""

    def __init__(
        self,
        *,
        config: MonitoringConfig,
        publisher: MetricsPublisher | None = None,
        publish: PublishFn | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if publisher is None and publish is None:
            raise ValueError("Either publisher or publish must be provided.")
        if config.interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero.")

        self.config = config
        self._collector = SystemMetricsCollector(
            service_name=config.service_name,
            instance_id=config.instance_id,
            labels=config.labels,
            status_provider=config.status_provider,
        )
        self._publisher = publisher
        self._publish = publish
        self._logger = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """Return whether the background monitor thread is alive."""

        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the background monitoring thread."""

        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"{self.config.service_name}-resource-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stop the background monitoring thread."""

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None

    def collect_once(self) -> dict[str, Any]:
        """Collect a single metrics payload without publishing it."""

        return self._collector.collect()

    def publish_once(self) -> dict[str, Any]:
        """Collect and publish one metrics payload."""

        payload = self.collect_once()
        self._publish_payload(payload)
        return payload

    def _run(self) -> None:
        if self.config.publish_immediately:
            self._safe_publish_once()

        while not self._stop_event.wait(self.config.interval_seconds):
            self._safe_publish_once()

    def _safe_publish_once(self) -> None:
        try:
            self.publish_once()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("resource monitor publish failed: %s", exc)

    def _publish_payload(self, payload: dict[str, Any]) -> None:
        if self._publish is not None:
            result = self._publish(payload)
            if asyncio.iscoroutine(result):
                asyncio.run(result)
            return

        assert self._publisher is not None
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        attributes = {
            "event_type": "machine_status",
            "service": self.config.service_name,
            "instance_id": self.config.instance_id,
        }
        result = self._publisher.publish(data, attributes=attributes)
        if asyncio.iscoroutine(result):
            asyncio.run(result)


def start_resource_monitor(
    *,
    service_name: str,
    publisher: MetricsPublisher | None = None,
    publish: PublishFn | None = None,
    interval_seconds: float = 5.0,
    instance_id: str | None = None,
    labels: dict[str, str] | None = None,
    publish_immediately: bool = False,
    status_provider: StatusProvider | None = None,
    logger: logging.Logger | None = None,
) -> ResourceMonitor:
    """Create and start a ResourceMonitor."""

    monitor = ResourceMonitor(
        config=MonitoringConfig(
            service_name=service_name,
            interval_seconds=interval_seconds,
            instance_id=instance_id or uuid4().hex,
            labels=dict(labels or {}),
            publish_immediately=publish_immediately,
            status_provider=status_provider,
        ),
        publisher=publisher,
        publish=publish,
        logger=logger,
    )
    monitor.start()
    return monitor
