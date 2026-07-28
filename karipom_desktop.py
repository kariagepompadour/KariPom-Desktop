#!/usr/bin/env python3
# =============================================================================
# KariPom Desktop v0.2
# PCだけで「かりポム」を体験するスピンアウト版
#
# ベース:
#   - karipom_talk_20260719_multiOS.py のPC音声取得・発話判定
#   - karipom_lighting_20260728_0731_v2_0_8lane_rhythm.ino の通常顔描画
#
# v0.1:
#   * macOS / Windows / Linux のPC再生音を取得
#   * 発話中は実機同様、140msごとに口を開閉
#   * 開口時は赤い楕円を±2px揺らす
#   * 実機同様の通常顔、瞬き、二重瞬き、鼻ヒクヒク
#   * CoreS3 / Arduino / UDP / IP設定は不要
#
# 必要パッケージ:
#   共通            : pip install numpy PyQt5
#   macOS           : pip install sounddevice
#                     + BlackHole 2ch と Audio MIDI設定の「複数出力装置」
#   Windows / Linux : pip install soundcard
#   Linux           : libpulse0（PipeWire環境は pipewire-pulse も）
# =============================================================================

from __future__ import annotations

import argparse
import platform
import random
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

try:
    from PyQt5.QtCore import QTimer, Qt
    from PyQt5.QtGui import QBrush, QColor, QPainter, QPen
    from PyQt5.QtWidgets import QApplication, QWidget
except ImportError as exc:
    print("PyQt5 が必要です。")
    print("  pip install PyQt5 numpy")
    raise SystemExit(1) from exc


OS_NAME = platform.system()  # Darwin / Windows / Linux
IS_MAC = OS_NAME == "Darwin"
IS_WINDOWS = OS_NAME == "Windows"
IS_LINUX = OS_NAME == "Linux"

if not (IS_MAC or IS_WINDOWS or IS_LINUX):
    print(f"未対応OSです: {OS_NAME}")
    raise SystemExit(1)

if IS_MAC:
    try:
        import sounddevice as sd
    except ImportError as exc:
        print("macOSでは sounddevice が必要です。")
        print("  pip install sounddevice")
        raise SystemExit(1) from exc
else:
    try:
        import soundcard as sc
    except ImportError as exc:
        print("Windows/Linuxでは soundcard が必要です。")
        print("  pip install soundcard")
        if IS_LINUX:
            print("Linuxでは libpulse0 も必要です。")
        raise SystemExit(1) from exc


# ===== karipom_talk 最終版と同じ音声判定値 =====
DEFAULT_THRESHOLD = 0.02
SILENCE_TIMEOUT = 5.0
HARD_STOP_RMS = 0.0005
HARD_STOP_TIMEOUT = 0.8
BLOCKSIZE = 2048
SAMPLERATE = 44100

# ===== 実機 .ino と同じ顔・モーション値 =====
CANVAS_W = 320
CANVAS_H = 240
NOSE_X = 160
NOSE_Y = 145
MOUTH_PAKU_MS = 140
BLINK_CLOSED_MS = 80
DOUBLE_BLINK_GAP_MS = 180
BLINK_PROBABILITY = 0.45
DOUBLE_BLINK_PROBABILITY = 0.08
BLINK_INTERVAL_MS = (3000, 10000)
NOSE_INTERVAL_MS = (120, 280)

# ===== 起動時処理 =====
CHATGPT_URL = "https://chatgpt.com/"
STARTUP_GREETING = "こんにちは！ ブラウザでChatGPTも開きました。ChatGPTの入力欄の右側にある音声ボタンを押して、かりポムとお話ししましょう。"
BROWSER_OPEN_DELAY_MS = 300
GREETING_DELAY_MS = 1200


