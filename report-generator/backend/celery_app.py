"""
Celery configuration - manages the async task queue.

Uses Redis when available; falls back to the built-in filesystem transport
so the wrapper runs on Windows machines that do not have Redis installed.
"""

import select
import socket

from celery import Celery

from config import config

_BROKER_URL = config.CELERY_BROKER_URL
_BACKEND_URL = config.CELERY_RESULT_BACKEND
_TRANSPORT_OPTIONS = {}
_BROKER_OPTIONS = {}


def _redis_reachable(url: str, timeout: float = 2.0) -> bool:
    """
    Check whether a Redis server is listening at *url* within *timeout* seconds.

    Uses a raw non-blocking socket so the probe always returns within the
    deadline — redis-py's socket_connect_timeout is unreliable on Windows
    loopback because the OS holds SYN_SENT far longer than the library timeout.
    """
    try:
        if not url.startswith("redis://"):
            return False
        hostport = url.split("://", 1)[1].split("/", 1)[0]
        host, _, port_str = hostport.partition(":")
        host = host or "localhost"
        port = int(port_str or 6379)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            sock.connect((host, port))
        except BlockingIOError:
            pass
        except OSError:
            return False

        # Wait up to *timeout* seconds for the connection to complete
        _, writable, _ = select.select([], [sock], [], timeout)
        if not writable:
            return False  # timed out

        # A zero-length getpeername() error means the connect failed
        try:
            sock.getpeername()
            return True
        except OSError:
            return False
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


if config.ALLOW_BROKER_FALLBACK:
    try:
        if not _redis_reachable(config.REDIS_URL):
            raise ConnectionError("Redis port not reachable")
    except Exception as exc:  # noqa: BLE001 - any failure -> fallback
        print(
            f"[celery_app] Redis unavailable ({exc}). "
            "Falling back to filesystem broker."
        )
        # The filesystem transport URL is just "filesystem://" - all folders
        # are passed via transport_options (a Windows path must NOT go in the
        # URL, kombu would misparse the drive letter as the host).
        _DATA_DIR = config.TEMP_DIR / "celery_fs"
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _BROKER_URL = "filesystem://"
        # Celery's file-system backend expects a file:// URL pointing at a
        # directory (it is NOT a registered scheme alias, so no class path).
        # Windows: use 3 slashes (no authority/host component) so kombu does
        # NOT misinterpret the drive letter as a hostname.
        # e.g.  file:///C:/Users/.../backend_results
        _backend_dir = str(_DATA_DIR / "backend_results").replace("\\", "/")
        _BACKEND_URL = "file:///" + _backend_dir
        (_DATA_DIR / "backend_results").mkdir(parents=True, exist_ok=True)
        # kombu's filesystem transport is quirky: producers WRITE to
        # data_folder_out and consumers READ from data_folder_in. Pointing
        # both at the SAME shared folder guarantees the worker sees every
        # message the API publishes.
        _shared = _DATA_DIR / "broker"
        _shared.mkdir(parents=True, exist_ok=True)
        _BROKER_OPTIONS = {
            "data_folder_in": str(_shared),
            "data_folder_out": str(_shared),
            "processed_folder": str(_DATA_DIR / "broker_processed"),
            "store_processed": True,
        }
        _TRANSPORT_OPTIONS = {}

# Create Celery app
celery = Celery(
    "report_generator",
    broker=_BROKER_URL,
    backend=_BACKEND_URL,
    include=["tasks"],
)
celery_app = celery


# Configure Celery
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=config.AGENT_TIMEOUT,
    task_soft_time_limit=config.AGENT_TIMEOUT - 30,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=config.JOB_TTL,
    broker_transport_options=_BROKER_OPTIONS,
    result_backend_transport_options=_TRANSPORT_OPTIONS,
    worker_pool="solo",  # CRITICAL FOR WINDOWS - multiprocessing pool does not work reliably
    worker_concurrency=1,  # SINGLE worker process/thread
)

if __name__ == "__main__":
    celery.start()