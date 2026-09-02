# Máy chủ mạng nội bộ cung cấp luồng webcam và dashboard điều khiển RoboDog.
# File này chỉ phục vụ HTTP; việc quyết định chuyển động vẫn nằm trong main.py.

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


# Thời gian mặc định một trình duyệt được giữ quyền dashboard.
DASHBOARD_LEASE_S = 15.0
# Đọc giao diện tĩnh tương đối với thư mục gốc của dự án.
DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"

# Các kiểu callback để main.py gắn logic robot vào máy chủ mà không tạo vòng
# import phụ thuộc giữa hai file.
DashboardCommandHandler = Callable[[str], dict[str, Any]]
DashboardModeHandler = Callable[[str], dict[str, Any]]
DashboardCameraRestartHandler = Callable[[], dict[str, Any]]
DashboardStatusProvider = Callable[[], dict[str, Any]]


class _CameraHandler(BaseHTTPRequestHandler):
    # Xử lý các yêu cầu cho trang dashboard, API và luồng ảnh mới nhất.

    server: "_CameraServer"

    @property
    def client_host(self) -> str:
        # Địa chỉ nguồn trong mạng LAN được dùng làm danh tính thiết bị giữ lease.

        return str(self.client_address[0])

    def do_GET(self) -> None:  # noqa: N802 - tên bắt buộc của BaseHTTPRequestHandler
        # Bỏ query string để cùng một route xử lý cả /api/status?no-cache=1.
        path = self.path.split("?", 1)[0]
        # Các route này chỉ được xem bởi thiết bị đang giữ quyền dashboard.
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
            # Trang HTML nhận thông báo dễ đọc; API/video nhận lỗi JSON 409.
            if path in {"/", "/dashboard", "/dashboard.html"}:
                self.server.video.serve_dashboard_busy(self)
            else:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    self.server.video.dashboard_busy_payload(),
                )
            return

        # Chọn bộ phục vụ tương ứng với route đã được xác thực.
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

    def do_POST(self) -> None:  # noqa: N802 - tên bắt buộc của BaseHTTPRequestHandler
        # POST dùng cho nhả lease, đổi chế độ, gửi lệnh và restart camera.
        path = self.path.split("?", 1)[0]
        if path == "/api/release":
            # Chỉ chủ sở hữu hiện tại mới có thể tự nhả lease của mình.
            released = self.server.video.release_dashboard(self.client_host)
            self._send_json(
                HTTPStatus.OK,
                {"status": "released" if released else "ignored"},
            )
            return
        if path not in {"/api/command", "/api/mode", "/api/camera/restart"}:
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown dashboard endpoint")
            return

        # Không cho thiết bị khác gửi lệnh dù nó biết URL API.
        if not self.server.video.authorize_dashboard(self.client_host):
            self._send_json(
                HTTPStatus.CONFLICT,
                self.server.video.dashboard_busy_payload(),
            )
            return

        if path == "/api/camera/restart":
            # Restart không cần body JSON; bước xác thực phía trên vẫn bắt buộc.
            result = self.server.video.handle_camera_restart()
        else:
            try:
                # Đọc và kiểm tra body trước khi chuyển dữ liệu cho main.py.
                payload = self._read_json()
            except ValueError as error:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "message": str(error)},
                )
                return

            # /api/command lấy khóa command, còn /api/mode lấy khóa mode.
            value = payload.get("command" if path == "/api/command" else "mode")
            if not isinstance(value, str):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "message": "Expected a string command or mode"},
                )
                return

            # Callback trả về status; lớp HTTP chỉ dịch status đó thành mã HTTP.
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
        # Giới hạn body để một request lỗi không chiếm quá nhiều bộ nhớ.
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length <= 0 or length > 16 * 1024:
            raise ValueError("Request body must be between 1 and 16384 bytes")
        # Đọc đúng số byte đã khai báo; thiếu byte nghĩa là request chưa hoàn tất.
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Incomplete request body")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        # Các callback chỉ nhận object JSON để có thể lấy command/mode an toàn.
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        # Giữ nguyên Unicode tiếng Việt trong JSON và gửi các header cần thiết.
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        # Hàm dùng chung để trả HTML hoặc dữ liệu nhị phân với đúng Content-Length.
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_camera_stream(self) -> None:
        # MJPEG là nhiều ảnh JPEG nối tiếp trong một response multipart duy nhất.
        self.send_response(HTTPStatus.OK)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while not self.server.video.stop_event.is_set():
                # frame_provider trả về bản sao khung hình mới nhất hoặc None.
                frame = self.server.video.frame_provider()
                if frame is None:
                    time.sleep(0.02)
                    continue
                ok, encoded = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
                )
                if not ok:
                    # Bỏ qua khung không mã hóa được và thử khung kế tiếp.
                    continue
                payload = encoded.tobytes()
                # Gửi boundary, header kích thước rồi nội dung JPEG cho trình duyệt.
                self.wfile.write(
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
                    + payload
                    + b"\r\n"
                )
                self.wfile.flush()
                # Nhường CPU và giới hạn tốc độ gửi khoảng 30 ms mỗi khung.
                time.sleep(0.03)
        except OSError:
            # Người dùng đóng tab hoặc mạng ngắt; không cần in traceback dài.
            return

    def log_message(self, _format: str, *_args: object) -> None:
        # Tắt log mặc định của BaseHTTPRequestHandler để terminal chỉ hiện log hữu ích.
        return


