import asyncio
import os
from datetime import datetime, timezone

import psutil

try:
    import docker as docker_sdk
    _docker_available = True
except Exception:
    _docker_available = False


def _blocking_system_health() -> dict:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=1)
    load = os.getloadavg()
    boot = psutil.boot_time()
    uptime_seconds = datetime.now().timestamp() - boot

    return {
        "cpu_percent": cpu,
        "mem_used_gb": round(mem.used / 1024**3, 2),
        "mem_total_gb": round(mem.total / 1024**3, 2),
        "mem_percent": mem.percent,
        "disk_used_gb": round(disk.used / 1024**3, 2),
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "disk_percent": disk.percent,
        "load_1": round(load[0], 2),
        "load_5": round(load[1], 2),
        "load_15": round(load[2], 2),
        "uptime_hours": round(uptime_seconds / 3600, 1),
        "process_count": len(psutil.pids()),
    }


async def get_system_health() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _blocking_system_health)


def _blocking_docker_containers() -> list[dict]:
    if not _docker_available:
        return []
    client = docker_sdk.from_env()
    containers = []
    for c in client.containers.list(all=True):
        image_tag = c.image.tags[0] if c.image.tags else c.image.short_id
        containers.append({
            "name": c.name,
            "status": c.status,
            "image": image_tag,
            "id": c.short_id,
        })
    return containers


async def get_docker_containers() -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _blocking_docker_containers)


def _blocking_container_logs(container_name: str, tail: int) -> str:
    if not _docker_available:
        return "Docker SDK not available."
    client = docker_sdk.from_env()
    c = client.containers.get(container_name)
    return c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")


async def get_container_logs(container_name: str, tail: int = 50) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _blocking_container_logs(container_name, tail))
