"""LAN media bridge used while the robot is in manual-control mode.

The UNO Q is the media relay.  The protocol intentionally uses only the
Python standard library plus OpenCV, which is already required by DogVision:

* ``GET /camera.mjpg`` - multipart MJPEG camera feed.
* microphone TCP - ``AUD0`` stream header followed by length-prefixed mono
  PCM16 frames at 24 kHz.
* controller speaker TCP - the laptop sends an ``AUD0`` header and framed
  PCM16 mono audio at 16 kHz.
* robot speaker TCP - the ESP32 connects here and receives the controller's
  framed PCM audio.

The media server drops audio while automatic mode is active.  That keeps the
manual-control surface quiet until the explicit mode transition succeeds.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import cv2

from media_protocol import (
    AUDIO_HEADER,
    AUDIO_MAGIC,
    FRAME_LENGTH,
    read_exact as _read_exact,
    stream_header as _stream_header,
)
MAX_AUDIO_FRAME_BYTES = 256 * 1024


class _CameraHandler(BaseHTTPRequestHandler):
    """Serve the latest frame without buffering old camera frames."""

    server: "_CameraServer"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path.split("?", 1)[0] != "/camera.mjpg":
            self.send_error(HTTPStatus.NOT_FOUND, "Use /camera.mjpg")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        while not self.server.media.stop_event.is_set():
            if not self.server.media.active:
                time.sleep(0.05)
                continue
            frame = self.server.media.frame_provider()
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

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _CameraServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], media: "ManualMediaServer") -> None:
        self.media = media
        super().__init__(address, _CameraHandler)


class ManualMediaServer:
    """Expose camera, microphone, and controller-to-robot speaker streams."""

    def __init__(
        self,
        frame_provider: Callable[[], object | None],
        *,
        host: str | None = None,
        video_port: int | None = None,
        audio_port: int | None = None,
        speaker_port: int | None = None,
        robot_speaker_port: int | None = None,
    ) -> None:
        self.frame_provider = frame_provider
        self.host = host or os.getenv("MANUAL_MEDIA_HOST", "0.0.0.0")
        self.video_port = video_port or int(os.getenv("MANUAL_VIDEO_PORT", "8080"))
        self.audio_port = audio_port or int(os.getenv("MANUAL_AUDIO_PORT", "3334"))
        self.speaker_port = speaker_port or int(
            os.getenv("MANUAL_SPEAKER_PORT", "3335")
        )
        self.robot_speaker_port = robot_speaker_port or int(
            os.getenv("MANUAL_ROBOT_SPEAKER_PORT", "3336")
        )
        self.stop_event = threading.Event()
        self._active = threading.Event()
        self._audio_clients: set[socket.socket] = set()
        self._robot_speaker_clients: set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._servers: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._camera_server: _CameraServer | None = None

    @property
    def active(self) -> bool:
        return self._active.is_set()

    def set_active(self, active: bool) -> None:
        if active:
            self._active.set()
            print(
                "MANUAL_MEDIA_ACTIVE: "
                f"camera=http://<uno-q-ip>:{self.video_port}/camera.mjpg "
                f"audio=tcp://<uno-q-ip>:{self.audio_port} "
                f"speaker=tcp://<uno-q-ip>:{self.speaker_port}"
            )
        else:
            self._active.clear()
            self._close_clients(self._audio_clients)
            print("MANUAL_MEDIA_INACTIVE")

    def start(self) -> None:
        self._camera_server = _CameraServer((self.host, self.video_port), self)
        self._start_thread(self._camera_server.serve_forever, "manual-camera-http")
        self._start_socket_server(self.audio_port, self._serve_audio_clients, "manual-audio")
        self._start_socket_server(
            self.speaker_port,
            self._serve_controller_speaker,
            "manual-controller-speaker",
        )
        self._start_socket_server(
            self.robot_speaker_port,
            self._serve_robot_speaker,
            "manual-robot-speaker",
        )
        print(
            "Manual media listeners: "
            f"camera={self.host}:{self.video_port}, "
            f"microphones={self.host}:{self.audio_port}, "
            f"controller_speaker={self.host}:{self.speaker_port}, "
            f"robot_speaker={self.host}:{self.robot_speaker_port}"
        )

    def _start_thread(self, target: Callable[[], object], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _start_socket_server(
        self,
        port: int,
        handler: Callable[[socket.socket], None],
        name: str,
    ) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, port))
        server.listen(4)
        server.settimeout(1.0)
        self._servers.append(server)

        def accept_loop() -> None:
            while not self.stop_event.is_set():
                try:
                    connection, address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                connection.settimeout(10.0)
                print(f"Manual media client connected: {name} {address[0]}:{address[1]}")
                self._start_thread(
                    lambda conn=connection: self._run_client(handler, conn),
                    f"{name}-client",
                )

        self._start_thread(accept_loop, f"{name}-accept")

    def _run_client(
        self, handler: Callable[[socket.socket], None], connection: socket.socket
    ) -> None:
        try:
            handler(connection)
        except (ConnectionError, OSError, ValueError) as error:
            if not self.stop_event.is_set():
                print(f"Manual media client stopped: {error}")
        finally:
            with self._clients_lock:
                self._audio_clients.discard(connection)
                self._robot_speaker_clients.discard(connection)
            try:
                connection.close()
            except OSError:
                pass

    def _serve_audio_clients(self, connection: socket.socket) -> None:
        connection.settimeout(None)
        with self._clients_lock:
            self._audio_clients.add(connection)
        connection.sendall(_stream_header(24000, 1))
        # Audio is pushed by publish_audio_frame.  Keep this socket alive until
        # the laptop disconnects; a one-byte read detects that without polling.
        while not self.stop_event.is_set():
            if not connection.recv(1):
                return

    def publish_audio_frame(self, frame: bytes) -> None:
        """Broadcast one mono PCM16 microphone frame."""
        if not self.active or not frame:
            return
        self._broadcast(
            self._audio_clients,
            FRAME_LENGTH.pack(len(frame)) + frame,
        )

    def _serve_controller_speaker(self, connection: socket.socket) -> None:
        connection.settimeout(10.0)
        self._read_and_validate_header(connection, 16000, 1)
        connection.settimeout(None)
        while not self.stop_event.is_set():
            header = _read_exact(connection, FRAME_LENGTH.size)
            frame_size = FRAME_LENGTH.unpack(header)[0]
            if frame_size == 0 or frame_size > MAX_AUDIO_FRAME_BYTES:
                raise ValueError(f"invalid speaker frame size: {frame_size}")
            frame = _read_exact(connection, frame_size)
            if self.active:
                self._send_to_robot_speakers(FRAME_LENGTH.pack(frame_size) + frame)

    def _serve_robot_speaker(self, connection: socket.socket) -> None:
        connection.settimeout(None)
        with self._clients_lock:
            self._robot_speaker_clients.add(connection)
        connection.sendall(_stream_header(16000, 1))
        # The robot only receives data.  A blocking read detects disconnects.
        while not self.stop_event.is_set():
            if not connection.recv(1):
                return

    def _send_to_robot_speakers(self, payload: bytes) -> None:
        self._broadcast(self._robot_speaker_clients, payload)

    def _broadcast(self, clients: set[socket.socket], payload: bytes) -> None:
        with self._clients_lock:
            connections = tuple(clients)
        dead: list[socket.socket] = []
        for connection in connections:
            try:
                connection.sendall(payload)
            except OSError:
                dead.append(connection)
        with self._clients_lock:
            for connection in dead:
                clients.discard(connection)

    @staticmethod
    def _read_and_validate_header(
        connection: socket.socket, expected_rate: int, expected_channels: int
    ) -> None:
        if _read_exact(connection, len(AUDIO_MAGIC)) != AUDIO_MAGIC:
            raise ValueError("missing AUD0 audio header")
        sample_rate, channels, bits = AUDIO_HEADER.unpack(
            _read_exact(connection, AUDIO_HEADER.size)
        )
        if (sample_rate, channels, bits) != (expected_rate, expected_channels, 16):
            raise ValueError(
                "expected PCM16 "
                f"{expected_channels}ch/{expected_rate}Hz, got {channels}ch/{sample_rate}Hz/{bits}bit"
            )

    @staticmethod
    def _close_clients(clients: set[socket.socket]) -> None:
        with_clients = tuple(clients)
        clients.clear()
        for connection in with_clients:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def stop(self) -> None:
        self.stop_event.set()
        self._active.clear()
        if self._camera_server is not None:
            self._camera_server.shutdown()
            self._camera_server.server_close()
        for server in self._servers:
            try:
                server.close()
            except OSError:
                pass
        with self._clients_lock:
            audio_clients = set(self._audio_clients)
            robot_clients = set(self._robot_speaker_clients)
            self._audio_clients.clear()
            self._robot_speaker_clients.clear()
        self._close_clients(audio_clients)
        self._close_clients(robot_clients)
        for thread in self._threads:
            thread.join(timeout=0.5)