class _CameraServer(ThreadingHTTPServer):
    # Server đa luồng để một request video dài không chặn API điều khiển.
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], video: "ManualVideoServer") -> None:
        # Giữ tham chiếu tới lớp bọc video để handler truy cập frame và callback.
        self.video = video
        super().__init__(address, _CameraHandler)


class ManualVideoServer:
    # Cung cấp webcam và dashboard qua giao diện mạng LAN của UNO Q.

    def __init__(
        self,
        frame_provider: Callable[[], object | None],
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        # frame_provider thường là CameraStream.read, trả về khung hình mới nhất.
        self.frame_provider = frame_provider
        # Host/port có thể đổi bằng biến môi trường để phục vụ các cấu hình khác.
        self.host = host or os.getenv("MANUAL_VIDEO_HOST", "0.0.0.0")
        self.port = (
            port if port is not None else int(os.getenv("MANUAL_VIDEO_PORT", "8080"))
        )
        # stop_event kết thúc server; _active báo Python đang cho phép video manual.
        self.stop_event = threading.Event()
        self._active = threading.Event()
        # Lock bảo vệ chủ sở hữu và thời điểm gia hạn lease giữa nhiều request.
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
        # Callback được gắn sau khi main.py tạo xong logic robot.
        self._server: _CameraServer | None = None
        self._thread: threading.Thread | None = None
        self._dashboard_command_handler: DashboardCommandHandler | None = None
        self._dashboard_mode_handler: DashboardModeHandler | None = None
        self._dashboard_camera_restart_handler: DashboardCameraRestartHandler | None = None
        self._dashboard_status_provider: DashboardStatusProvider | None = None
        # Đọc HTML một lần, tránh đọc đĩa cho mỗi lần trình duyệt tải trang.
        self._dashboard_html = self._load_dashboard_html()

    @staticmethod
    def _load_dashboard_html() -> bytes | None:
        # Trả về bytes để có thể gửi nguyên file và giữ đúng UTF-8 của giao diện.
        try:
            return DASHBOARD_PATH.read_bytes()
        except OSError as error:
            print(f"Dashboard unavailable: could not read {DASHBOARD_PATH}: {error}")
            return None

    @property
    def active(self) -> bool:
        # Event được dùng như cờ thread-safe cho status endpoint.
        return self._active.is_set()

    def set_active(self, active: bool) -> None:
        # main.py gọi hàm này khi vào/rời STATE_MANUAL.
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
        # Gắn callback sau khi server đã tạo để tránh import vòng với main.py.

        self._dashboard_command_handler = command_handler
        self._dashboard_mode_handler = mode_handler
        self._dashboard_camera_restart_handler = camera_restart_handler
        self._dashboard_status_provider = status_provider

    def serve_dashboard(self, handler: _CameraHandler) -> None:
        # Gửi file HTML đã đọc sẵn; nếu thiếu file thì báo lỗi máy chủ.
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
        # Trả trang giải thích khi một địa chỉ LAN khác đang giữ lease.

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
        # Payload dùng chung cho các endpoint JSON bị từ chối vì lease bận.
        return {
            "status": "rejected",
            "code": "dashboard_busy",
            "message": "Dashboard is currently in use by another device.",
        }

    def authorize_dashboard(self, client_host: str) -> bool:
        # Cấp một lease duy nhất và gia hạn nó mỗi khi đúng client truy cập.

        now = time.monotonic()
        acquired = False
        with self._dashboard_access_lock:
            # Lease hết hạn khi chưa có chủ hoặc quá lâu không thấy request.
            expired = (
                self._dashboard_owner is None
                or now - self._dashboard_owner_seen > self.dashboard_lease_s
            )
            if expired:
                self._dashboard_owner = client_host
                acquired = True
            if self._dashboard_owner != client_host:
                # Không để thiết bị thứ hai xem feed hoặc gửi lệnh.
                return False
            self._dashboard_owner_seen = now
        if acquired:
            print("DASHBOARD_CLIENT_ACQUIRED")
        return True

    def release_dashboard(self, client_host: str) -> bool:
        # Chỉ client đang giữ lease mới có thể giải phóng nó.

        with self._dashboard_access_lock:
            if self._dashboard_owner != client_host:
                return False
            self._dashboard_owner = None
            self._dashboard_owner_seen = 0.0
        print("DASHBOARD_CLIENT_RELEASED")
        return True

    def serve_status(self, handler: _CameraHandler) -> None:
        # Tạo status cơ bản rồi ghép thêm dữ liệu robot do main.py cung cấp.
        status: dict[str, Any] = {
            "media_active": self.active,
            "camera_live": not self.stop_event.is_set(),
            "media": {"camera": "/camera.mjpg"},
        }
        if self._dashboard_status_provider is not None:
            try:
                status.update(self._dashboard_status_provider())
            except Exception as error:  # Giữ endpoint status hoạt động dù callback lỗi.
                status.update({"status": "error", "status_error": str(error)})
        body = json.dumps(status, ensure_ascii=False).encode("utf-8")
        handler._send_bytes(
            HTTPStatus.OK,
            "application/json; charset=utf-8",
            body,
        )

    def serve_camera(self, handler: _CameraHandler) -> None:
        # Handler riêng giữ response mở và liên tục đẩy các JPEG mới.
        handler._serve_camera_stream()

    def handle_dashboard_command(self, command: str) -> dict[str, Any]:
        # Chuyển lệnh web sang callback main.py và chuẩn hóa lỗi thành JSON.
        if self._dashboard_command_handler is None:
            return {"status": "error", "message": "Dashboard control is not initialized"}
        try:
            return self._dashboard_command_handler(command)
        except Exception as error:
            print(f"Dashboard command failed: {error}")
            return {"status": "error", "message": str(error)}

    def handle_dashboard_mode(self, mode: str) -> dict[str, Any]:
        # Chuyển yêu cầu Manual/Automatic sang callback quản lý máy trạng thái.
        if self._dashboard_mode_handler is None:
            return {"status": "error", "message": "Dashboard control is not initialized"}
        try:
            return self._dashboard_mode_handler(mode)
        except Exception as error:
            print(f"Dashboard mode change failed: {error}")
            return {"status": "error", "message": str(error)}

    def handle_camera_restart(self) -> dict[str, Any]:
        # Restart được thực hiện không đồng bộ bởi CameraStream trong main.py.
        if self._dashboard_camera_restart_handler is None:
            return {"status": "error", "message": "Camera restart is not initialized"}
        try:
            return self._dashboard_camera_restart_handler()
        except Exception as error:
            print(f"Camera restart failed: {error}")
            return {"status": "error", "message": str(error)}

    def start(self) -> None:
        # Bind socket rồi chạy serve_forever ở thread nền để App vẫn chạy main_loop.
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
        # Đánh thức mọi vòng stream, xóa lease và đóng socket/server một cách có thứ tự.
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
