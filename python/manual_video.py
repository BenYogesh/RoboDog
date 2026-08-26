"""Webcam-only MJPEG feed enabled while Bluetooth manual mode is active."""

from __future__ import annotations

import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import cv2


class _CameraHandler(BaseHTTPRequestHandler):
    """Serve the latest frame without buffering stale webcam images."""

    server: "_CameraServer"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path.split("?", 1)[0] != "/camera.mjpg":
            self.send_error(HTTPStatus.NOT_FOUND, "Use /camera.mjpg")
            return
        if not self.server.video.active:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Manual mode is inactive")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while (
                not self.server.video.stop_event.is_set()
                and self.server.video.active
            ):
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
    """Expose the webcam on the LAN only while manual mode is active."""

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
        self._server: _CameraServer | None = None
        self._thread: threading.Thread | None = None

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

    def start(self) -> None:
        self._server = _CameraServer((self.host, self.port), self)
        self.port = self._server.server_port
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="manual-camera-http",
            daemon=True,
        )
        self._thread.start()
        print(f"Manual webcam listener: {self.host}:{self.port}")

    def stop(self) -> None:
        self.stop_event.set()
        self._active.clear()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