def open_chatgpt_page() -> None:
    """標準ブラウザでChatGPTを開く。失敗してもDesktop本体は継続する。"""
    try:
        opened = webbrowser.open(CHATGPT_URL, new=2)
        if opened:
            print(f"Opened ChatGPT: {CHATGPT_URL}")
        else:
            print(f"[startup] 標準ブラウザでChatGPTを開けませんでした: {CHATGPT_URL}", file=sys.stderr)
    except Exception as exc:
        print(f"[startup] ChatGPT browser open failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def _speak_startup_greeting_worker() -> None:
    """OSごとのローカルTTSで起動挨拶を再生する。GUIスレッドはブロックしない。"""
    try:
        if IS_MAC:
            say = shutil.which("say") or "/usr/bin/say"
            subprocess.run([say, STARTUP_GREETING], check=False)
            return

        if IS_WINDOWS:
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if not powershell:
                print("[startup] Windows TTS: PowerShell が見つかりません。", file=sys.stderr)
                return
            escaped = STARTUP_GREETING.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Speak('{escaped}'); "
                "$s.Dispose()"
            )
            subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                check=False,
            )
            return

        # LinuxはTTS実装がディストリビューションで異なるため、
        # 追加Python依存を増やさず、利用可能なローカルTTSを順に使う。
        for command in ("spd-say", "espeak-ng", "espeak"):
            executable = shutil.which(command)
            if not executable:
                continue
            subprocess.run([executable, STARTUP_GREETING], check=False)
            return

        print(
            "[startup] Linux TTSが見つかりません。spd-say / espeak-ng / espeak のいずれかが必要です。",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[startup] TTS failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def speak_startup_greeting() -> None:
    """TTSをバックグラウンドで開始する。"""
    threading.Thread(
        target=_speak_startup_greeting_worker,
        name="KariPomStartupTTS",
        daemon=True,
    ).start()


@dataclass
class AudioOptions:
    threshold: float
    input_hint: Optional[str]
    keepalive: bool


class SharedAudioState:
    """音声スレッドからGUIへ渡す最小共有状態。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._speaking = False
        self._rms = 0.0
        self._error = ""

    def set_audio(self, speaking: bool, rms: float) -> None:
        with self._lock:
            self._speaking = speaking
            self._rms = rms

    def set_error(self, message: str) -> None:
        with self._lock:
            self._error = message

    def snapshot(self) -> tuple[bool, float, str]:
        with self._lock:
            return self._speaking, self._rms, self._error


class SpeechDetector:
    """karipom_talk.py の発話判定をUDPなしでそのまま利用する。"""

    def __init__(self, threshold: float, on_state: Callable[[bool, float], None]) -> None:
        self.threshold = threshold
        self.on_state = on_state
        self.speaking = False
        self.last_loud_time = 0.0
        self.hard_silence_start_time: Optional[float] = None

    def process_block(self, mono_block: np.ndarray) -> None:
        block = np.asarray(mono_block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return

        rms = float(np.sqrt(np.mean(block ** 2)))
        now = time.time()

        if rms > self.threshold:
            self.last_loud_time = now
            self.hard_silence_start_time = None
            speaking_now = True
        else:
            if rms < HARD_STOP_RMS:
                if self.hard_silence_start_time is None:
                    self.hard_silence_start_time = now

                hard_stop = (
                    now - self.hard_silence_start_time
                ) >= HARD_STOP_TIMEOUT
                normal_stop = (now - self.last_loud_time) > SILENCE_TIMEOUT
                speaking_now = not (hard_stop or normal_stop)
            else:
                self.hard_silence_start_time = None
                speaking_now = (now - self.last_loud_time) <= SILENCE_TIMEOUT

        if speaking_now != self.speaking:
            self.speaking = speaking_now
            marker = "▶ SPEAK_START" if speaking_now else "■ SPEAK_STOP"
            print(f"{marker}  rms={rms:.5f}")

        self.on_state(speaking_now, rms)

    def force_stop(self) -> None:
        if self.speaking:
            print("■ SPEAK_STOP  [audio reset]")
        self.speaking = False
        self.hard_silence_start_time = None
        self.on_state(False, 0.0)


class AudioEngine:
    """Talk最終版のOS別PC再生音取得をGUI向けバックグラウンド処理にしたもの。"""

    RESTART_WAIT = 2.0

    def __init__(self, state: SharedAudioState, options: AudioOptions) -> None:
        self.state = state
        self.options = options
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.detector = SpeechDetector(options.threshold, self.state.set_audio)
        self.keepalive_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="KariPomAudio", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.detector.force_stop()

    def _run(self) -> None:
        try:
            if IS_MAC:
                self._run_mac()
            else:
                if self.options.keepalive:
                    self.keepalive_thread = threading.Thread(
                        target=self._keepalive_loop,
                        name="KariPomKeepalive",
                        daemon=True,
                    )
                    self.keepalive_thread.start()
                self._run_soundcard()
        except Exception as exc:  # 最後の防波堤。GUIは落とさない。
            message = f"Audio engine stopped: {type(exc).__name__}: {exc}"
            self.state.set_error(message)
            self.detector.force_stop()
            print(message, file=sys.stderr)

    # ----- macOS / sounddevice -----
    @staticmethod
    def _find_blackhole_device() -> tuple[Optional[int], Optional[str]]:
        devices = sd.query_devices()
        for idx, dev in enumerate(devices):
            if "BlackHole" in dev["name"] and int(dev.get("max_input_channels", 0)) > 0:
                return idx, dev["name"]
        return None, None

    def _run_mac(self) -> None:
        while not self.stop_event.is_set():
            device_id, device_name = self._find_blackhole_device()
            if device_id is None:
                msg = "BlackHole input device not found"
                self.state.set_error(msg)
                print(f"[audio] {msg}. {self.RESTART_WAIT:.0f}秒後に再検索します。", file=sys.stderr)
                self.detector.force_stop()
                self.stop_event.wait(self.RESTART_WAIT)
                continue

            self.state.set_error("")
            print(f"Detected BlackHole: {device_name} = {device_id}")

            def callback(indata, frames, time_info, status) -> None:  # noqa: ARG001
                if status:
                    print(f"Audio status: {status}", file=sys.stderr)
                self.detector.process_block(indata[:, 0])

            try:
                with sd.InputStream(
                    device=device_id,
                    channels=1,
                    samplerate=SAMPLERATE,
                    blocksize=BLOCKSIZE,
                    callback=callback,
                ):
                    while not self.stop_event.wait(0.1):
                        pass
                    return
            except Exception as exc:
                msg = f"InputStream error: {type(exc).__name__}: {exc}"
                self.state.set_error(msg)
                print(f"[audio] {msg}", file=sys.stderr)
                self.detector.force_stop()
                self.stop_event.wait(self.RESTART_WAIT)

    # ----- Windows / Linux / soundcard -----
    @staticmethod
    def _is_loopback_mic(mic) -> bool:
        if getattr(mic, "isloopback", False):
            return True
        return str(getattr(mic, "id", "")).endswith(".monitor")

    def _find_loopback_mic(self):
        mics = sc.all_microphones(include_loopback=True)

        if self.options.input_hint:
            hint = self.options.input_hint.lower()
            for mic in mics:
                if hint in mic.name.lower():
                    return mic
            return None

        loopbacks = [mic for mic in mics if self._is_loopback_mic(mic)]
        try:
            speaker_name = sc.default_speaker().name
        except Exception:
            speaker_name = None

        if speaker_name:
            for mic in loopbacks:
                if speaker_name in mic.name:
                    return mic

        return loopbacks[0] if loopbacks else None

    def _run_soundcard(self) -> None:
        while not self.stop_event.is_set():
            mic = self._find_loopback_mic()
            if mic is None:
                msg = "PC再生音のループバック/モニター入力が見つかりません"
                self.state.set_error(msg)
                print(f"[audio] {msg}. {self.RESTART_WAIT:.0f}秒後に再検索します。", file=sys.stderr)
                self.detector.force_stop()
                self.stop_event.wait(self.RESTART_WAIT)
                continue

            self.state.set_error("")
            print(f"Detected loopback: {mic.name}")

            try:
                with mic.recorder(samplerate=SAMPLERATE, blocksize=BLOCKSIZE) as recorder:
                    while not self.stop_event.is_set():
                        data = recorder.record(numframes=BLOCKSIZE)
                        if data.ndim > 1 and data.shape[1] > 1:
                            mono = data.mean(axis=1)
                        else:
                            mono = data.reshape(-1)
                        self.detector.process_block(mono)
            except Exception as exc:
                msg = f"capture error: {type(exc).__name__}: {exc}"
                self.state.set_error(msg)
                print(f"[audio] {msg}", file=sys.stderr)
                self.detector.force_stop()
                self.stop_event.wait(self.RESTART_WAIT)

    def _keepalive_loop(self) -> None:
        zeros = np.zeros((BLOCKSIZE, 1), dtype="float32")
        while not self.stop_event.is_set():
            try:
                speaker = sc.default_speaker()
                with speaker.player(samplerate=SAMPLERATE, blocksize=BLOCKSIZE) as player:
                    while not self.stop_event.is_set():
                        player.play(zeros)
            except Exception as exc:
                print(f"[keepalive] {type(exc).__name__}: {exc}", file=sys.stderr)
                self.stop_event.wait(5.0)


class KariPomWindow(QWidget):
    """320x240の実機座標系をそのまま描くPC版かりポム。"""

    def __init__(self, audio_state: SharedAudioState, audio_engine: AudioEngine, scale: int) -> None:
        super().__init__()
        self.audio_state = audio_state
        self.audio_engine = audio_engine
        self.scale_factor = scale

        self.eye_mode = "open"  # open / blink
        self.nose_offset = 0
        self.mouth_open = False
        self.mouth_mx = NOSE_X
        self.mouth_my = NOSE_Y + 26
        self.mouth_mw = 18
        self.mouth_mh = 12
        self.was_speaking = False

        now = self._now_ms()
        self.next_nose_at = now + random.randrange(*NOSE_INTERVAL_MS)
        self.next_blink_check_at = now + random.randrange(*BLINK_INTERVAL_MS)
        self.next_mouth_at = now + MOUTH_PAKU_MS
        self.blink_events: list[tuple[int, str]] = []

        self.setWindowTitle("KariPom Desktop v0.2")
        self.setFixedSize(CANVAS_W * scale, CANVAS_H * scale)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    @staticmethod
    def _now_ms() -> int:
        return int(time.monotonic() * 1000)

    def _tick(self) -> None:
        now = self._now_ms()
        speaking, _rms, error = self.audio_state.snapshot()

        # 音声状態に合わせて実機同様、発話中だけ140ms周期で口を開閉する。
        if speaking != self.was_speaking:
            self.was_speaking = speaking
            if speaking:
                self.mouth_open = False
                self.next_mouth_at = now
            else:
                self.mouth_open = False

        if speaking and now >= self.next_mouth_at:
            self.next_mouth_at = now + MOUTH_PAKU_MS
            self.mouth_open = not self.mouth_open
            if self.mouth_open:
                self._randomize_talk_mouth()

        # 鼻ヒクヒク: 実機同様 ±1px を120〜280msで交互に動かす。
        if now >= self.next_nose_at:
            self.nose_offset = -1 if self.nose_offset >= 0 else 1
            self.next_nose_at = now + random.randrange(*NOSE_INTERVAL_MS)

        # 瞬き: 3〜10秒ごとの判定で45%。8%で二重瞬き。
        if now >= self.next_blink_check_at:
            self.next_blink_check_at = now + random.randrange(*BLINK_INTERVAL_MS)
            if random.random() < BLINK_PROBABILITY:
                self._schedule_blink(now)

        while self.blink_events and now >= self.blink_events[0][0]:
            _, mode = self.blink_events.pop(0)
            self.eye_mode = mode

        title = "KariPom Desktop v0.2"
        if error:
            title += "  [Audio Error]"
        elif speaking:
            title += "  [Talking]"
        self.setWindowTitle(title)

        self.update()

    def _schedule_blink(self, now: int) -> None:
        self.blink_events.append((now, "blink"))
        self.blink_events.append((now + BLINK_CLOSED_MS, "open"))

        if random.random() < DOUBLE_BLINK_PROBABILITY:
            second_close = now + BLINK_CLOSED_MS + DOUBLE_BLINK_GAP_MS
            self.blink_events.append((second_close, "blink"))
            self.blink_events.append((second_close + BLINK_CLOSED_MS, "open"))

        self.blink_events.sort(key=lambda item: item[0])

    def _randomize_talk_mouth(self) -> None:
        # ino: random(-1, 2) * 2 -> -2 / 0 / +2
        self.mouth_mx = NOSE_X + random.choice((-2, 0, 2))
        self.mouth_my = NOSE_Y + 26 + random.choice((-2, 0, 2))
        self.mouth_mw = 18 + random.choice((-2, 0, 2))
        self.mouth_mh = 12 + random.choice((-2, 0, 2))

    @staticmethod
    def _draw_thick_line(
        painter: QPainter,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        thickness: int,
        color: QColor,
    ) -> None:
        pen = QPen(color)
        pen.setWidth(thickness)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(x0, y0, x1, y1)

    @staticmethod
    def _fill_ellipse(painter: QPainter, cx: int, cy: int, rx: int, ry: int, color: QColor) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)

    @staticmethod
    def _fill_circle(painter: QPainter, cx: int, cy: int, radius: int, color: QColor) -> None:
        KariPomWindow._fill_ellipse(painter, cx, cy, radius, radius, color)

    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        # CoreS3の320x240座標を整数倍で拡大。アンチエイリアスは切って輪郭を素直に保つ。
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("white"))
        painter.scale(self.scale_factor, self.scale_factor)

        black = QColor("black")
        red = QColor("red")

        # 目: ino faceShapeOpenEyes / faceShapeBlinkEyes と同座標。
        if self.eye_mode == "blink":
            self._draw_thick_line(painter, 72, 90, 108, 90, 6, black)
            self._draw_thick_line(painter, 212, 90, 248, 90, 6, black)
        else:
            self._fill_circle(painter, 90, 90, 20, black)
            self._fill_circle(painter, 230, 90, 20, black)

        # 鼻＋口。
        self._fill_ellipse(painter, NOSE_X, NOSE_Y + self.nose_offset, 18, 12, black)

        if self.mouth_open and self.was_speaking:
            self._draw_thick_line(
                painter,
                NOSE_X,
                NOSE_Y + self.nose_offset + 8,
                NOSE_X,
                NOSE_Y + 25,
                6,
                black,
            )
            self._fill_ellipse(
                painter,
                self.mouth_mx,
                self.mouth_my,
                self.mouth_mw,
                self.mouth_mh,
                red,
            )
        else:
            self._draw_thick_line(
                painter,
                NOSE_X,
                NOSE_Y + self.nose_offset + 8,
                NOSE_X,
                NOSE_Y + 22,
                6,
                black,
            )
            self._draw_thick_line(painter, NOSE_X, NOSE_Y + 22, NOSE_X - 20, NOSE_Y + 32, 6, black)
            self._draw_thick_line(painter, NOSE_X, NOSE_Y + 22, NOSE_X + 20, NOSE_Y + 32, 6, black)

        painter.end()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.audio_engine.stop()
        event.accept()


def print_devices() -> None:
    if IS_MAC:
        print(sd.query_devices())
        return

    print("=== 音声デバイス一覧（[loopback] がPC再生音の候補）===")
    for mic in sc.all_microphones(include_loopback=True):
        loopback = getattr(mic, "isloopback", False) or str(getattr(mic, "id", "")).endswith(".monitor")
        mark = "[loopback]" if loopback else "[mic]     "
        print(f"{mark} {mic.name}")
    try:
        print(f"既定スピーカー: {sc.default_speaker().name}")
    except Exception as exc:
        print(f"既定スピーカー: 取得失敗 ({exc})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KariPom Desktop v0.2")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"口パク開始RMSしきい値（既定 {DEFAULT_THRESHOLD}）")
    parser.add_argument("--input", dest="input_hint", default=None,
                        help="Windows/Linuxで使用する入力デバイス名の一部")
    parser.add_argument("--no-keepalive", action="store_true",
                        help="Windows/Linuxの無音キープアライブ出力を無効化")
    parser.add_argument("--list-devices", action="store_true",
                        help="音声デバイス一覧を表示して終了")
    parser.add_argument("--scale", type=int, choices=(1, 2, 3, 4), default=2,
                        help="320x240画面の整数拡大率（既定 2）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_devices:
        print_devices()
        return 0

    print("=== KariPom Desktop v0.2 ===")
    print(f"OS: {OS_NAME}")
    print(f"Threshold: {args.threshold}")
    if IS_MAC:
        print("Audio: BlackHole")
    else:
        print("Audio: WASAPI Loopback" if IS_WINDOWS else "Audio: PulseAudio/PipeWire monitor")
        print(f"Keepalive: {'OFF' if args.no_keepalive else 'ON'}")
    print("CoreS3 / IP設定は不要です。")

    state = SharedAudioState()
    options = AudioOptions(
        threshold=args.threshold,
        input_hint=args.input_hint,
        keepalive=not args.no_keepalive,
    )
    engine = AudioEngine(state, options)

    app = QApplication(sys.argv[:1])
    window = KariPomWindow(state, engine, args.scale)
    window.show()
    engine.start()

    # GUIと音声解析を先に起動し、その後に起動時処理だけを追加する。
    # ChatGPTの音声会話ボタン自体は自動操作しない。
    QTimer.singleShot(BROWSER_OPEN_DELAY_MS, open_chatgpt_page)
    QTimer.singleShot(GREETING_DELAY_MS, speak_startup_greeting)

    result = app.exec_()
    engine.stop()
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
