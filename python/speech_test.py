"""UNO Q Realtime speech service.

The default input is the USB webcam microphone attached directly to the UNO Q.
The Linux side captures little-endian PCM16 mono frames with ALSA/``arecord``
and forwards them to the OpenAI Realtime WebSocket.  A legacy ESP32 TCP input
can still be selected with ``UNO_Q_SPEECH_INPUT=esp32`` while old firmware is
being retired.  The normal robot application exposes a ``move_robot``
function; the standalone test can instead expose the local ``play_sound``
function.

Run this file directly on the UNO Q Linux side. It intentionally does not
start the existing camera/vision application.
"""

from __future__ import annotations

import argparse
from array import array
import base64
import json
import math
import os
import socket
import sys
import threading
import time
from typing import Any, Callable

import websocket
from arduino.app_utils import Bridge
from local_microphone import (
    AlsaMicrophone,
    AudioCaptureError,
    DEFAULT_FRAME_MS,
    configured_device,
)
from media_protocol import (
    AUDIO_HEADER as MIC_STREAM_HEADER,
    AUDIO_MAGIC as MIC_STREAM_MAGIC,
    FRAME_LENGTH,
    read_exact,
)
from runtime_secrets import load_runtime_secrets


load_runtime_secrets()


MODEL = "gpt-realtime-2.1-mini"
INPUT_SAMPLE_RATE = 24000
DEFAULT_AUDIO_PORT = 3333
MAX_AUDIO_FRAME_BYTES = 64 * 1024
MIC_CHANNELS = 1
MIC_RETRY_DELAY_S = 3.0
ALLOWED_SOUNDS = {"beep", "success", "error"}
MOVEMENT_COMMANDS = {
    "forward": "w",
    "backward": "b",
    "turn_left": "a",
    "turn_right": "d",
    "crab_left": "e",
    "crab_right": "f",
    "pace": "p",
    "stop": "s",
    "hold": "z",
    "sit": "q",
    "prone": "c",
    "stand": "s",
    "wave": "g",
    "bounce": "u",
    "jump": "j",
    "center": "k",
    "manual": "manual",
    "automatic": "automatic",
    # ``chase`` is handled by main.py because it starts the camera/ball state.
    "chase": "chase",
}
# This is a deterministic hardware test. Once the voice turn is detected, make
# the model emit the sound tool call instead of allowing a text-only reply.
# Set SPEECH_TEST_FORCE_TOOL=0 later if unrelated speech must be ignored.
FORCE_SOUND_TOOL = os.getenv("SPEECH_TEST_FORCE_TOOL", "1").lower() not in {
    "0",
    "false",
    "no",
}


SOUND_TEST_INSTRUCTIONS = (
    "You are a small bilingual Vietnamese-English robot speech-command detector. "
    "The user may speak Vietnamese or English. "
    "Do not answer general questions and do not speak. "
    "When the user clearly asks for a sound, call play_sound exactly once. "
    "Map English 'play beep', 'beep', or 'make a beep' and Vietnamese "
    "'phát tiếng bíp', 'kêu bíp', or 'bíp' to sound='beep'. "
    "Map English 'play success' or 'success sound' and Vietnamese "
    "'báo thành công' or 'phát âm thanh thành công' to sound='success'. "
    "Map English 'play error' or 'error sound' and Vietnamese "
    "'báo lỗi' or 'phát âm thanh lỗi' to sound='error'. "
    "Understand natural Vietnamese wording and ignore unrelated speech."
)

