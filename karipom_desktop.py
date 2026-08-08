#!/usr/bin/env python3
# =============================================================================
# KariPom Desktop v0.3 - 7 Visualizers + Face
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
import math
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

# ===== EQ Classic 試験移植：KariPom Talk/Companion と同じ8バンドFFT =====
FFT_UPDATE_INTERVAL = 0.08
FFT_BANDS_HZ = [60, 120, 250, 500, 1000, 2000, 4000, 8000, 16000]
FFT_DB_RANGES = [
    (-60, -10),
    (-62, -12),
    (-63, -13),
    (-65, -15),
    (-65, -15),
    (-70, -20),
    (-78, -28),
    (-85, -35),
]
FFT_ATTACK = 0.6
FFT_DECAY = 0.18

# CoreS3 Graphic EQ (Classic) と同じ表示ゲイン・8色・レイアウト。
EQ_GAIN8 = [1.6, 1.6, 1.6, 1.6, 1.7, 1.8, 2.0, 2.2]
EQ_COLORS_RGB = [
    (0, 0, 255),       # 0x001F blue
    (0, 255, 255),     # 0x07FF cyan
    (0, 255, 0),       # 0x07E0 green
    (173, 255, 41),    # 0xAFE5 yellow-green (RGB565 -> RGB888)
    (255, 255, 0),     # 0xFFE0 yellow
    (255, 165, 0),     # 0xFD20 orange
    (255, 0, 0),       # 0xF800 red
    (255, 0, 255),     # 0xF81F magenta
]
EQ_COL_W = 40
EQ_BAR_MX = 3
EQ_BAR_W = 34
EQ_BAR_MAX_H = 160
EQ_BAR_BOTTOM_Y = 200

# ===== Audio Visualizer modes =====
VIS_MODES = [
    "Face",
    "EQ Classic",
    "Audio Halo",
    "Mirror Wave",
    "8-Lane Rhythm",
    "Kaleidoscope",
    "Analog VU",
    "Tetromino Dance",
]

SCENE_TOP = 48
SPECTRUM_COLORS = [QColor(*rgb) for rgb in EQ_COLORS_RGB]

# Mirror Wave
MW_CY, MW_YMIN, MW_YMAX, MW_SX, MW_EDGE = 143, 50, 236, 4, 6
MW_PAL = [
    QColor(0,230,255), QColor(0,140,255), QColor(150,90,255),
    QColor(255,60,220), QColor(255,200,0), QColor(60,255,140),
]

# 8-Lane Rhythm
RHY_ROW_H = 4
RHY_HIST_ROWS = (CANVAS_H - SCENE_TOP) // RHY_ROW_H if "CANVAS_H" in globals() else 48

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
RHY_HIST_ROWS = (CANVAS_H - SCENE_TOP) // RHY_ROW_H

