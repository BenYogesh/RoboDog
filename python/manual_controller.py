"""Small optional laptop client for the UNO Q manual media streams.

Install the laptop-only dependencies first:

    python -m pip install opencv-python sounddevice

Bluetooth movement commands remain owned by the ESP32. This helper only
displays the camera, plays the mono microphone stream, and sends the laptop
microphone to the robot speaker stream.
"""

from __future__ import annotations

import argparse
import socket
import threading

from media_protocol import AUDIO_HEADER, AUDIO_MAGIC, FRAME_LENGTH, read_exact


def receive_microphones(host: str, port: int, stop_event: threading.Event) -> None:
    import sounddevice as sd

    connection = socket.create_connection((host, port), timeout=10)
    connection.settimeout(None)
    if read_exact(connection, 4) != AUDIO_MAGIC:
        raise ValueError("invalid microphone stream header")
    sample_rate, channels, bits = AUDIO_HEADER.unpack(
        read_exact(connection, AUDIO_HEADER.size)
    )
    if bits != 16:
        raise ValueError(f"unsupported microphone depth: {bits}")
    print(f"Microphones: {sample_rate} Hz, {channels} channel(s), PCM16")
    with sd.RawOutputStream(
        samplerate=sample_rate,
        channels=channels,
        dtype="int16",
        blocksize=0,
    ) as output:
        while not stop_event.is_set():
            frame_size = FRAME_LENGTH.unpack(read_exact(connection, 4))[0]
            if frame_size == 0:
                continue
            output.write(read_exact(connection, frame_size))
    connection.close()


def send_laptop_microphone(
    host: str, port: int, stop_event: threading.Event
) -> None:
    import sounddevice as sd

    connection = socket.create_connection((host, port), timeout=10)
    connection.settimeout(None)
    connection.sendall(AUDIO_MAGIC + AUDIO_HEADER.pack(16000, 1, 16))
    print("Laptop microphone: 16000 Hz, mono PCM16")
    with sd.RawInputStream(
        samplerate=16000,
        channels=1,
        dtype="int16",
        blocksize=320,
    ) as microphone:
        while not stop_event.is_set():
            frame, _overflowed = microphone.read(320)
            connection.sendall(FRAME_LENGTH.pack(len(frame)) + frame)
    connection.close()


def show_camera(host: str, port: int, stop_event: threading.Event) -> None:
    import cv2

    capture = cv2.VideoCapture(f"http://{host}:{port}/camera.mjpg")
    if not capture.isOpened():
        raise RuntimeError("could not open UNO Q camera stream")
    try:
        while not stop_event.is_set():
            ok, frame = capture.read()
            if not ok:
                continue
            cv2.imshow("RoboDog manual camera (q to quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                stop_event.set()
                return
    finally:
        capture.release()
        cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="UNO Q LAN IP address")
    parser.add_argument("--video-port", type=int, default=8080)
    parser.add_argument("--audio-port", type=int, default=3334)
    parser.add_argument("--speaker-port", type=int, default=3335)
    parser.add_argument("--no-camera", action="store_true")
    parser.add_argument("--no-speaker", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stop_event = threading.Event()
    threads: list[threading.Thread] = []

    def start(target, name: str, *target_args) -> None:
        thread = threading.Thread(
            target=target, args=target_args, name=name, daemon=True
        )
        thread.start()
        threads.append(thread)

    start(receive_microphones, "robot-microphones", args.host, args.audio_port, stop_event)
    if not args.no_speaker:
        start(
            send_laptop_microphone,
            "laptop-speaker-uplink",
            args.host,
            args.speaker_port,
            stop_event,
        )
    try:
        if args.no_camera:
            stop_event.wait()
        else:
            show_camera(args.host, args.video_port, stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=1.0)


if __name__ == "__main__":
    main()