ROBOT_INSTRUCTIONS = (
    "You are a bilingual Vietnamese-English robot movement command detector. "
    "The user may speak Vietnamese or English. Do not answer general questions "
    "and do not speak. When the user clearly requests a robot movement, call "
    "move_robot exactly once with the matching command. Do not require or check "
    "a familiar face; speech commands are always allowed. Ignore unrelated speech. "
    "Map forward/walk/go ahead and Vietnamese 'đi tới', 'tiến lên', or 'đi thẳng' "
    "to forward. Map backward/reverse and 'đi lùi' to backward. Map turn left and "
    "'quay trái' to turn_left; turn right and 'quay phải' to turn_right. Map "
    "sidestep left/right and 'đi ngang trái/phải' to crab_left/crab_right. "
    "Map stop/stand and 'dừng lại', 'đứng lại', or 'đứng lên' to stop or stand. "
    "Map hold and 'giữ nguyên' to hold. Map sit/'ngồi xuống' to sit, "
    "prone/lie down/'nằm xuống' to prone, chase/follow the ball/'đuổi bóng' "
    "to chase, wave/'vẫy chào' to wave, bounce/'nhún' to bounce, jump/'nhảy' "
    "to jump, and center/'đưa servo về giữa' to center. Map manual control, "
    "manual mode, or 'điều khiển tay' to manual. Map automatic mode or "
    "'điều khiển tự động' to automatic. Use only the command names declared "
    "by the tool."
)


def movement_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "move_robot",
        "description": "Execute one approved robot movement command.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": sorted(MOVEMENT_COMMANDS),
                }
            },
            "required": ["command"],
        },
    }


def sound_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "play_sound",
        "description": "Play one predefined sound on the robot.",
        "parameters": {
            "type": "object",
            "properties": {
                "sound": {
                    "type": "string",
                    "enum": sorted(ALLOWED_SOUNDS),
                }
            },
            "required": ["sound"],
        },
    }


def send_json(ws: websocket.WebSocketApp, event: dict[str, Any]) -> None:
    """Send one Realtime client event as JSON."""

    ws.send(json.dumps(event, separators=(",", ":")))


