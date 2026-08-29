"""Capture a USB webcam microphone through ALSA on the UNO Q Linux side.

The UNO Q receives the webcam audio directly, so the ESP32 no longer needs to
sample or forward an INMP441 microphone.  ``arecord`` is used instead of a
Python audio package because it is already the native ALSA command-line
interface on Linux and keeps the App Lab dependency set small.
"""

from __future__ import annotations

from array import array
import math
import os
import shutil
import subprocess
import sys
import threading
from typing import Iterator


DEFAULT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1
DEFAULT_FRAME_MS = 20
DEFAULT_DEVICE = "default"


class AudioCaptureError(RuntimeError):
    """The local ALSA capture process could not provide PCM audio."""


def configured_device(device: str | None = None) -> str:
    """Return the explicitly selected ALSA device or the runtime default."""

    selected = device if device is not None else os.getenv("UNO_Q_MIC_DEVICE")
    return str(selected or DEFAULT_DEVICE).strip() or DEFAULT_DEVICE


def _arecord_path() -> str:
    executable = shutil.which("arecord")
    if executable is None:
        raise AudioCaptureError(
            "arecord was not found. Install the ALSA utilities on the UNO Q "
            "or use the board's bundled audio tools."
        )
    return executable


def list_capture_devices() -> str:
    """Return the ALSA hardware capture-device listing."""

    executable = _arecord_path()
    try:
        result = subprocess.run(
            [executable, "--list-devices"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError as error:
        raise AudioCaptureError(f"could not run arecord: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise AudioCaptureError("arecord device listing timed out") from error

    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise AudioCaptureError(
            f"arecord device listing failed with code {result.returncode}: "
            f"{output or 'no diagnostic output'}"
        )
    return output or "No ALSA capture devices were reported."


class AlsaMicrophone:
    """Read fixed-size little-endian PCM16 frames from an ALSA device."""

    def __init__(
        self,
        *,
        device: str | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        frame_ms: int = DEFAULT_FRAME_MS,
    ) -> None:
        if sample_rate <= 0 or channels <= 0 or frame_ms <= 0:
            raise ValueError("sample_rate, channels, and frame_ms must be positive")

        samples_per_frame = round(sample_rate * frame_ms / 1000)
        if samples_per_frame <= 0:
            raise ValueError("frame_ms is too small for the selected sample rate")

        self.device = configured_device(device)
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_ms = frame_ms
        self.samples_per_frame = samples_per_frame
        self.frame_bytes = samples_per_frame * channels * 2
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def format_description(self) -> str:
        return (
            f"PCM16_LE {self.channels}ch/{self.sample_rate}Hz "
            f"{self.frame_ms}ms frames"
        )

    def command(self) -> list[str]:
        """Build the arecord command used for this capture stream."""

        return [
            _arecord_path(),
            "--quiet",
            "--device",
            self.device,
            "--type",
            "raw",
            "--format",
            "S16_LE",
            "--channels",
            str(self.channels),
            "--rate",
            str(self.sample_rate),
        ]

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                self.command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as error:
            raise AudioCaptureError(f"could not start arecord: {error}") from error

    def _process_diagnostic(self) -> str:
        process = self._process
        if process is None:
            return "capture process is not running"

        code = process.poll()
        if code is None:
            return "capture stream ended unexpectedly"

        detail = ""
        if process.stderr is not None:
            try:
                detail = process.stderr.read().decode(errors="replace").strip()
            except OSError:
                detail = ""
        return f"arecord exited with code {code}: {detail or 'no diagnostic output'}"

    def _read_frame(self) -> bytes | None:
        process = self._process
        if process is None or process.stdout is None:
            raise AudioCaptureError("capture process is not running")

        frame = bytearray()
        while len(frame) < self.frame_bytes:
            try:
                chunk = process.stdout.read(self.frame_bytes - len(frame))
            except OSError as error:
                raise AudioCaptureError(f"could not read microphone audio: {error}") from error
            if not chunk:
                return None
            frame.extend(chunk)
        return bytes(frame)

    def frames(self, stop_event: threading.Event) -> Iterator[bytes]:
        """Yield PCM frames until the stop event is set or capture fails."""

        self.start()
        try:
            while not stop_event.is_set():
                frame = self._read_frame()
                if frame is None:
                    if stop_event.is_set():
                        return
                    raise AudioCaptureError(self._process_diagnostic())
                yield frame
        finally:
            self.stop()

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)

        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def pcm16_levels(pcm16: bytes) -> tuple[float, float, int]:
    """Return ``(rms_dbfs, peak_dbfs, peak)`` for a PCM16 frame."""

    samples = array("h")
    samples.frombytes(pcm16[: len(pcm16) - (len(pcm16) % 2)])
    if not samples:
        return -float("inf"), -float("inf"), 0
    if sys.byteorder != "little":
        samples.byteswap()

    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(
        sum(int(sample) * int(sample) for sample in samples) / len(samples)
    )
    return (
        20.0 * math.log10(max(rms, 1.0) / 32768.0),
        20.0 * math.log10(max(peak, 1.0) / 32768.0),
        peak,
    )
