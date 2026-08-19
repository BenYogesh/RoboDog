"""Standalone UNO Q speech-command test.

The ESP32 sends length-prefixed, little-endian PCM16 mono frames over TCP.
This process forwards those frames to the OpenAI Realtime WebSocket and lets
the model call the local ``play_sound`` function for approved sounds.

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
import struct
import sys
import threading
import time
from typing import Any

import websocket
from arduino.app_utils import Bridge


MODEL = "gpt-realtime-2.1-mini"
INPUT_SAMPLE_RATE = 24000
DEFAULT_AUDIO_PORT = 3333
MAX_AUDIO_FRAME_BYTES = 64 * 1024
ALLOWED_SOUNDS = {"beep", "success", "error"}
# This is a deterministic hardware test. Once the voice turn is detected, make
# the model emit the sound tool call instead of allowing a text-only reply.
# Set SPEECH_TEST_FORCE_TOOL=0 later if unrelated speech must be ignored.
FORCE_SOUND_TOOL = os.getenv("SPEECH_TEST_FORCE_TOOL", "1").lower() not in {
    "0",
    "false",
    "no",
}


REALTIME_INSTRUCTIONS = (
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


def send_json(ws: websocket.WebSocketApp, event: dict[str, Any]) -> None:
    """Send one Realtime client event as JSON."""

    ws.send(json.dumps(event, separators=(",", ":")))


class RealtimeSpeechClient:
    """Small websocket-client wrapper for the Realtime speech session."""

    def __init__(self, api_key: str, bridge: Bridge) -> None:
        self.bridge = bridge
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
                    "instructions": REALTIME_INSTRUCTIONS,
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
                        {
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
                    ],
                    "tool_choice": "required" if FORCE_SOUND_TOOL else "auto",
                },
            },
        )
        print(
            "Realtime tool mode: "
            + ("required" if FORCE_SOUND_TOOL else "auto")
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
                    "name": event.get("name", "play_sound"),
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

        try:
            arguments = json.loads(item.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}

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

        # The TCP listener starts before the Realtime session is ready. Drop
        # early frames instead of blocking the ESP32 connection while waiting
        # for an unrelated cloud connection.
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


def read_exact(connection: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ConnectionError("ESP32 audio connection closed")
        data.extend(chunk)
    return bytes(data)


class AudioMonitor:
    """Print evidence that PCM frames and a nonzero mic signal reached UNO Q."""

    def __init__(self) -> None:
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
            f"UNO Q audio RX: frames={self.frames} "
            f"bytes={self.bytes_received} {levels}"
        )


def serve_esp32_audio(
    realtime: RealtimeSpeechClient,
    host: str,
    port: int,
    stop_event: threading.Event,
) -> None:
    """Accept one ESP32 TCP stream using a 4-byte big-endian length prefix."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((host, port))
        except OSError as error:
            print(f"Could not bind UNO Q audio server to {host}:{port}: {error}")
            return
        server.listen(1)
        server.settimeout(1.0)
        print(f"Listening for ESP32 PCM audio on {host}:{port}.")
        monitor = AudioMonitor()

        while not stop_event.is_set():
            try:
                connection, address = server.accept()
            except socket.timeout:
                continue

            print(f"ESP32 audio connected from {address[0]}:{address[1]}.")
            with connection:
                while not stop_event.is_set():
                    try:
                        header = read_exact(connection, 4)
                        frame_size = struct.unpack("!I", header)[0]
                        if frame_size == 0 or frame_size > MAX_AUDIO_FRAME_BYTES:
                            raise ValueError(f"Invalid audio frame size: {frame_size}")
                        frame = read_exact(connection, frame_size)
                    except (ConnectionError, OSError, ValueError) as error:
                        print(f"ESP32 audio stream stopped: {error}")
                        break

                    monitor.report(frame)
                    realtime.send_audio(frame)

            print("Waiting for ESP32 audio reconnect.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--listen-host",
        default=os.getenv("UNO_Q_AUDIO_LISTEN_HOST", "0.0.0.0"),
        help="TCP bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--audio-port",
        type=int,
        default=int(os.getenv("UNO_Q_AUDIO_PORT", str(DEFAULT_AUDIO_PORT))),
        help="TCP port for ESP32 PCM frames (default: 3333)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY in the UNO Q runtime environment first.")

    bridge = Bridge()
    realtime = RealtimeSpeechClient(api_key, bridge)
    stop_event = threading.Event()
    realtime.start()
    audio_thread = threading.Thread(
        target=serve_esp32_audio,
        args=(realtime, args.listen_host, args.audio_port, stop_event),
        name="esp32-audio-server",
        daemon=True,
    )
    audio_thread.start()

    try:
        if not realtime.ready.wait(timeout=20):
            print(
                "Realtime session is not ready yet; the UNO Q TCP audio server "
                "will remain available for network testing."
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