class RealtimeSpeechClient:
    """Small websocket-client wrapper for the Realtime speech session."""

    def __init__(
        self,
        api_key: str,
        bridge: Bridge,
        *,
        on_move: Callable[[str], dict[str, Any] | None] | None = None,
        enable_sound_tool: bool = False,
        instructions: str = ROBOT_INSTRUCTIONS,
        force_tool: str | None = None,
    ) -> None:
        self.bridge = bridge
        self.on_move = on_move
        self.enable_sound_tool = enable_sound_tool
        self.instructions = instructions
        self.force_tool = force_tool
        self._ws: websocket.WebSocketApp | None = None
        self._send_lock = threading.Lock()
        self._handled_call_ids: set[str] = set()
        self.ready = threading.Event()
        self.closed = threading.Event()

        url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
        headers = [f"Authorization: Bearer {api_key}"]
        self._app = websocket.WebSocketApp(
            url,
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    def start(self) -> None:
        thread = threading.Thread(target=self._run, name="realtime-ws", daemon=True)
        thread.start()

    def _run(self) -> None:
        self._ws = self._app
        try:
            self._app.run_forever()
        finally:
            self.closed.set()

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        print(f"Connected to OpenAI Realtime ({MODEL}).")
        send_json(
            ws,
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": MODEL,
                    "output_modalities": ["text"],
                    "instructions": self.instructions,
                    "audio": {
                        "input": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": INPUT_SAMPLE_RATE,
                            },
                            "transcription": {
                                "model": "gpt-4o-mini-transcribe",
                            },
                            "turn_detection": {"type": "semantic_vad"},
                        }
                    },
                    "tools": [
                        sound_tool() if self.enable_sound_tool else movement_tool()
                    ],
                    "tool_choice": self.force_tool or "auto",
                },
            },
        )
        print(
            "Realtime tool mode: "
            + (self.force_tool or "auto")
        )

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        event = json.loads(message)
        event_type = event.get("type", "")

        if event_type == "session.updated":
            self.ready.set()
            print("Realtime session ready; waiting for speech.")
        elif event_type == "input_audio_buffer.speech_started":
            print("SPEECH_RECEIVED: speech started; listening")
        elif event_type == "input_audio_buffer.speech_stopped":
            print("SPEECH_RECEIVED: speech ended; processing")
        elif event_type == "response.output_text.delta":
            print(event.get("delta", ""), end="", flush=True)
        elif event_type == "response.output_text.done":
            print()
        elif event_type == "conversation.item.input_audio_transcription.completed":
            print(f"SPEECH_RECEIVED: transcript={event.get('transcript', '')}")
        elif event_type in {"response.output_item.done", "conversation.item.done"}:
            item = event.get("item", {})
            if item.get("type") == "function_call":
                self._handle_function_call(item)
        elif event_type == "response.function_call_arguments.done":
            self._handle_function_call(
                {
                    "type": "function_call",
                    "call_id": event.get("call_id"),
                    "name": event.get("name", "unknown"),
                    "arguments": event.get("arguments", "{}"),
                }
            )
        elif event_type == "response.done":
            response = event.get("response", {})
            if response.get("status") not in {None, "completed"}:
                print(
                    f"Realtime response status: {response.get('status')} "
                    f"error={response.get('status_details')}"
                )
            # Fallback for a complete response whose output item was not
            # surfaced separately by the websocket client/server combination.
            for item in response.get("output", []):
                if item.get("type") == "function_call":
                    self._handle_function_call(item)
        elif event_type == "error":
            print(f"Realtime error: {json.dumps(event)}")

    def _on_error(self, _ws: websocket.WebSocketApp, error: Any) -> None:
        print(f"Realtime websocket error: {error}")

    def _on_close(
        self,
        _ws: websocket.WebSocketApp,
        status_code: int | None,
        message: str | None,
    ) -> None:
        print(f"Realtime websocket closed ({status_code}): {message}")

    def _handle_function_call(self, item: dict[str, Any]) -> None:
        call_id = item.get("call_id")
        if not call_id or call_id in self._handled_call_ids:
            return
        self._handled_call_ids.add(call_id)

        print(
            f"Realtime tool call: {item.get('name', 'unknown')} "
            f"arguments={item.get('arguments', '{}')}"
        )

        raw_arguments = item.get("arguments", "{}")
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            )
        except (TypeError, json.JSONDecodeError):
            arguments = {}

        name = item.get("name", "unknown")
        if name == "move_robot":
            command = str(arguments.get("command", "")).lower()
            if command not in MOVEMENT_COMMANDS:
                result = {"status": "rejected", "message": "Unsupported movement"}
                print(f"Rejected unsupported movement: {command!r}")
            else:
                try:
                    callback_result = (
                        self.on_move(command) if self.on_move else None
                    )
                    if callback_result is None:
                        command_payload = MOVEMENT_COMMANDS[command]
                        if command_payload == "chase":
                            raise RuntimeError(
                                "chase requires the main robot application callback"
                            )
                        self.bridge.call("send_motor_command", command_payload)
                        callback_result = {
                            "status": "accepted",
                            "command": command,
                            "uart": command_payload,
                        }
                    result = callback_result
                    print(f"SPEECH_COMMAND_ACCEPTED: {command}")
                except Exception as error:  # Keep errors inside the WS loop.
                    result = {"status": "error", "message": str(error)}
                    print(f"Could not send movement command to robot: {error}")
        elif name == "play_sound" and self.enable_sound_tool:
            sound = str(arguments.get("sound", "")).lower()
            if sound in ALLOWED_SOUNDS:
                try:
                    self.bridge.call("play_test_sound", sound)
                    result = {"status": "played", "sound": sound}
                    print(f"UNO Q sent command to ESP32: PLAY:{sound}")
                except Exception as error:  # Bridge errors must not kill the WS loop.
                    result = {"status": "error", "message": str(error)}
                    print(f"Could not send sound command to ESP32: {error}")
            else:
                result = {"status": "rejected", "message": "Unsupported sound"}
                print(f"Rejected unsupported sound: {sound!r}")
        else:
            result = {"status": "rejected", "message": "Unknown tool"}
            print(f"Rejected unknown tool: {name!r}")

        if self._ws is None:
            return
        with self._send_lock:
            send_json(
                self._ws,
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result),
                    },
                },
            )
            send_json(self._ws, {"type": "response.create"})

    def send_audio(self, pcm16: bytes) -> bool:
        """Append one raw PCM16 frame to the current Realtime input buffer."""

        # Local capture starts before the Realtime session is ready. Drop early
        # frames instead of blocking the microphone reader while waiting for
        # the cloud session.
        if not self.ready.is_set():
            return False
        if self.closed.is_set() or self._ws is None:
            return False

        event = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm16).decode("ascii"),
        }
        try:
            with self._send_lock:
                send_json(self._ws, event)
            return True
        except Exception as error:
            print(f"Could not send microphone audio: {error}")
            return False

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()


