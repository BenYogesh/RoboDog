"""Standalone test for the USB webcam microphone connected to the UNO Q.

Examples on the UNO Q::

    python3 python/usb_mic_test.py --list
    UNO_Q_MIC_DEVICE=plughw:CARD=Webcam,DEV=0 python3 python/usb_mic_test.py
    python3 python/usb_mic_test.py --device plughw:1,0 --seconds 10
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import threading
import time
import wave

from local_microphone import (
    AlsaMicrophone,
    AudioCaptureError,
    DEFAULT_CHANNELS,
    DEFAULT_FRAME_MS,
    DEFAULT_SAMPLE_RATE,
    configured_device,
    list_capture_devices,
    pcm16_levels,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="list ALSA capture devices and exit",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("UNO_Q_MIC_DEVICE", "default"),
        help="ALSA device (default: UNO_Q_MIC_DEVICE or default; use plughw:...)",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=int(os.getenv("UNO_Q_MIC_RATE", str(DEFAULT_SAMPLE_RATE))),
        help="capture sample rate (default: 24000; plughw can resample webcam audio)",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=int(os.getenv("UNO_Q_MIC_CHANNELS", str(DEFAULT_CHANNELS))),
        help="capture channel count (default: 1)",
    )
    parser.add_argument(
        "--frame-ms",
        type=int,
        default=int(os.getenv("UNO_Q_MIC_FRAME_MS", str(DEFAULT_FRAME_MS))),
        help="PCM frame duration in milliseconds (default: 20)",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="stop after this many seconds; use 0 to run until Ctrl+C (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optionally save the captured audio as a WAV file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        try:
            print(list_capture_devices())
            print("Use the webcam's card/device as plughw:<card>,<device>, for example plughw:1,0.")
        except AudioCaptureError as error:
            print(f"USB_MIC_ERROR {error}", file=sys.stderr)
            return 2
        return 0

    if args.seconds < 0:
        print("--seconds cannot be negative", file=sys.stderr)
        return 2

    stop_event = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    device = configured_device(args.device)
    microphone = AlsaMicrophone(
        device=device,
        sample_rate=args.rate,
        channels=args.channels,
        frame_ms=args.frame_ms,
    )
    output_file: wave.Wave_write | None = None
    if args.output is not None:
        try:
            output_file = wave.open(str(args.output), "wb")
            output_file.setnchannels(args.channels)
            output_file.setsampwidth(2)
            output_file.setframerate(args.rate)
        except (OSError, wave.Error) as error:
            print(f"USB_MIC_ERROR could not open {args.output}: {error}", file=sys.stderr)
            return 2

    print(
        "USB_MIC_TEST_START "
        f"device={device} format={microphone.format_description} "
        f"duration={'until Ctrl+C' if args.seconds == 0 else f'{args.seconds:g}s'}"
    )
    print("Speak near the webcam microphone; a non-quiet peak confirms signal capture.")

    started_at = time.monotonic()
    next_report_at = started_at
    frame_count = 0
    byte_count = 0
    signal_seen = False
    try:
        for frame in microphone.frames(stop_event):
            frame_count += 1
            byte_count += len(frame)
            if output_file is not None:
                output_file.writeframesraw(frame)

            rms_dbfs, peak_dbfs, peak = pcm16_levels(frame)
            signal_seen = signal_seen or peak > 300
            now = time.monotonic()
            if now >= next_report_at:
                state = "signal" if peak > 300 else "quiet"
                print(
                    "USB_MIC_LEVEL "
                    f"frames={frame_count} bytes={byte_count} "
                    f"rms={rms_dbfs:.1f}dBFS peak={peak_dbfs:.1f}dBFS {state}"
                )
                next_report_at = now + 1.0
            if args.seconds and now - started_at >= args.seconds:
                stop_event.set()
    except (AudioCaptureError, ValueError) as error:
        print(f"USB_MIC_ERROR {error}", file=sys.stderr)
        return 2
    finally:
        microphone.stop()
        if output_file is not None:
            output_file.close()

    print(
        "USB_MIC_TEST_DONE "
        f"frames={frame_count} bytes={byte_count} "
        f"signal_seen={'yes' if signal_seen else 'no'}"
    )
    return 0 if frame_count > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
