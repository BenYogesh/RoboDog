"""LAN webcam feed and browser dashboard for RoboDog control."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import cv2


DASHBOARD_LEASE_S = 15.0
DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"

DashboardCommandHandler = Callable[[str], dict[str, Any]]
DashboardModeHandler = Callable[[str], dict[str, Any]]
DashboardCameraRestartHandler = Callable[[], dict[str, Any]]
DashboardStatusProvider = Callable[[], dict[str, Any]]


class _CameraHandler(BaseHTTPRequestHandler):
    """Serve the latest frame and the browser control API."""

    server: "_CameraServer"

    @property
    def client_host(self) -> str:
        """Use the LAN source address as the dashboard device identity."""

        return str(self.client_address[0])

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]
        dashboard_path = path in {
            "/",
            "/dashboard",
            "/dashboard.html",
            "/api/status",
            "/camera.mjpg",
        }
        if dashboard_path and not self.server.video.authorize_dashboard(
            self.client_host
        ):
            if path in {"/", "/dashboard", "/dashboard.html"}:
                self.server.video.serve_dashboard_busy(self)
            else:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    self.server.video.dashboard_busy_payload(),
                )
            return

        if path in {"/", "/dashboard", "/dashboard.html"}:
            self.server.video.serve_dashboard(self)
            return
        if path == "/api/status":
            self.server.video.serve_status(self)
            return
        if path == "/camera.mjpg":
            self.server.video.serve_camera(self)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Use / or /camera.mjpg")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]
        if path == "/api/release":
            released = self.server.video.release_dashboard(self.client_host)
            self._send_json(
                HTTPStatus.OK,
                {"status": "released" if released else "ignored"},
            )
            return
        if path not in {"/api/command", "/api/mode", "/api/camera/restart"}:
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard endpoint")
            return

        if not self.server.video.authorize_dashboard(self.client_host):
            self._send_json(
                HTTPStatus.CONFLICT,
                self.server.video.dashboard_busy_payload(),
            )
            return

        if path == "/api/camera/restart":
            # The restart action has no user payload; authorization above still
            # ensures only the current dashboard owner can trigger it.
            result = self.server.video.handle_camera_restart()
        else:
            try:
                payload = self._read_json()
            except ValueError as error:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "message": str(error)},
                )
                return

            value = payload.get("command" if path == "/api/command" else "mode")
            if not isinstance(value, str):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "message": "Expected a string command or mode"},
                )
                return

            result = (
                self.server.video.handle_dashboard_command(value)
                if path == "/api/command"
                else self.server.video.handle_dashboard_mode(value)
            )
        status = result.get("status")
        http_status = (
            HTTPStatus.OK
            if status in {"accepted", "ok"}
            else HTTPStatus.CONFLICT
            if status == "rejected"
            else HTTPStatus.INTERNAL_SERVER_ERROR
        )
        self._send_json(http_status, result)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length <= 0 or length > 16 * 1024:
            raise ValueError("Request body must be between 1 and 16384 bytes")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Incomplete request body")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_camera_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while not self.server.video.stop_event.is_set():
                frame = self.server.video.frame_provider()
                if frame is None:
                    time.sleep(0.02)
                    continue
                ok, encoded = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
                )
                if not ok:
                    continue
                payload = encoded.tobytes()
                self.wfile.write(
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
                    + payload
                    + b"\r\n"
                )
                self.wfile.flush()
                time.sleep(0.03)
        except OSError:
            return

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _CameraServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], video: "ManualVideoServer") -> None:
        self.video = video
        super().__init__(address, _CameraHandler)


class ManualVideoServer:
    """Expose the webcam and dashboard on the UNO Q LAN interface."""

    def __init__(
        self,
        frame_provider: Callable[[], object | None],
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.frame_provider = frame_provider
        self.host = host or os.getenv("MANUAL_VIDEO_HOST", "0.0.0.0")
        self.port = (
            port if port is not None else int(os.getenv("MANUAL_VIDEO_PORT", "8080"))
        )
        self.stop_event = threading.Event()
        self._active = threading.Event()
        self._dashboard_access_lock = threading.Lock()
        self._dashboard_owner: str | None = None
        self._dashboard_owner_seen = 0.0
        try:
            self.dashboard_lease_s = max(
                5.0,
                float(os.getenv("MANUAL_DASHBOARD_LEASE_S", str(DASHBOARD_LEASE_S))),
            )
        except ValueError:
            self.dashboard_lease_s = DASHBOARD_LEASE_S
        self._server: _CameraServer | None = None
        self._thread: threading.Thread | None = None
        self._dashboard_command_handler: DashboardCommandHandler | None = None
        self._dashboard_mode_handler: DashboardModeHandler | None = None
        self._dashboard_camera_restart_handler: DashboardCameraRestartHandler | None = None
        self._dashboard_status_provider: DashboardStatusProvider | None = None
        self._dashboard_html = self._load_dashboard_html()

    @staticmethod
    def _load_dashboard_html() -> bytes | None:
        try:
            return DASHBOARD_PATH.read_bytes()
        except OSError as error:
            print(f"Dashboard unavailable: could not read {DASHBOARD_PATH}: {error}")
            return None

    @property
    def active(self) -> bool:
        return self._active.is_set()

    def set_active(self, active: bool) -> None:
        if active:
            self._active.set()
            print(f"MANUAL_VIDEO_ACTIVE: http://<uno-q-ip>:{self.port}/camera.mjpg")
        else:
            self._active.clear()
            print("MANUAL_VIDEO_INACTIVE")

    def configure_dashboard(
        self,
        *,
        command_handler: DashboardCommandHandler,
        mode_handler: DashboardModeHandler,
        status_provider: DashboardStatusProvider,
        camera_restart_handler: DashboardCameraRestartHandler | None = None,
    ) -> None:
        """Attach robot callbacks after the server has been constructed."""

        self._dashboard_command_handler = command_handler
        self._dashboard_mode_handler = mode_handler
        self._dashboard_camera_restart_handler = camera_restart_handler
        self._dashboard_status_provider = status_provider

    def serve_dashboard(self, handler: _CameraHandler) -> None:
        if self._dashboard_html is None:
            handler.send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Dashboard file unavailable",
            )
            return
        handler._send_bytes(
            HTTPStatus.OK,
            "text/html; charset=utf-8",
            self._dashboard_html,
        )

    def serve_dashboard_busy(self, handler: _CameraHandler) -> None:
        """Explain why another device cannot open the dashboard right now."""

        body = (
            "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>RoboDog dashboard busy</title>"
            "<style>body{margin:2rem;background:#1b0e07;color:#fff1e6;"
            "font:16px system-ui,sans-serif}main{max-width:36rem;margin:auto;"
            "padding:2rem;border:1px solid #7b431f;border-radius:1rem;"
            "background:#29150b}h1{color:#ff9d43}</style><main>"
            "<h1>Dashboard in use</h1>"
            "<p>Another device currently has dashboard access. Try again when it disconnects.</p>"
            "<p>Trang điều khiển đang được thiết bị khác sử dụng. Hãy thử lại sau.</p>"
            "</main></html>"
        ).encode("utf-8")
        handler._send_bytes(HTTPStatus.CONFLICT, "text/html; charset=utf-8", body)

    @staticmethod
    def dashboard_busy_payload() -> dict[str, str]:
        return {
            "status": "rejected",
            "code": "dashboard_busy",
            "message": "Dashboard is currently in use by another device.",
        }

    def authorize_dashboard(self, client_host: str) -> bool:
        """Grant one renewable dashboard lease to a LAN client."""

        now = time.monotonic()
        acquired = False
        with self._dashboard_access_lock:
            expired = (
                self._dashboard_owner is None
                or now - self._dashboard_owner_seen > self.dashboard_lease_s
            )
            if expired:
                self._dashboard_owner = client_host
                acquired = True
            if self._dashboard_owner != client_host:
                return False
            self._dashboard_owner_seen = now
        if acquired:
            print("DASHBOARD_CLIENT_ACQUIRED")
        return True

    def release_dashboard(self, client_host: str) -> bool:
        """Release the lease only when the request comes from its owner."""

        with self._dashboard_access_lock:
            if self._dashboard_owner != client_host:
                return False
            self._dashboard_owner = None
            self._dashboard_owner_seen = 0.0
        print("DASHBOARD_CLIENT_RELEASED")
        return True

    def serve_status(self, handler: _CameraHandler) -> None:
        status: dict[str, Any] = {
            "media_active": self.active,
            "camera_live": not self.stop_event.is_set(),
            "media": {"camera": "/camera.mjpg"},
        }
        if self._dashboard_status_provider is not None:
            try:
                status.update(self._dashboard_status_provider())
            except Exception as error:  # Keep the status endpoint available.
                status.update({"status": "error", "status_error": str(error)})
        body = json.dumps(status, ensure_ascii=False).encode("utf-8")
        handler._send_bytes(
            HTTPStatus.OK,
            "application/json; charset=utf-8",
            body,
        )

    def serve_camera(self, handler: _CameraHandler) -> None:
        handler._serve_camera_stream()

    def handle_dashboard_command(self, command: str) -> dict[str, Any]:
        if self._dashboard_command_handler is None:
            return {"status": "error", "message": "Dashboard control is not initialized"}
        try:
            return self._dashboard_command_handler(command)
        except Exception as error:
            print(f"Dashboard command failed: {error}")
            return {"status": "error", "message": str(error)}

    def handle_dashboard_mode(self, mode: str) -> dict[str, Any]:
        if self._dashboard_mode_handler is None:
            return {"status": "error", "message": "Dashboard control is not initialized"}
        try:
            return self._dashboard_mode_handler(mode)
        except Exception as error:
            print(f"Dashboard mode change failed: {error}")
            return {"status": "error", "message": str(error)}

    def handle_camera_restart(self) -> dict[str, Any]:
        if self._dashboard_camera_restart_handler is None:
            return {"status": "error", "message": "Camera restart is not initialized"}
        try:
            return self._dashboard_camera_restart_handler()
        except Exception as error:
            print(f"Camera restart failed: {error}")
            return {"status": "error", "message": str(error)}

    def start(self) -> None:
        self._server = _CameraServer((self.host, self.port), self)
        self.port = self._server.server_port
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="manual-camera-http",
            daemon=True,
        )
        self._thread.start()
        print(f"Manual webcam/dashboard listener: {self.host}:{self.port}")

    def stop(self) -> None:
        self.stop_event.set()
        self._active.clear()
        with self._dashboard_access_lock:
            self._dashboard_owner = None
            self._dashboard_owner_seen = 0.0
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