class AudioMonitor:
    """Print evidence that PCM frames and a nonzero mic signal reached UNO Q."""

    def __init__(self, source: str = "UNO Q audio") -> None:
        self.source = source
        self.frames = 0
        self.bytes_received = 0
        self.next_report = 0.0
        self.signal_active = False

    def report(self, pcm16: bytes) -> None:
        self.frames += 1
        self.bytes_received += len(pcm16)

        samples = array("h")
        samples.frombytes(pcm16[: len(pcm16) - (len(pcm16) % 2)])
        peak = 0
        rms = 0.0
        if sys.byteorder != "little":
            samples.byteswap()
        if samples:
            peak = max(abs(sample) for sample in samples)
            rms = math.sqrt(
                sum(int(sample) * int(sample) for sample in samples) / len(samples)
            )
            signal_active = peak > 300
            if signal_active != self.signal_active:
                self.signal_active = signal_active
                print(
                    "SPEECH_AUDIO_RECEIVED: microphone signal detected"
                    if signal_active
                    else "SPEECH_AUDIO_ENDED: microphone quiet"
                )

        now = time.monotonic()
        if self.frames != 1 and now < self.next_report:
            return
        self.next_report = now + 1.0

        if samples:
            rms_dbfs = 20.0 * math.log10(max(rms, 1.0) / 32768.0)
            peak_dbfs = 20.0 * math.log10(max(peak, 1.0) / 32768.0)
            signal_state = "signal" if self.signal_active else "quiet"
            levels = f"rms={rms_dbfs:.1f} dBFS peak={peak_dbfs:.1f} dBFS {signal_state}"
        else:
            levels = "empty frame"
        print(
            f"{self.source} RX: frames={self.frames} "
            f"bytes={self.bytes_received} {levels}"
        )


def serve_local_microphone(
    realtime: RealtimeSpeechClient | None,
    stop_event: threading.Event,
    on_frame: Callable[[bytes], None] | None = None,
    *,
    device: str | None = None,
    sample_rate: int = INPUT_SAMPLE_RATE,
    frame_ms: int = DEFAULT_FRAME_MS,
) -> None:
    """Capture the USB webcam microphone locally on the UNO Q.

    ``plughw:...`` is recommended for ``device`` because many USB webcams
    advertise 48 kHz while the Realtime input is sent at 24 kHz.  ALSA's plug
    layer performs that conversion before the PCM frame reaches this process.
    """

    if sample_rate != INPUT_SAMPLE_RATE:
        print(
            "USB microphone capture stopped: Realtime speech input requires "
            f"{INPUT_SAMPLE_RATE} Hz, got {sample_rate} Hz."
        )
        return

    selected_device = configured_device(device)
    monitor = AudioMonitor("UNO Q USB mic")
    print(
        "Using UNO Q USB microphone: "
        f"device={selected_device} format=PCM16 mono/{sample_rate}Hz "
        f"frame={frame_ms}ms"
    )

    while not stop_event.is_set():
        microphone = AlsaMicrophone(
            device=selected_device,
            sample_rate=sample_rate,
            channels=MIC_CHANNELS,
            frame_ms=frame_ms,
        )
        try:
            for frame in microphone.frames(stop_event):
                monitor.report(frame)
                if on_frame is not None:
                    on_frame(frame)
                if realtime is not None:
                    realtime.send_audio(frame)
        except (AudioCaptureError, ValueError) as error:
            if stop_event.is_set():
                break
            print(f"USB microphone capture error: {error}")
            print(
                f"Retrying USB microphone in {MIC_RETRY_DELAY_S:g}s; "
                "set UNO_Q_MIC_DEVICE to the webcam's ALSA plughw device."
            )
            stop_event.wait(MIC_RETRY_DELAY_S)
        else:
            if not stop_event.is_set():
                print(
                    "USB microphone capture ended; "
                    f"retrying in {MIC_RETRY_DELAY_S:g}s."
                )
                stop_event.wait(MIC_RETRY_DELAY_S)
        finally:
            microphone.stop()


