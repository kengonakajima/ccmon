#!/usr/bin/env python3
"""
CCMon - Claude/Codex/Cursor/Opencode/Kimi/Agy Monitor
macOS用のコーディングエージェントの活動状況を音で表現するモニターツール
"""

import os
import json
import sys
import time
import threading
import subprocess
import tempfile
import wave
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

# Rich: TUI 表示（必須）。無い場合はエラー終了
try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
except Exception:
    print("エラー: Richが見つかりません。pip install rich でインストールしてください。")
    sys.exit(1)

# デバッグ出力制御
DEBUG_MODE = str(os.environ.get("CCMON_DEBUG", "")).lower() not in ("", "0", "false", "no")

def dprint(*args, **kwargs):
    if DEBUG_MODE:
        print(*args, **kwargs)

# 設定ファイルの永続化（~/.ccmon/settings.json）
CONFIG_DIR = Path.home() / ".ccmon"
CONFIG_FILE = CONFIG_DIR / "settings.json"

def _load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        dprint(f"設定読み込みエラー: {e}")
    return {}

def _save_config(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        dprint(f"設定保存エラー: {e}")

def load_sound_enabled() -> bool:
    cfg = _load_config()
    val = cfg.get("sound_enabled")
    return bool(val) if isinstance(val, bool) else True

def save_sound_enabled(enabled: bool) -> None:
    cfg = _load_config()
    cfg["sound_enabled"] = bool(enabled)
    _save_config(cfg)

def load_volume_level() -> int:
    cfg = _load_config()
    val = cfg.get("volume_level")
    try:
        lvl = int(val)
    except Exception:
        return 2
    return max(0, min(3, lvl))

def save_volume_level(level: int) -> None:
    cfg = _load_config()
    cfg["volume_level"] = max(0, min(3, int(level)))
    _save_config(cfg)

try:
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("watchdogがインストールされていません。以下のコマンドでインストールしてください:")
    print("pip3 install watchdog")
    sys.exit(1)


# 入力待ち通知用の猫の鳴き声（効果音ラボ https://soundeffect-lab.info/）
NEKO_SOUND_FILE = Path(__file__).resolve().parent / 'sounds' / 'neko.mp3'


def create_observer(*, polling=False):
    """OS通知が届かない監視対象はポーリングを使う。"""
    if polling or os.environ.get('CCMON_POLLING', '').lower() in ('1', 'true', 'yes'):
        return PollingObserver()
    return Observer()


def stop_observers(observers, timeout=2.0):
    """watchdogのstop自体も内部でjoinするため、終了待ち全体に上限を設ける。"""
    def stop_one(observer):
        try:
            observer.stop()
            observer.join(timeout=timeout)
        except Exception as error:
            dprint(f'監視の終了エラー: {error}')

    deadline = time.monotonic() + timeout
    threads = []
    for observer in observers:
        thread = threading.Thread(target=stop_one, args=(observer,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))


class SoundPlayer:
    """再生要求を直列化し、音声デバイス操作はafplay子プロセスに隔離する。"""

    PLAYBACK_TIMEOUT = 5.0
    PROCESS_STOP_TIMEOUT = 0.25
    BEEP_DURATION = 10.0

    def __init__(self):
        self.sample_rate = 44100
        self._volume_level = 2
        self._enabled = True
        self._condition = threading.Condition()
        self._pending = None
        self._current = None
        self._closed = False
        self._last_neko_time = float('-inf')
        self._last_beep_time = float('-inf')
        self._worker = threading.Thread(target=self._run, name='ccmon-audio', daemon=True)
        self._worker.start()

    def get_output_device_label(self) -> str:
        return 'macOS既定出力 (afplay)'

    @property
    def volume_level(self) -> int:
        with self._condition:
            return self._volume_level

    @volume_level.setter
    def volume_level(self, level: int) -> None:
        with self._condition:
            previous_level = self._volume_level
            self._volume_level = max(0, min(3, int(level)))
            if self._volume_level == 0:
                self._stop_locked()
            elif (self._volume_level != previous_level and self._current is not None
                  and self._current[0] == 'beeps' and not self._current[1].is_set()):
                # WAVには音量が焼き込まれるため、操作時は新しい音量で再生し直す。
                self._stop_locked()
                self._request('beeps')

    @property
    def enabled(self) -> bool:
        with self._condition:
            return self._enabled

    @enabled.setter
    def enabled(self, flag: bool) -> None:
        with self._condition:
            self._enabled = bool(flag)
            if not self._enabled:
                self._stop_locked()

    @property
    def playing(self) -> bool:
        with self._condition:
            return self._current is not None and not self._current[1].is_set()

    def generate_beep(self, frequency, duration):
        """指定された周波数と長さのSin波を生成（フェードアウト付き）。"""
        samples = int(self.sample_rate * duration)
        t = np.linspace(0, duration, samples, False)
        volume_scale = self.volume_level / 3.0
        wave_data = (0.2 * volume_scale) * np.sin(2 * np.pi * frequency * t)
        fade_samples = int(self.sample_rate * 0.01)
        if fade_samples < samples:
            wave_data[-fade_samples:] *= np.linspace(1, 0, fade_samples)
        return (wave_data * 32767).astype('<i2')

    def _request(self, kind):
        with self._condition:
            if self._closed or not self._enabled or self._volume_level <= 0:
                return
            if kind == 'beeps':
                if self._current is not None and not self._current[1].is_set():
                    # afplayの終了に少し余裕が必要でも、10秒後の活動通知を捨てない。
                    # 同じ活動の重複通知は無視し、次の再生は最大1件だけ予約する。
                    now = time.monotonic()
                    if (self._current[0] == 'beeps' and self._pending is None
                            and now - self._last_beep_time >= self.BEEP_DURATION):
                        self._pending = (kind, threading.Event())
                        self._last_beep_time = now
                    return
                self._last_beep_time = time.monotonic()
            else:
                now = time.monotonic()
                if now - self._last_neko_time < 5.0:
                    return
                self._last_neko_time = now
            self._stop_locked()
            # 取り消しイベントは再生要求ごとに作る。古い要求を再開させない。
            self._current = (kind, threading.Event())
            self._pending = self._current
            self._condition.notify()

    def play_beeps(self):
        self._request('beeps')

    def play_neko(self):
        self._request('neko')

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                request = self._pending
                self._pending = None
            kind, cancel = request
            try:
                if kind == 'beeps':
                    self._play_beeps(cancel)
                else:
                    self._play_file(NEKO_SOUND_FILE, cancel, self.volume_level / 3.0)
            except Exception as error:
                dprint(f'音声再生エラー: {error}')
            finally:
                with self._condition:
                    # 新しく届いた再生要求を、古い要求の終了処理で消さない。
                    if self._current is request:
                        self._current = self._pending
                    self._condition.notify_all()

    def _play_beeps(self, cancel):
        # 音と無音をまとめて生成し、プロセス起動時間を各ビープの間に挟まない。
        samples = int(self.sample_rate * self.BEEP_DURATION)
        sequence = np.zeros(samples, dtype='<i2')
        offset = 0
        beep_samples = int(self.sample_rate * 0.05)
        while offset + beep_samples <= samples and not cancel.is_set():
            beep = self.generate_beep(np.random.randint(400, 1600), 0.05)
            sequence[offset:offset + beep_samples] = beep
            offset += beep_samples + int(self.sample_rate * np.random.uniform(0.2, 1.0))
        if cancel.is_set():
            return
        with tempfile.TemporaryDirectory(prefix='ccmon-audio-') as directory:
            path = Path(directory) / 'beeps.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(self.sample_rate)
                output.writeframes(sequence.tobytes())
            self._play_file(path, cancel, duration=self.BEEP_DURATION)

    def _play_file(self, path, cancel, volume=1.0, *, duration=0.0):
        if cancel.is_set():
            return False
        # stdinや端末のシグナルを引き継がず、終了はこのワーカーが管理する。
        process = subprocess.Popen(
            ['/usr/bin/afplay', '-v', str(volume), str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.monotonic() + duration + self.PLAYBACK_TIMEOUT
        try:
            while not cancel.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    dprint('音声再生がタイムアウトしました。次の通知で再試行します。')
                    return False
                try:
                    return process.wait(timeout=min(0.05, remaining)) == 0
                except subprocess.TimeoutExpired:
                    pass
            return False
        finally:
            self._stop_process(process)

    def _stop_process(self, process):
        if process.poll() is not None:
            return
        for action in (process.terminate, process.kill):
            try:
                action()
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=self.PROCESS_STOP_TIMEOUT)
                return
            except subprocess.TimeoutExpired:
                pass
        dprint('音声プロセスの終了待ちを打ち切りました。')

    def _stop_locked(self):
        if self._current is not None:
            self._current[1].set()
        self._pending = None

    def stop(self):
        with self._condition:
            self._stop_locked()

    def cleanup(self):
        with self._condition:
            self._closed = True
            self._stop_locked()
            self._condition.notify()
        # 音声処理が異常でも、メインスレッドでは無期限に待たない。
        self._worker.join(timeout=1.0)


class ActivityTracker:
    """各プラットフォームの最近のアクティビティ時刻を保持し、アクティブ判定を行う。"""
    def __init__(self, window_seconds: int = 10):
        self.window = timedelta(seconds=window_seconds)
        self._last = {
            'claude': datetime.min,
            'codex': datetime.min,
            'cursor': datetime.min,
            'opencode': datetime.min,
            'kimi': datetime.min,
            'agy': datetime.min,
        }
        self._lock = threading.Lock()

    def note(self, name: str):
        now = datetime.now()
        with self._lock:
            if name in self._last:
                self._last[name] = now

    def is_active(self, name: str) -> bool:
        now = datetime.now()
        with self._lock:
            last = self._last.get(name, datetime.min)
        return (now - last) <= self.window

    def snapshot(self) -> dict:
        now = datetime.now()
        with self._lock:
            return {name: (now - last) <= self.window
                    for name, last in self._last.items()}


def read_last_jsonl_line(path: str) -> Optional[str]:
    """ファイル末尾の最後の非空行を読む（書き込み途中のファイルでも安全に）。"""
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return None
            read_size = min(size, 65536)
            f.seek(size - read_size)
            data = f.read(read_size)
    except OSError:
        return None
    lines = [ln for ln in data.split(b'\n') if ln.strip()]
    if not lines:
        return None
    return lines[-1].decode('utf-8', errors='ignore')


def detect_claude_waiting(path: str) -> Optional[str]:
    """Claude Codeのセッションログが「入力待ち」状態かを判定する。
    最終行が stop_reason=end_turn のassistantメッセージ（ターン完了）、
    または AskUserQuestion のtool_use（質問中）なら、その識別子を返す。
    それ以外は None。
    """
    line = read_last_jsonl_line(path)
    if not line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get('type') != 'assistant':
        return None
    message = d.get('message') or {}
    content = message.get('content')
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') == 'AskUserQuestion':
                return f"ask:{block.get('id')}"
    if message.get('stop_reason') == 'end_turn':
        return f"end:{message.get('id') or d.get('uuid')}"
    return None


def detect_codex_waiting(path: str) -> Optional[str]:
    """Codexのセッションログが「ターン完了（入力待ち）」状態かを判定する。
    本体のセッションで、最終行が task_complete の event_msg なら turn_id を返す。
    補助エージェントの完了はユーザーの入力待ちではないため通知しない。
    """
    line = read_last_jsonl_line(path)
    if not line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get('type') != 'event_msg':
        return None
    payload = d.get('payload')
    if not isinstance(payload, dict) or payload.get('type') != 'task_complete':
        return None
    turn_id = payload.get('turn_id')
    if not isinstance(turn_id, str) or not turn_id:
        return None

    # sessions配下にはguardian等の内部セッションも保存される。
    # 先頭のsession_metaを確認し、sourceがオブジェクトのsubagentを除外する。
    # 作成途中などでメタデータを読めない場合も、完了と推測しない。
    try:
        with open(path, 'r', encoding='utf-8') as f:
            metadata = json.loads(f.readline())
    except (OSError, ValueError):
        return None
    if not isinstance(metadata, dict) or metadata.get('type') != 'session_meta':
        return None
    session = metadata.get('payload')
    if not isinstance(session, dict):
        return None
    source = session.get('source')
    if not isinstance(source, str) or not source or source == 'subagent':
        return None
    return f"task:{turn_id}"


class ClaudeProjectsHandler(FileSystemEventHandler):
    """Claude Codeプロジェクトディレクトリの変更を監視するハンドラー"""
    
    def __init__(self, sound_player, activity_tracker: ActivityTracker):
        super().__init__()
        self.sound_player = sound_player
        self.activity_tracker = activity_tracker
        self.last_played = datetime.now() - timedelta(seconds=10)
        self.last_waiting_marker = None

    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return

        # .jsonlファイルを対象とする
        if event.src_path.endswith('.jsonl'):
            current_time = datetime.now()

            # 入力待ち状態（ターン完了 or 質問中）ならピコピコを止めてneko音を鳴らす
            waiting_marker = detect_claude_waiting(event.src_path)
            if waiting_marker is not None:
                self.activity_tracker.note('claude')
                if waiting_marker != self.last_waiting_marker:
                    self.last_waiting_marker = waiting_marker
                    self.sound_player.stop()
                    self.sound_player.play_neko()
                return

            # 前回の再生から10秒以上経過していれば音を鳴らす
            if current_time - self.last_played >= timedelta(seconds=10):
                self.sound_player.play_beeps()
                self.last_played = current_time
                self.activity_tracker.note('claude')
    
    def on_created(self, event):
        """ファイルが作成されたときの処理"""
        self._handle_file_event(event, "作成")
        
    def on_modified(self, event):
        """ファイルが変更されたときの処理"""
        self._handle_file_event(event, "更新")


class CodexSessionsHandler(FileSystemEventHandler):
    """Codex会話ログディレクトリの変更を監視するハンドラー"""
    
    def __init__(self, sound_player, activity_tracker: ActivityTracker):
        super().__init__()
        self.sound_player = sound_player
        self.activity_tracker = activity_tracker
        self.last_played = datetime.now() - timedelta(seconds=10)
        self.processed_files = set()  # 処理済みファイルを記録
        self.last_waiting_markers = {}  # セッションごとに通知済みターンを記録

    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return

        current_time = datetime.now()
        dprint(f"[DEBUG {current_time.strftime('%H:%M:%S')}] {event_type}: {event.src_path}")

        # .jsonまたは.jsonlファイルを対象とする
        if event.src_path.endswith('.json') or event.src_path.endswith('.jsonl'):
            # 入力待ち状態（ターン完了）ならピコピコを止めてneko音を鳴らす
            if event.src_path.endswith('.jsonl'):
                waiting_marker = detect_codex_waiting(event.src_path)
                if waiting_marker is not None:
                    self.activity_tracker.note('codex')
                    if waiting_marker != self.last_waiting_markers.get(event.src_path):
                        self.last_waiting_markers[event.src_path] = waiting_marker
                        self.sound_player.stop()
                        self.sound_player.play_neko()
                    return

            # 新規ファイルまたは前回から10秒経過している場合
            if event.src_path not in self.processed_files or current_time - self.last_played >= timedelta(seconds=10):
                self.sound_player.play_beeps()
                self.last_played = current_time
                self.processed_files.add(event.src_path)
                self.activity_tracker.note('codex')
        
    def on_created(self, event):
        """ファイルが作成されたときの処理"""
        self._handle_file_event(event, "作成")
        
    def on_modified(self, event):
        """ファイルが変更されたときの処理"""
        self._handle_file_event(event, "更新")
    
    def on_any_event(self, event):
        """すべてのイベントをキャッチ（デバッグ用）"""
        if not event.is_directory:
            dprint(f"[ANY EVENT] {event.event_type}: {event.src_path}")


def is_cursor_storage_file(path: str) -> bool:
    name = Path(path).name
    return name in ("state.vscdb", "state.vscdb-wal")


class CursorChatsHandler(FileSystemEventHandler):
    """Cursor workspaceStorage/globalStorage の変更を監視するハンドラー"""
    
    def __init__(self, sound_player, activity_tracker: ActivityTracker, file_sizes: Optional[dict] = None):
        super().__init__()
        self.sound_player = sound_player
        self.activity_tracker = activity_tracker
        self.last_played = datetime.now() - timedelta(seconds=10)
        self.processed_files = set()
        self.file_sizes = file_sizes or {}
        
    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return
        
        current_time = datetime.now()
        dprint(f"[DEBUG {current_time.strftime('%H:%M:%S')}] {event_type}: {event.src_path}")
        
        # Cursor IDEのチャット履歴は workspaceStorage/*/state.vscdb(-wal) に保存される
        if not is_cursor_storage_file(event.src_path):
            return

        try:
            current_size = Path(event.src_path).stat().st_size
        except FileNotFoundError:
            return
        previous_size = self.file_sizes.get(event.src_path)
        self.file_sizes[event.src_path] = current_size
        if previous_size == current_size:
            dprint(f"[DEBUG {current_time.strftime('%H:%M:%S')}] サイズ不変のCursor更新を無視: {event.src_path}")
            return

        if event.src_path not in self.processed_files or current_time - self.last_played >= timedelta(seconds=10):
            self.sound_player.play_beeps()
            self.last_played = current_time
            self.processed_files.add(event.src_path)
            self.activity_tracker.note('cursor')
    
    def on_created(self, event):
        """ファイルが作成されたときの処理"""
        self._handle_file_event(event, "作成")
        
    def on_modified(self, event):
        """ファイルが変更されたときの処理"""
        self._handle_file_event(event, "更新")


def collect_cursor_storage_file_sizes(paths: list[Path]) -> dict:
    file_sizes = {}
    for root in paths:
        for path in root.rglob('*'):
            if not path.is_file() or not is_cursor_storage_file(str(path)):
                continue
            try:
                file_sizes[str(path)] = path.stat().st_size
            except FileNotFoundError:
                pass
    return file_sizes


class OpencodeSessionsHandler(FileSystemEventHandler):
    """Opencode会話ログディレクトリの変更を監視するハンドラー"""

    def __init__(self, sound_player, activity_tracker: ActivityTracker):
        super().__init__()
        self.sound_player = sound_player
        self.activity_tracker = activity_tracker
        self.last_played = datetime.now() - timedelta(seconds=10)
        self.processed_files = set()

    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return

        current_time = datetime.now()
        dprint(f"[DEBUG {current_time.strftime('%H:%M:%S')}] {event_type}: {event.src_path}")

        # .logファイルを対象とする
        if event.src_path.endswith('.log'):
            if event.src_path not in self.processed_files or current_time - self.last_played >= timedelta(seconds=10):
                self.sound_player.play_beeps()
                self.last_played = current_time
                self.processed_files.add(event.src_path)
                self.activity_tracker.note('opencode')
    
    def on_created(self, event):
        """ファイルが作成されたときの処理"""
        self._handle_file_event(event, "作成")
        
    def on_modified(self, event):
        """ファイルが変更されたときの処理"""
        self._handle_file_event(event, "更新")


class KimiCodeSessionsHandler(FileSystemEventHandler):
    """Kimi Code 会話ログディレクトリの変更を監視するハンドラー"""

    def __init__(self, sound_player, activity_tracker: ActivityTracker):
        super().__init__()
        self.sound_player = sound_player
        self.activity_tracker = activity_tracker
        self.last_played = datetime.now() - timedelta(seconds=10)
        self.processed_files = set()

    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return

        current_time = datetime.now()
        dprint(f"[DEBUG {current_time.strftime('%H:%M:%S')}] {event_type}: {event.src_path}")

        # .jsonlファイルを対象とする
        if event.src_path.endswith('.jsonl'):
            if event.src_path not in self.processed_files or current_time - self.last_played >= timedelta(seconds=10):
                self.sound_player.play_beeps()
                self.last_played = current_time
                self.processed_files.add(event.src_path)
                self.activity_tracker.note('kimi')

    def on_created(self, event):
        """ファイルが作成されたときの処理"""
        self._handle_file_event(event, "作成")

    def on_modified(self, event):
        """ファイルが変更されたときの処理"""
        self._handle_file_event(event, "更新")


def is_agy_transcript_file(path: str) -> bool:
    """Antigravity (agy) の会話ログ本体かどうかを判定する。"""
    return Path(path).parts[-3:] == ('.system_generated', 'logs', 'transcript.jsonl')


def detect_agy_waiting(path: str) -> Optional[str]:
    """Antigravity (agy) のセッションログが「入力待ち」状態かを判定する。
    最終行が status=DONE の PLANNER_RESPONSE で、ask_question のtool_callを含む、
    または tool_calls 無しで本文を返してターン完了していれば識別子を返す。
    それ以外は None。
    """
    line = read_last_jsonl_line(path)
    if not line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if d.get('type') != 'PLANNER_RESPONSE' or d.get('status') != 'DONE':
        return None
    step_id = d.get('step_index')
    if step_id is None:
        step_id = d.get('created_at')
    if step_id is None or step_id == '':
        return None
    tool_calls = d.get('tool_calls')
    if isinstance(tool_calls, list) and tool_calls:
        for call in tool_calls:
            if isinstance(call, dict) and call.get('name') == 'ask_question':
                return f"ask:{step_id}"
        return None
    content = d.get('content')
    if isinstance(content, str) and content.strip():
        return f"end:{step_id}"
    return None


class AgySessionsHandler(FileSystemEventHandler):
    """Antigravity (agy) 会話ログディレクトリの変更を監視するハンドラー"""

    def __init__(self, sound_player, activity_tracker: ActivityTracker):
        super().__init__()
        self.sound_player = sound_player
        self.activity_tracker = activity_tracker
        self.last_played = datetime.now() - timedelta(seconds=10)
        self.processed_files = set()
        self.last_waiting_markers = {}  # セッションごとに通知済みターンを記録

    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return

        current_time = datetime.now()
        dprint(f"[DEBUG {current_time.strftime('%H:%M:%S')}] {event_type}: {event.src_path}")

        if not is_agy_transcript_file(event.src_path):
            return

        # 入力待ち状態（ターン完了 or 質問中）ならピコピコを止めてneko音を鳴らす
        waiting_marker = detect_agy_waiting(event.src_path)
        if waiting_marker is not None:
            self.activity_tracker.note('agy')
            if waiting_marker != self.last_waiting_markers.get(event.src_path):
                self.last_waiting_markers[event.src_path] = waiting_marker
                self.sound_player.stop()
                self.sound_player.play_neko()
            return

        # 新規ファイルまたは前回から10秒経過している場合
        if event.src_path not in self.processed_files or current_time - self.last_played >= timedelta(seconds=10):
            self.sound_player.play_beeps()
            self.last_played = current_time
            self.processed_files.add(event.src_path)
            self.activity_tracker.note('agy')

    def on_created(self, event):
        """ファイルが作成されたときの処理"""
        self._handle_file_event(event, "作成")

    def on_modified(self, event):
        """ファイルが変更されたときの処理"""
        self._handle_file_event(event, "更新")


# --- 端末入力（非ブロッキング） ---
class TerminalInput:
    """非ブロッキングでキー入力を読み取る簡易ヘルパー。
    Space: 次の音量
    ←/→/↑/↓: 音量変更
    o: On/Off トグル
    q: 終了
    """
    def __init__(self):
        import termios, tty
        self.termios = termios
        self.tty = tty
        self.fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(self.fd)
        self._buffer = ""
        self._lock = threading.Lock()

    def __enter__(self):
        # 逐次読み取りモード
        self.tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        # 設定を戻す
        try:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self._old)
        except Exception:
            pass

    def read_keys(self) -> list[str]:
        """利用可能なキーをすべて読み取って返す（無ければ空配列）。"""
        import select
        keys: list[str] = []
        with self._lock:
            while True:
                rlist, _, _ = select.select([self.fd], [], [], 0)
                if not rlist:
                    break
                try:
                    ch = os.read(self.fd, 1).decode(errors='ignore')
                except Exception:
                    break
                self._buffer += ch

            # バッファを解析（矢印キーESCシーケンス対応）
            i = 0
            while i < len(self._buffer):
                c = self._buffer[i]
                if c == '\x1b':  # ESC
                    if i + 1 >= len(self._buffer):
                        break
                    prefix = self._buffer[i+1]
                    if prefix in ('[', 'O'):
                        if i + 2 >= len(self._buffer):
                            break
                        code = self._buffer[i+2]
                        if code == 'A':
                            keys.append('UP')
                        elif code == 'B':
                            keys.append('DOWN')
                        elif code == 'C':
                            keys.append('RIGHT')
                        elif code == 'D':
                            keys.append('LEFT')
                        elif prefix == 'O' and code == 'M':
                            keys.append('TOGGLE')
                        i += 3
                        continue
                    i += 2
                    continue
                else:
                    if c == ' ':
                        keys.append('SPACE')
                    elif c in ('q', 'Q'):
                        keys.append('QUIT')
                    elif c in ('o', 'O'):
                        keys.append('TOGGLE')
                    # Enterでトグルも可
                    elif c in ('\r', '\n'):
                        keys.append('TOGGLE')
                    i += 1
                    continue
                i += 1

            self._buffer = self._buffer[i:]
        return keys


def build_ui(enabled: bool, volume_level: int, active_status: dict, output_label: str) -> Panel:
    """RichのUIを構築して返す。"""
    # 上段: On/Off ステータス
    status_text = Text()
    status_text.append("Status: ")
    if enabled:
        status_text.append("ON", style="bold green")
    else:
        status_text.append("OFF", style="bold red")

    # 中段: プラットフォームのアクティビティ表示
    plat = Text()
    def token(label: str, is_active: bool):
        style = "bold green" if is_active else "grey58 dim"
        plat.append(f"[{label}]", style=style)
    token("ClaudeCode", bool(active_status.get('claude')))
    plat.append(" ")
    token("Codex", bool(active_status.get('codex')))
    plat.append(" ")
    token("Cursor", bool(active_status.get('cursor')))
    plat.append(" ")
    token("Opencode", bool(active_status.get('opencode')))
    plat.append(" ")
    token("Kimi", bool(active_status.get('kimi')))
    plat.append(" ")
    token("Agy", bool(active_status.get('agy')))

    # 下段: Volume 3 2 1 0（現在値をハイライト）
    vol_text = Text("Volume:  ")
    for lvl in (3, 2, 1, 0):
        if lvl == volume_level:
            vol_text.append(f"{lvl}", style="reverse bold")
        else:
            vol_text.append(f"{lvl}", style="dim")
        if lvl != 0:
            vol_text.append(" ")

    output_text = Text("Output: ")
    output_text.append(output_label, style="bold")

    body = Text()
    body.append_text(status_text)
    body.append("\n")
    body.append_text(plat)
    body.append("\n")
    body.append_text(vol_text)
    body.append("\n")
    body.append_text(output_text)
    body.append("\n\n")
    body.append("[Space]/矢印: 音量  o/Enter: On/Off  q: 終了", style="italic dim")

    return Panel(body, title="CCMon", border_style="cyan")


def run_tui(console, sound_player, activity_tracker):
    """キー入力を確認し、状態や端末サイズが変わった時だけ再描画する。"""
    def ui_state():
        return (sound_player.enabled, sound_player.volume_level,
                activity_tracker.snapshot(), sound_player.get_output_device_label(),
                console.size)

    try:
        console.clear()
    except Exception:
        pass
    with TerminalInput() as tinput:
        previous_state = ui_state()
        with Live(build_ui(*previous_state[:4]), console=console, auto_refresh=False) as live:
            while True:
                for key in tinput.read_keys():
                    # 右に動く = 表示上で右(3 2 1 0)へ → 値は減少
                    if key in ('SPACE', 'RIGHT', 'DOWN'):
                        sound_player.volume_level = (sound_player.volume_level - 1) % 4
                        save_volume_level(sound_player.volume_level)
                    elif key in ('LEFT', 'UP'):
                        sound_player.volume_level = (sound_player.volume_level + 1) % 4
                        save_volume_level(sound_player.volume_level)
                    elif key == 'TOGGLE':
                        sound_player.enabled = not sound_player.enabled
                        if not sound_player.enabled:
                            sound_player.stop()
                        save_sound_enabled(sound_player.enabled)
                    elif key == 'QUIT':
                        raise KeyboardInterrupt

                current_state = ui_state()
                if current_state != previous_state:
                    live.update(build_ui(*current_state[:4]), refresh=True)
                    previous_state = current_state
                time.sleep(0.1)


def main():
    """メイン処理"""
    # 監視対象ディレクトリ
    claude_watch_dir = Path.home() / '.claude' / 'projects'
    codex_watch_dir = Path.home() / '.codex' / 'sessions'
    cursor_user_dir = Path.home() / 'Library' / 'Application Support' / 'Cursor' / 'User'
    cursor_watch_dirs = [
        cursor_user_dir / 'workspaceStorage',
        cursor_user_dir / 'globalStorage',
    ]
    opencode_watch_dir = Path.home() / '.local' / 'share' / 'opencode' / 'log'
    kimi_watch_dir = Path.home() / '.kimi-code' / 'sessions'
    gemini_dir = Path.home() / '.gemini'
    agy_watch_dirs = [
        gemini_dir / 'antigravity-cli' / 'brain',
        gemini_dir / 'antigravity' / 'brain',
        gemini_dir / 'antigravity-ide' / 'brain',
    ]

    # ディレクトリの存在確認
    claude_exists = claude_watch_dir.exists()
    codex_exists = codex_watch_dir.exists()
    existing_cursor_watch_dirs = [p for p in cursor_watch_dirs if p.exists()]
    cursor_exists = bool(existing_cursor_watch_dirs)
    opencode_exists = opencode_watch_dir.exists()
    kimi_exists = kimi_watch_dir.exists()
    existing_agy_watch_dirs = [p for p in agy_watch_dirs if p.exists()]
    agy_exists = bool(existing_agy_watch_dirs)

    if not claude_exists and not codex_exists and not cursor_exists and not opencode_exists and not kimi_exists and not agy_exists:
        print("エラー: Claude Code / Codex / Cursor / Opencode / Kimi Code / Agy いずれも見つかりません。")
        print("いずれかがインストールされているか確認してください。")
        sys.exit(1)
    
    console = Console()
    # 端末での対話実行かを先に判定
    use_tui = (
        hasattr(sys.stdin, "isatty") and hasattr(sys.stdout, "isatty")
        and sys.stdin.isatty() and sys.stdout.isatty()
    )
    show_start_logs = not use_tui

    if show_start_logs:
        header_lines = [
            "CCMon - Claude / Codex / Cursor / Opencode / Kimi / Agy Monitor",
            "-" * 50,
            (f"✓ Claude監視ディレクトリ: {claude_watch_dir}" if claude_exists else f"✗ Claude未検出: {claude_watch_dir}"),
            (f"✓ Codex監視ディレクトリ: {codex_watch_dir}" if codex_exists else f"✗ Codex未検出: {codex_watch_dir}"),
            (f"✓ Cursor監視ディレクトリ: {', '.join(str(p) for p in existing_cursor_watch_dirs)}" if cursor_exists else f"✗ Cursor未検出: {cursor_user_dir}"),
            (f"✓ Opencode監視ディレクトリ: {opencode_watch_dir}" if opencode_exists else f"✗ Opencode未検出: {opencode_watch_dir}"),
            (f"✓ Kimi監視ディレクトリ: {kimi_watch_dir}" if kimi_exists else f"✗ Kimi未検出: {kimi_watch_dir}"),
            (f"✓ Agy監視ディレクトリ: {', '.join(str(p) for p in existing_agy_watch_dirs)}" if agy_exists else f"✗ Agy未検出: {gemini_dir}"),
            "-" * 50,
            "監視項目: ファイル作成/更新",
            "qで終了。TUI上のヘルプも参照してください。",
            "-" * 50,
        ]
        for line in header_lines:
            print(line)
    
    # 音声プレイヤー、ネットワークモニター、イベントハンドラーの初期化
    sound_player = SoundPlayer()
    # 起動時にサウンドON/OFFと音量を復元
    try:
        sound_player.enabled = load_sound_enabled()
    except Exception:
        # 読み込みに失敗した場合は既定のONを維持
        pass
    try:
        sound_player.volume_level = load_volume_level()
    except Exception:
        # 読み込みに失敗した場合は既定(2)を維持
        pass
    activity_tracker = ActivityTracker(window_seconds=10)
    
    # ファイルシステム監視の開始
    # Codex以外はOSの変更通知でアイドル中の全ファイル走査を避ける
    observers = []
    
    if claude_exists:
        claude_observer = create_observer()
        claude_handler = ClaudeProjectsHandler(sound_player, activity_tracker)
        claude_observer.schedule(claude_handler, str(claude_watch_dir), recursive=True)
        claude_observer.start()
        observers.append(claude_observer)
        if show_start_logs:
            print(f"Claude: {type(claude_observer).__name__}で監視開始")
        else:
            dprint(f"Claude: {type(claude_observer).__name__}で監視開始")
    
    if codex_exists:
        # Codex Desktopの実ログ追記はFSEventsで通知されない場合がある。
        # ファイルの作成・追記を確実に拾うため、この監視だけ毎秒ポーリングする。
        codex_observer = create_observer(polling=True)
        codex_handler = CodexSessionsHandler(sound_player, activity_tracker)
        codex_observer.schedule(codex_handler, str(codex_watch_dir), recursive=True)
        codex_observer.start()
        observers.append(codex_observer)
        if show_start_logs:
            print(f"Codex: {type(codex_observer).__name__}で監視開始")
        else:
            dprint(f"Codex: {type(codex_observer).__name__}で監視開始")
    
    if cursor_exists:
        # workspaceStorage + globalStorageを監視
        cursor_observer = create_observer()
        cursor_file_sizes = collect_cursor_storage_file_sizes(existing_cursor_watch_dirs)
        cursor_handler = CursorChatsHandler(sound_player, activity_tracker, cursor_file_sizes)
        for cursor_watch_dir in existing_cursor_watch_dirs:
            cursor_observer.schedule(cursor_handler, str(cursor_watch_dir), recursive=True)
        cursor_observer.start()
        observers.append(cursor_observer)
        if show_start_logs:
            print(f"Cursor: {type(cursor_observer).__name__}で監視開始（workspaceStorage + globalStorage）")
        else:
            dprint(f"Cursor: {type(cursor_observer).__name__}で監視開始（workspaceStorage + globalStorage）")
    
    if opencode_exists:
        opencode_observer = create_observer()
        opencode_handler = OpencodeSessionsHandler(sound_player, activity_tracker)
        opencode_observer.schedule(opencode_handler, str(opencode_watch_dir), recursive=True)
        opencode_observer.start()
        observers.append(opencode_observer)
        if show_start_logs:
            print(f"Opencode: {type(opencode_observer).__name__}で監視開始")
        else:
            dprint(f"Opencode: {type(opencode_observer).__name__}で監視開始")

    if kimi_exists:
        kimi_observer = create_observer()
        kimi_handler = KimiCodeSessionsHandler(sound_player, activity_tracker)
        kimi_observer.schedule(kimi_handler, str(kimi_watch_dir), recursive=True)
        kimi_observer.start()
        observers.append(kimi_observer)
        if show_start_logs:
            print(f"Kimi: {type(kimi_observer).__name__}で監視開始")
        else:
            dprint(f"Kimi: {type(kimi_observer).__name__}で監視開始")

    if agy_exists:
        agy_observer = create_observer()
        agy_handler = AgySessionsHandler(sound_player, activity_tracker)
        for agy_watch_dir in existing_agy_watch_dirs:
            agy_observer.schedule(agy_handler, str(agy_watch_dir), recursive=True)
        agy_observer.start()
        observers.append(agy_observer)
        if show_start_logs:
            print(f"Agy: {type(agy_observer).__name__}で監視開始")
        else:
            dprint(f"Agy: {type(agy_observer).__name__}で監視開始")

    try:
        if use_tui:
            run_tui(console, sound_player, activity_tracker)
        else:
            # TTYでない: TUIを無効化。端末から直接実行するよう案内。
            print("(TTYでないためTUIを無効化しました。端末から直接実行してください)")
            while True:
                time.sleep(3)
    except KeyboardInterrupt:
        print("\n終了します...")
    finally:
        sound_player.cleanup()
        stop_observers(observers)
    print("CCMonを終了しました。")


if __name__ == "__main__":
    main()