# ===== 起動時処理 =====
CHATGPT_URL = "https://chatgpt.com/"
STARTUP_GREETING = "こんにちは、かりポムです！ ブラウザでChatGPTも開きました。ChatGPTの音声ボタンを押して会話を始めましょう。かりポム画面をクリックすると、顔のオーディオビジュアライザーを切り替えられます。"
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
    """音声スレッドからGUIへ渡す共有状態。既存状態＋EQ用8バンド。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._speaking = False
        self._rms = 0.0
        self._error = ""
        self._fft = np.zeros(8, dtype=np.float32)

    def set_audio(self, speaking: bool, rms: float) -> None:
        with self._lock:
            self._speaking = speaking
            self._rms = rms

    def set_fft(self, levels) -> None:
        arr = np.asarray(levels, dtype=np.float32).reshape(-1)
        if arr.size != 8:
            return
        with self._lock:
            self._fft = arr.copy()

    def set_error(self, message: str) -> None:
        with self._lock:
            self._error = message

    def snapshot(self) -> tuple[bool, float, str, np.ndarray]:
        with self._lock:
            return self._speaking, self._rms, self._error, self._fft.copy()


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

        # EQ Classic用FFT。KariPom Talk/Companionの設定をそのまま使用する。
        self._fft_last = 0.0
        self._fft_window = np.hanning(BLOCKSIZE)
        freqs = np.fft.rfftfreq(BLOCKSIZE, 1.0 / SAMPLERATE)
        self._fft_bins = [
            np.where((freqs >= lo) & (freqs < hi))[0]
            for lo, hi in zip(FFT_BANDS_HZ[:-1], FFT_BANDS_HZ[1:])
        ]
        self._fft_levels = np.zeros(8, dtype=float)

    def _process_fft(self, mono_block: np.ndarray) -> None:
        now = time.time()
        if now - self._fft_last < FFT_UPDATE_INTERVAL:
            return
        self._fft_last = now

        block = np.asarray(mono_block, dtype=np.float32).reshape(-1)
        if block.size != BLOCKSIZE:
            return

        spec = np.abs(np.fft.rfft(block * self._fft_window)) / (BLOCKSIZE / 2)
        band_db = np.array([
            20.0 * np.log10(np.sqrt(np.mean(spec[idx] ** 2)) + 1e-12)
            for idx in self._fft_bins
        ])
        db_min = np.array([r[0] for r in FFT_DB_RANGES])
        db_max = np.array([r[1] for r in FFT_DB_RANGES])
        target = np.clip((band_db - db_min) / (db_max - db_min), 0.0, 1.0) * 100.0
        coef = np.where(target > self._fft_levels, FFT_ATTACK, FFT_DECAY)
        self._fft_levels = self._fft_levels + coef * (target - self._fft_levels)
        self.state.set_fft([int(v + 0.5) for v in self._fft_levels])

    def _process_audio_block(self, mono_block: np.ndarray) -> None:
        # 既存の口パク判定を先に処理し、同じPCMをFFTへ分岐する。
        self.detector.process_block(mono_block)
        self._process_fft(mono_block)

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
                self._process_audio_block(indata[:, 0])

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
                        self._process_audio_block(mono)
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

        # 1つの320x240キャンバスを8モードで順送りする。
        # 0=Face, 1..7=本家Visualizer。
        self.display_mode = 0

        # Visualizer専用状態
        self.halo_env = np.zeros(48, dtype=float)
        self.halo_agc_peak = 0.0
        self.mw_agc_peak = 0.0
        self.mw_phase = 0.0
        self.rhythm_hist = np.zeros((RHY_HIST_ROWS, 8), dtype=np.float32)
        self.rhythm_prev = np.zeros(8, dtype=float)
        self.rhythm_last_step = 0
        self.kal_rot = 0.0
        self.kal_pulse = 0.0
        self.kal_bass_avg = 0.0
        self.avu_needle = np.zeros(8, dtype=float)
        self.tetro_pieces = []
        self.tetro_last_spawn = 0
        self.tetro_bass_avg = 0.0

        now = self._now_ms()
        self.next_nose_at = now + random.randrange(*NOSE_INTERVAL_MS)
        self.next_blink_check_at = now + random.randrange(*BLINK_INTERVAL_MS)
        self.next_mouth_at = now + MOUTH_PAKU_MS
        self.blink_events: list[tuple[int, str]] = []

        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowTitle("KariPom Desktop v0.3  [Face]  • Always on top")
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
        speaking, _rms, error, _fft = self.audio_state.snapshot()

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

        # Visualizer animation state
        fft01 = np.clip(_fft / 100.0, 0.0, 1.0)
        level = float(np.mean(fft01))
        bass = float(np.mean(fft01[:2]))

        # Mirror color phase
        self.mw_phase = (self.mw_phase + 0.02 + level * 0.30) % 6.0

        # Kaleidoscope bass pulse
        self.kal_bass_avg += (bass - self.kal_bass_avg) * 0.15
        if bass > self.kal_bass_avg * 1.3 + 0.04:
            self.kal_pulse = 1.0
        self.kal_pulse *= 0.65
        self.kal_rot = (self.kal_rot + 0.014) % (2.0 * math.pi)

        # Analog VU needle smoothing
        gained = np.clip(fft01 * np.array(EQ_GAIN8), 0.0, 1.3)
        for i in range(8):
            target = float(min(1.0, gained[i] / 1.30))
            coef = 0.60 if target > self.avu_needle[i] else 0.14
            self.avu_needle[i] += (target - self.avu_needle[i]) * coef

        self._update_rhythm(now, _fft)
        self._update_tetromino(now, fft01, level, bass)

        mode_label = VIS_MODES[self.display_mode]
        title = f"KariPom Desktop v0.3  [{mode_label}]  • Always on top"
        if error:
            title += "  [Audio Error]"
        elif speaking:
            title += "  [Talking]"
        self.setWindowTitle(title)

        self.update()

    def mousePressEvent(self, event) -> None:
        """左クリックで Face → 7 Visualizers → Face を順送りする。"""
        if event.button() == Qt.LeftButton:
            self.display_mode = (self.display_mode + 1) % len(VIS_MODES)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    @staticmethod
    def _interp_band(fft_levels, p: float) -> float:
        """8バンドを0..1位置へ線形補間し、0..1で返す。"""
        a = np.clip(np.asarray(fft_levels, dtype=float) / 100.0, 0.0, 1.0)
        x = np.clip(p, 0.0, 1.0) * 7.0
        i = int(math.floor(x))
        j = min(7, i + 1)
        t = x - i
        return float(a[i] * (1.0 - t) + a[j] * t)

    @staticmethod
    def _tint(color: QColor, white_pct: float) -> QColor:
        t = max(0.0, min(1.0, white_pct / 100.0))
        return QColor(
            int(color.red()   * (1-t) + 255*t),
            int(color.green() * (1-t) + 255*t),
            int(color.blue()  * (1-t) + 255*t),
        )

    def _update_rhythm(self, now: int, fft_levels) -> None:
        # CoreS3は40ms。GUIタイマー20msなので40msごとに1行進める。
        if now - self.rhythm_last_step < 40:
            return
        self.rhythm_last_step = now
        self.rhythm_hist[1:] = self.rhythm_hist[:-1]
        self.rhythm_hist[0] = 0
        vals = np.clip(np.asarray(fft_levels, dtype=float) * np.asarray(EQ_GAIN8), 0, 100)
        rise = vals - self.rhythm_prev
        for i in range(8):
            if vals[i] >= 12 and rise[i] >= 10:
                self.rhythm_hist[0, i] = vals[i]
        self.rhythm_prev = vals

    def _spawn_tetromino(self, color: QColor, now: int) -> None:
        if len(self.tetro_pieces) >= 4:
            return
        shape = random.choice(("O", "I", "L"))
        self.tetro_pieces.append({
            "shape": shape,
            "cx": float(random.randint(64, 256)),
            "cy": float(SCENE_TOP - 64),
            "vy": 0.6,
            "angle": 0.0,
            "target": 0.0,
            "color": color,
            "next_rot": now + random.randint(900, 1700),
            "slide_to": None,
            "slide_start": 0,
            "slide_dur": 0,
            "next_slide": now + random.randint(1200, 2200),
        })

    def _update_tetromino(self, now: int, fft01, level: float, bass: float) -> None:
        # band attackで出現。無音でも2.5秒に1個を保証。
        vals = np.clip(fft01 * np.asarray(EQ_GAIN8) * 100.0, 0, 100)
        if not hasattr(self, "_tetro_prev"):
            self._tetro_prev = np.zeros(8)
        for i in range(8):
            if vals[i] >= 15 and vals[i] - self._tetro_prev[i] >= 12 and now - self.tetro_last_spawn >= 220:
                self._spawn_tetromino(SPECTRUM_COLORS[i], now)
                self.tetro_last_spawn = now
                break
        self._tetro_prev = vals
        if not self.tetro_pieces and now - self.tetro_last_spawn >= 2500:
            self._spawn_tetromino(SPECTRUM_COLORS[4], now)
            self.tetro_last_spawn = now

        self.tetro_bass_avg += (bass - self.tetro_bass_avg) * 0.15
        bass_hit = bass > self.tetro_bass_avg * 1.3 + 0.04

        for p in list(self.tetro_pieces):
            p["cy"] += p["vy"] * (1.0 + level * 2.2)
            if abs(p["target"] - p["angle"]) > 1e-4:
                step = 0.2618 if p["target"] > p["angle"] else -0.2618
                if abs(p["target"] - p["angle"]) <= abs(step):
                    p["angle"] = p["target"]
                else:
                    p["angle"] += step
            elif p["shape"] != "O" and (now >= p["next_rot"] or (bass_hit and random.random() < 0.25)):
                p["target"] = p["angle"] + math.pi / 2
                p["next_rot"] = now + random.randint(900, 1700)

            if p["slide_to"] is not None:
                t = min(1.0, (now - p["slide_start"]) / max(1, p["slide_dur"]))
                e = t*t*(3-2*t)
                p["cx"] = p["slide_from"] + (p["slide_to"] - p["slide_from"]) * e
                if t >= 1.0:
                    p["slide_to"] = None
            elif now >= p["next_slide"]:
                if random.random() < 0.5:
                    target = p["cx"] + random.choice((-1,1)) * random.choice((40,80))
                    target = max(64.0, min(256.0, target))
                    if abs(target - p["cx"]) > 4:
                        p["slide_from"] = p["cx"]
                        p["slide_to"] = target
                        p["slide_start"] = now
                        p["slide_dur"] = random.randint(300,500)
                p["next_slide"] = now + random.randint(1200,2200)

            if p["cy"] - 64 > CANVAS_H:
                self.tetro_pieces.remove(p)

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

    def _draw_face(self, painter: QPainter) -> None:
        black = QColor("black")
        red = QColor("red")
        if self.eye_mode == "blink":
            self._draw_thick_line(painter, 72, 90, 108, 90, 6, black)
            self._draw_thick_line(painter, 212, 90, 248, 90, 6, black)
        else:
            self._fill_circle(painter, 90, 90, 20, black)
            self._fill_circle(painter, 230, 90, 20, black)

        self._fill_ellipse(painter, NOSE_X, NOSE_Y + self.nose_offset, 18, 12, black)
        if self.mouth_open and self.was_speaking:
            self._draw_thick_line(painter, NOSE_X, NOSE_Y + self.nose_offset + 8, NOSE_X, NOSE_Y + 25, 6, black)
            self._fill_ellipse(painter, self.mouth_mx, self.mouth_my, self.mouth_mw, self.mouth_mh, red)
        else:
            self._draw_thick_line(painter, NOSE_X, NOSE_Y + self.nose_offset + 8, NOSE_X, NOSE_Y + 22, 6, black)
            self._draw_thick_line(painter, NOSE_X, NOSE_Y + 22, NOSE_X - 20, NOSE_Y + 32, 6, black)
            self._draw_thick_line(painter, NOSE_X, NOSE_Y + 22, NOSE_X + 20, NOSE_Y + 32, 6, black)

    def _draw_eq(self, painter: QPainter, fft_levels) -> None:
        painter.setPen(Qt.NoPen)
        for i in range(8):
            disp = max(0, min(100, int(round(float(fft_levels[i]) * EQ_GAIN8[i]))))
            h = disp * EQ_BAR_MAX_H // 100
            if h > 0:
                painter.setBrush(QBrush(SPECTRUM_COLORS[i]))
                painter.drawRect(i * EQ_COL_W + EQ_BAR_MX, EQ_BAR_BOTTOM_Y - h, EQ_BAR_W, h)
        # 顔を最後に描いて最前面へ。
        self._draw_face(painter)

    def _draw_halo(self, painter: QPainter, fft_levels) -> None:
        vals = np.clip(np.asarray(fft_levels, dtype=float) / 100.0, 0, 1)
        peak = float(vals.max())
        self.halo_agc_peak += (peak - self.halo_agc_peak) * (1.0 if peak > self.halo_agc_peak else 0.03)
        ref = max(0.21, self.halo_agc_peak)
        agc = 0.64 / ref
        cx, cy = 160.0, 143.0
        for j in range(48):
            rel = j / 47.0
            fold = min(rel * 2.0, 2.0 - rel * 2.0)
            v = min(1.0, self._interp_band(fft_levels, fold) * agc)
            if v < 0.02:
                v = 0.0
            elif v < 0.065:
                v *= (v - 0.02) / 0.045
            v = v ** 0.48 if v > 0 else 0
            a = math.radians(90 + j * 360.0 / 48.0)
            dx, dy = math.cos(a), math.sin(a)
            rin = 1.0 / math.sqrt((dx/84.0)**2 + (dy/40.0)**2)
            rout = 1.0 / math.sqrt((dx/157.0)**2 + (dy/93.0)**2)
            r2 = rin + (rout-rin)*v
            base = self._spectrum_color(fold)
            for frac, tint, width in ((0.45,82,8),(0.70,58,7),(0.88,28,6),(1.0,0,4)):
                rr = rin + (r2-rin)*frac
                pen = QPen(self._tint(base, tint))
                pen.setWidth(width)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawLine(int(cx+dx*rin), int(cy+dy*rin), int(cx+dx*rr), int(cy+dy*rr))
        self._draw_face(painter)

    @staticmethod
    def _spectrum_color(p: float) -> QColor:
        p = max(0.0, min(1.0, p)) * 7.0
        i = int(math.floor(p))
        j = min(7, i+1)
        t = p - i
        a, b = SPECTRUM_COLORS[i], SPECTRUM_COLORS[j]
        return QColor(
            int(a.red()*(1-t)+b.red()*t),
            int(a.green()*(1-t)+b.green()*t),
            int(a.blue()*(1-t)+b.blue()*t),
        )

    def _draw_mirror(self, painter: QPainter, fft_levels) -> None:
        vals = np.asarray(fft_levels, dtype=float)/100.0
        peak = float(vals.max())
        self.mw_agc_peak += (peak-self.mw_agc_peak)*(1.0 if peak>self.mw_agc_peak else 0.03)
        agc = 0.80 / max(0.20, self.mw_agc_peak)
        max_half = (MW_YMAX-MW_YMIN)//2
        for c in range(81):
            x = min(319, c*MW_SX)
            p = x/319.0
            v = min(1.0, self._interp_band(fft_levels,p)*agc)
            h = max(2, int((v**0.45)*max_half))
            top, bot = max(MW_YMIN,MW_CY-h), min(MW_YMAX,MW_CY+h)
            hue = MW_PAL[int((c*0.10+self.mw_phase)) % len(MW_PAL)]
            body = self._tint(hue,18)
            edge = self._tint(hue,62)
            painter.setPen(Qt.NoPen)
            if bot-top > 2*MW_EDGE:
                painter.setBrush(QBrush(body)); painter.drawRect(x,top+MW_EDGE,MW_SX,bot-top-2*MW_EDGE)
            painter.setBrush(QBrush(edge)); painter.drawRect(x,top,MW_SX,MW_EDGE); painter.drawRect(x,bot-MW_EDGE,MW_SX,MW_EDGE)
        self._draw_face(painter)

    def _draw_rhythm(self, painter: QPainter) -> None:
        painter.setPen(Qt.NoPen)
        for r in range(RHY_HIST_ROWS):
            if (r % 4) >= 3:
                continue
            y = SCENE_TOP + r*RHY_ROW_H
            for i in range(8):
                disp = float(self.rhythm_hist[r,i])
                if disp <= 0:
                    continue
                tint = max(0,min(88,100-disp))
                col = self._tint(SPECTRUM_COLORS[i],tint)
                painter.setBrush(QBrush(col))
                painter.drawRect(i*40+3,y,34,16)
        # 顔を最後に描いて最前面へ。
        self._draw_face(painter)

    def _draw_kaleido(self, painter: QPainter, fft_levels) -> None:
        painter.fillRect(0, SCENE_TOP, CANVAS_W, CANVAS_H-SCENE_TOP, QColor("black"))
        vals = np.clip(np.asarray(fft_levels,dtype=float)/100.0,0,1)
        mean = max(0.05,float(vals.mean()))
        pulse = 1.0 + 0.10*self.kal_pulse
        painter.save()
        painter.setClipRect(0,SCENE_TOP,CANVAS_W,CANVAS_H-SCENE_TOP)
        cx,cy=160,144
        radii=[35,75,125,190]
        prev=0
        for ring in range(4):
            v1=(vals[ring*2]/mean-1.0)
            v2=(vals[ring*2+1]/mean-1.0)
            r=radii[ring]*pulse + max(-22,min(22,v1*22))
            angofs=max(-0.35,min(0.35,v2*0.25))
            for sec in range(6):
                base=self.kal_rot + sec*math.pi/3
                for facet in range(4):
                    a0=base + facet*(math.pi/12) + angofs*(facet%2)
                    a1=base + (facet+1)*(math.pi/12) + angofs*((facet+1)%2)
                    p0=(int(cx+math.cos(a0)*prev),int(cy+math.sin(a0)*prev))
                    p1=(int(cx+math.cos(a1)*prev),int(cy+math.sin(a1)*prev))
                    p2=(int(cx+math.cos(a1)*r),int(cy+math.sin(a1)*r))
                    p3=(int(cx+math.cos(a0)*r),int(cy+math.sin(a0)*r))
                    col=self._spectrum_color((ring*2+(facet%2))/7.0)
                    painter.setPen(Qt.NoPen); painter.setBrush(QBrush(col))
                    painter.drawPolygon(*[self._qp(*p) for p in (p0,p1,p2,p3)])
            prev=r
        painter.restore()
        # 黒背景でも顔を読めるよう白リムを付ける。
        self._draw_face_with_rim(painter)

    @staticmethod
    def _qp(x,y):
        from PyQt5.QtCore import QPoint
        return QPoint(int(x),int(y))

    def _draw_face_with_rim(self,painter):
        # 白で一回太め→通常黒/赤を重ねる簡易リム
        white=QColor("white")
        if self.eye_mode=="blink":
            self._draw_thick_line(painter,72,90,108,90,10,white); self._draw_thick_line(painter,212,90,248,90,10,white)
        else:
            self._fill_circle(painter,90,90,24,white); self._fill_circle(painter,230,90,24,white)
        self._fill_ellipse(painter,NOSE_X,NOSE_Y+self.nose_offset,22,16,white)
        self._draw_face(painter)

    def _draw_analog_vu(self, painter: QPainter) -> None:
        painter.fillRect(0,SCENE_TOP,CANVAS_W,CANVAS_H-SCENE_TOP,QColor("black"))
        labels=("60","120","250","500","1k","2k","4k","8k")
        cream=QColor(245,230,200); frame=QColor(35,35,35); red=QColor(240,0,0)
        for b in range(8):
            col,row=b%4,b//4
            x0,y0=col*80,row*96+SCENE_TOP
            px,py=x0+40,y0+74
            painter.setPen(QPen(QColor(82,82,82),2)); painter.setBrush(QBrush(cream))
            painter.drawRoundedRect(x0+3,y0+8,74,76,5,5)
            painter.setPen(QPen(frame,1))
            # arc/ticks
            for t in range(7):
                ang=math.radians(-55+t*(110/6))
                x1=px+math.sin(ang)*34; y1=py-math.cos(ang)*34
                x2=px+math.sin(ang)*42; y2=py-math.cos(ang)*42
                painter.drawLine(int(x1),int(y1),int(x2),int(y2))
            # red zone
            painter.setPen(QPen(QColor(190,0,0),3))
            for deg in np.linspace(28,55,12):
                a=math.radians(deg)
                painter.drawLine(int(px+math.sin(a)*38),int(py-math.cos(a)*38),int(px+math.sin(a)*42),int(py-math.cos(a)*42))
            # needle
            a=math.radians(-55+110*float(self.avu_needle[b]))
            painter.setPen(QPen(red,2))
            painter.drawLine(px,py,int(px+math.sin(a)*38),int(py-math.cos(a)*38))
            painter.setBrush(QBrush(frame)); painter.setPen(Qt.NoPen); painter.drawEllipse(px-3,py-3,6,6)
            painter.setPen(QPen(frame,1)); painter.drawText(x0+29,y0+40,labels[b])
        self._draw_face_with_rim(painter)

    def _draw_tetromino(self, painter: QPainter) -> None:
        painter.save(); painter.setClipRect(0,SCENE_TOP,CANVAS_W,CANVAS_H-SCENE_TOP)
        shapes={"O":[(0,0),(1,0),(0,1),(1,1)],"I":[(0,0),(1,0),(2,0)],"L":[(0,0),(1,0),(0,1)]}
        for p in self.tetro_pieces:
            cells=shapes[p["shape"]]
            w=3 if p["shape"]=="I" else 2; h=1 if p["shape"]=="I" else 2
            ca,sa=math.cos(p["angle"]),math.sin(p["angle"])
            for col,row in cells:
                lcx=(col-(w-1)/2)*40; lcy=(row-(h-1)/2)*40
                half=19
                pts=[]
                for lx,ly in ((lcx-half,lcy-half),(lcx+half,lcy-half),(lcx+half,lcy+half),(lcx-half,lcy+half)):
                    rx=lx*ca-ly*sa; ry=lx*sa+ly*ca
                    pts.append(self._qp(p["cx"]+rx,p["cy"]+ry))
                painter.setPen(Qt.NoPen); painter.setBrush(QBrush(p["color"])); painter.drawPolygon(*pts)
        painter.restore()
        self._draw_face(painter)

    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("white"))
        painter.scale(self.scale_factor, self.scale_factor)

        _speaking, _rms, _error, fft_levels = self.audio_state.snapshot()

        if self.display_mode == 0:
            self._draw_face(painter)
        elif self.display_mode == 1:
            self._draw_eq(painter, fft_levels)
        elif self.display_mode == 2:
            self._draw_halo(painter, fft_levels)
        elif self.display_mode == 3:
            self._draw_mirror(painter, fft_levels)
        elif self.display_mode == 4:
            self._draw_rhythm(painter)
        elif self.display_mode == 5:
            self._draw_kaleido(painter, fft_levels)
        elif self.display_mode == 6:
            self._draw_analog_vu(painter)
        elif self.display_mode == 7:
            self._draw_tetromino(painter)

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
    parser = argparse.ArgumentParser(description="KariPom Desktop v0.3 - 7 Visualizers + Face")
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

    print("=== KariPom Desktop v0.3 - 7 Visualizers + Face ===")
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
