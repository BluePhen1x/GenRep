#!/usr/bin/env python
"""
ReportAI - Production Startup Script
Starts Celery worker and FastAPI server
Run: python run.py
"""

import atexit
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

# Track processes for graceful cleanup
processes = []


def cleanup():
    """Terminate all spawned child processes on exit."""
    if not processes:
        return
    print("\n🧹 Stopping services...")
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    processes.clear()
    print("✅ Cleanup complete")


atexit.register(cleanup)


def check_redis() -> bool:
    """Check if Redis is running locally."""
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("localhost", 6379))
        s.close()
        return True
    except Exception:
        return False


def start_redis():
    """Attempt to start Redis if available on the system."""
    if check_redis():
        print("✅ Redis is already running")
        return None

    if platform.system() == "Windows":
        redis_cmd = "redis-server.exe"
    elif platform.system() == "Darwin":
        redis_cmd = "redis-server"
    else:
        redis_cmd = "redis-server --daemonize yes"

    try:
        p = subprocess.Popen(
            redis_cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        if check_redis():
            print("✅ Redis started")
            return p
        return p
    except Exception:
        return None


def main():
    print("=" * 60)
    print("📊 ReportAI - Multi-Agent Report Generator")
    print("   Powered by OpenManus Engine")
    print("=" * 60)

    # Locate base and report-generator directory
    root_dir = Path(__file__).resolve().parent
    if (root_dir / "report-generator").exists():
        app_dir = root_dir / "report-generator"
    elif (root_dir / "backend").exists():
        app_dir = root_dir
    else:
        app_dir = root_dir

    # Check config.toml
    config_path = root_dir / "config" / "config.toml"
    if not config_path.exists() and (root_dir.parent / "config" / "config.toml").exists():
        config_path = root_dir.parent / "config" / "config.toml"

    if not config_path.exists():
        print(f"⚠️ OpenManus config not found at: {config_path}")
        print("   Please create config/config.toml and add your API key.")

    # Try starting Redis (optional; Celery falls back gracefully if absent)
    redis_proc = start_redis()
    if redis_proc:
        processes.append(redis_proc)

    # Start Celery worker
    print("\n🚀 Starting Celery worker...")
    celery_cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "backend.celery_app",
        "worker",
        "--loglevel=info",
        "--concurrency=2",
    ]
    celery = subprocess.Popen(celery_cmd, cwd=str(app_dir))
    processes.append(celery)
    time.sleep(3)

    # Start FastAPI server
    print("🚀 Starting FastAPI server...")
    uvicorn_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--reload",
    ]
    uvicorn = subprocess.Popen(uvicorn_cmd, cwd=str(app_dir))
    processes.append(uvicorn)

    print("\n" + "=" * 60)
    print("✅ ReportAI is running!")
    print("🌐 http://localhost:8000")
    print("📝 Press CTRL+C to stop all services")
    print("=" * 60)

    # Signal handlers
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    try:
        uvicorn.wait()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()


if __name__ == "__main__":
    main()