def serve_esp32_audio(
    realtime: RealtimeSpeechClient | None,
    host: str,
    port: int,
    stop_event: threading.Event,
    on_frame: Callable[[bytes], None] | None = None,
) -> None:
    """Accept one ESP32 TCP stream and optionally relay its raw frames.

    New ESP32 builds send an ``AUD0`` header that identifies mono PCM16.
    The old four-byte-length-only stream is still accepted for the one-mic
    speech test.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((host, port))
        except OSError as error:
            print(f"Could not bind UNO Q audio server to {host}:{port}: {error}")
            return
        server.listen(1)
        server.settimeout(1.0)
        print(f"Listening for legacy ESP32 PCM audio on {host}:{port}.")
        monitor = AudioMonitor("ESP32 mic")

        while not stop_event.is_set():
            try:
                connection, address = server.accept()
            except socket.timeout:
                continue

            print(f"ESP32 audio connected from {address[0]}:{address[1]}.")
            with connection:
                stream_channels = MIC_CHANNELS
                while not stop_event.is_set():
                    try:
                        header = read_exact(connection, 4)
                        if header == MIC_STREAM_MAGIC:
                            sample_rate, stream_channels, bits = MIC_STREAM_HEADER.unpack(
                                read_exact(connection, MIC_STREAM_HEADER.size)
                            )
                            if bits != 16:
                                raise ValueError(
                                    f"unsupported ESP32 audio depth: {bits}"
                                )
                            if stream_channels != MIC_CHANNELS:
                                raise ValueError(
                                    f"expected {MIC_CHANNELS} microphone channel, "
                                    f"got {stream_channels}"
                                )
                            print(
                                "ESP32 audio format: "
                                f"{stream_channels} channel(s), {sample_rate} Hz, PCM16"
                            )
                            header = read_exact(connection, 4)
                        frame_size = FRAME_LENGTH.unpack(header)[0]
                        if frame_size == 0 or frame_size > MAX_AUDIO_FRAME_BYTES:
                            raise ValueError(f"Invalid audio frame size: {frame_size}")
                        frame = read_exact(connection, frame_size)
                    except (ConnectionError, OSError, ValueError) as error:
                        print(f"ESP32 audio stream stopped: {error}")
                        break

                    monitor.report(frame)
                    if on_frame is not None:
                        on_frame(frame)
                    if realtime is not None:
                        realtime.send_audio(frame)

            print("Waiting for ESP32 audio reconnect.")


def configured_input_mode(value: str | None = None) -> str:
    """Normalize the speech input selector, defaulting to the USB webcam mic."""

    selected = str(
        value if value is not None else os.getenv("UNO_Q_SPEECH_INPUT", "usb")
    ).lower().strip()
    if selected in {"esp32", "tcp", "legacy"}:
        return "esp32"
    return "usb"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        choices=("usb", "esp32"),
        default=configured_input_mode(),
        help="speech input source (default: usb webcam microphone)",
    )
    parser.add_argument(
        "--listen-host",
        default=os.getenv("UNO_Q_AUDIO_LISTEN_HOST", "0.0.0.0"),
        help="legacy ESP32 TCP bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--audio-port",
        type=int,
        default=int(os.getenv("UNO_Q_AUDIO_PORT", str(DEFAULT_AUDIO_PORT))),
        help="legacy ESP32 PCM port (default: 3333)",
    )
    parser.add_argument(
        "--mic-device",
        default=os.getenv("UNO_Q_MIC_DEVICE", "default"),
        help="ALSA USB microphone device (default: UNO_Q_MIC_DEVICE or default)",
    )
    parser.add_argument(
        "--mic-rate",
        type=int,
        default=int(os.getenv("UNO_Q_MIC_RATE", str(INPUT_SAMPLE_RATE))),
        help=f"USB microphone rate; Realtime requires {INPUT_SAMPLE_RATE} Hz",
    )
    parser.add_argument(
        "--mic-frame-ms",
        type=int,
        default=int(os.getenv("UNO_Q_MIC_FRAME_MS", str(DEFAULT_FRAME_MS))),
        help="USB microphone frame duration in milliseconds (default: 20)",
    )
    return parser.parse_args()


class SpeechService:
    """Own the Realtime client and UNO Q audio listener threads."""

    def __init__(
        self,
        realtime: RealtimeSpeechClient | None,
        stop_event: threading.Event,
        audio_thread: threading.Thread,
    ) -> None:
        self.realtime = realtime
        self.stop_event = stop_event
        self.audio_thread = audio_thread

    def stop(self) -> None:
        self.stop_event.set()
        if self.realtime is not None:
            self.realtime.close()
        self.audio_thread.join(timeout=1.0)


def start_speech_service(
    bridge: Bridge,
    *,
    on_move: Callable[[str], dict[str, Any] | None] | None = None,
    on_audio_frame: Callable[[bytes], None] | None = None,
    listen_host: str | None = None,
    audio_port: int | None = None,
    input_mode: str | None = None,
    mic_device: str | None = None,
    mic_rate: int | None = None,
    mic_frame_ms: int | None = None,
) -> SpeechService | None:
    """Start speech recognition in the background for the normal robot app.

    Local USB microphone capture is the default.  The ESP32 TCP listener is
    retained only as an explicit compatibility path for old INMP441 firmware.
    """

    api_key = os.getenv("OPENAI_API_KEY")
    realtime: RealtimeSpeechClient | None = None
    if api_key:
        realtime = RealtimeSpeechClient(
            api_key,
            bridge,
            on_move=on_move,
            instructions=ROBOT_INSTRUCTIONS,
        )
    else:
        print("Speech recognition disabled: OPENAI_API_KEY is not set.")
    stop_event = threading.Event()
    selected_input = configured_input_mode(input_mode)
    if selected_input == "esp32":
        audio_thread = threading.Thread(
            target=serve_esp32_audio,
            args=(
                realtime,
                listen_host or os.getenv("UNO_Q_AUDIO_LISTEN_HOST", "0.0.0.0"),
                audio_port
                or int(os.getenv("UNO_Q_AUDIO_PORT", str(DEFAULT_AUDIO_PORT))),
                stop_event,
                on_audio_frame,
            ),
            name="esp32-audio-server",
            daemon=True,
        )
    else:
        audio_thread = threading.Thread(
            target=serve_local_microphone,
            args=(realtime, stop_event, on_audio_frame),
            kwargs={
                "device": mic_device,
                "sample_rate": mic_rate
                if mic_rate is not None
                else int(os.getenv("UNO_Q_MIC_RATE", str(INPUT_SAMPLE_RATE))),
                "frame_ms": mic_frame_ms
                if mic_frame_ms is not None
                else int(os.getenv("UNO_Q_MIC_FRAME_MS", str(DEFAULT_FRAME_MS))),
            },
            name="usb-microphone-capture",
            daemon=True,
        )
        print("Speech input source: USB webcam microphone on UNO Q.")
    if realtime is not None:
        realtime.start()
    audio_thread.start()
    return SpeechService(realtime, stop_event, audio_thread)


def main() -> None:
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY in the UNO Q runtime environment first.")

    bridge = Bridge()
    realtime = RealtimeSpeechClient(
        api_key,
        bridge,
        enable_sound_tool=True,
        instructions=SOUND_TEST_INSTRUCTIONS,
        force_tool="required" if FORCE_SOUND_TOOL else "auto",
    )
    stop_event = threading.Event()
    realtime.start()
    if args.input == "esp32":
        audio_thread = threading.Thread(
            target=serve_esp32_audio,
            args=(realtime, args.listen_host, args.audio_port, stop_event),
            name="esp32-audio-server",
            daemon=True,
        )
    else:
        audio_thread = threading.Thread(
            target=serve_local_microphone,
            args=(realtime, stop_event),
            kwargs={
                "device": args.mic_device,
                "sample_rate": args.mic_rate,
                "frame_ms": args.mic_frame_ms,
            },
            name="usb-microphone-capture",
            daemon=True,
        )
        print(
            "Speech test input: USB webcam microphone "
            f"device={configured_device(args.mic_device)}."
        )
    audio_thread.start()

    try:
        if not realtime.ready.wait(timeout=20):
            print(
                "Realtime session is not ready yet; the selected microphone "
                "capture will remain active for local testing."
            )
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopping speech test.")
    finally:
        stop_event.set()
        realtime.close()
        time.sleep(0.2)


if __name__ == "__main__":
    main()
