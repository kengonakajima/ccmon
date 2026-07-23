#!/usr/bin/env python3
"""
CCMon - Claude/Codex/Cursor/Opencode/Kimi Monitor
macOS用のコーディングエージェントの活動状況を音で表現するモニターツール
"""

import os
import json
import sys
import time
import threading
import subprocess
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple

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
    import pyaudio  # type: ignore
except ImportError:
    # PyAudio が未導入でもTUIを動作させ、音はシステムサウンドで代替する
    pyaudio = None  # type: ignore
    print("PyAudioが見つかりません。システムサウンドで代替します。")
    print("(インストール推奨: brew install portaudio && pip3 install pyaudio)")

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


class SoundPlayer:
    """Sin波による音声生成と再生を管理するクラス"""
    
    def __init__(self):
        self._pyaudio: Optional["pyaudio.PyAudio"] = None
        self._pyaudio_lock = threading.Lock()
        self._default_device_index: Optional[int] = None
        self._default_device_name: Optional[str] = None
        self._last_device_probe = 0.0
        if pyaudio is not None:
            self._initialize_pyaudio()
        self.sample_rate = 44100
        self.playing = False
        self.stop_event = threading.Event()
        # 0,1,2,3 の4段階ボリューム（既定=2）
        self._volume_level = 2
        # サウンド有効/無効
        self._enabled = True
        self._lock = threading.Lock()
        # neko音の最終再生時刻（連続再生の抑制用）
        self._last_neko_time = 0.0

    def _initialize_pyaudio(self) -> None:
        """PyAudioインスタンスを生成し既定出力デバイスを記録する。"""
        if pyaudio is None:
            return
        with self._pyaudio_lock:
            self._terminate_pyaudio_locked()
            try:
                instance = pyaudio.PyAudio()  # type: ignore
                index, name = self._get_default_output_device(instance)
                self._default_device_index = index
                self._default_device_name = name
                self._pyaudio = instance
                dprint(f"PyAudio初期化完了: default_device_index={self._default_device_index}, name={self._default_device_name}")
            except Exception as e:
                dprint(f"PyAudio初期化エラー: {e}")
                self._pyaudio = None
                self._default_device_index = None
                self._default_device_name = None

    def _terminate_pyaudio_locked(self) -> None:
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
        self._pyaudio = None
        self._default_device_index = None
        self._default_device_name = None

    def _invalidate_pyaudio(self) -> None:
        if pyaudio is None:
            return
        with self._pyaudio_lock:
            self._terminate_pyaudio_locked()

    def _get_default_output_device(self, instance: "pyaudio.PyAudio") -> Tuple[Optional[int], Optional[str]]:  # type: ignore
        try:
            info = instance.get_default_output_device_info()
        except Exception as e:
            dprint(f"既定出力デバイス取得失敗: {e}")
            return None, None
        index = info.get('index')
        name = info.get('name')
        try:
            idx = int(index) if index is not None else None
        except Exception:
            idx = None
        if isinstance(name, str) and name:
            name_str: Optional[str] = name
        else:
            name_str = None
        return idx, name_str

    def _ensure_pyaudio_ready(self) -> Optional["pyaudio.PyAudio"]:
        if pyaudio is None:
            return None
        with self._pyaudio_lock:
            if self._pyaudio is None:
                return self._create_pyaudio_locked()
            current_index, current_name = self._get_default_output_device(self._pyaudio)
            if current_index is None:
                dprint("既定出力デバイス情報を取得できませんでした。PyAudioを再初期化します。")
                self._terminate_pyaudio_locked()
                return self._create_pyaudio_locked()
            if current_index != self._default_device_index:
                dprint(f"既定出力デバイス変更を検知: {self._default_device_index} -> {current_index}")
                self._terminate_pyaudio_locked()
                return self._create_pyaudio_locked()
            if current_name:
                self._default_device_name = current_name
            return self._pyaudio

    def _maybe_update_output_device_info(self) -> None:
        if pyaudio is None:
            return
        now = time.time()
        if now - self._last_device_probe < 1.0:
            return
        self._last_device_probe = now
        # 再生中はPyAudioインスタンスをなるべく触らない
        if self.playing:
            return
        with self._pyaudio_lock:
            prev_index = self._default_device_index
            prev_name = self._default_device_name
            self._terminate_pyaudio_locked()
            new_instance = self._create_pyaudio_locked()
        if new_instance is None:
            return
        if prev_index != self._default_device_index or prev_name != self._default_device_name:
            dprint(f"出力デバイス変更検知: index {prev_index} -> {self._default_device_index}, name {prev_name} -> {self._default_device_name}")

    def _create_pyaudio_locked(self) -> Optional["pyaudio.PyAudio"]:
        try:
            instance = pyaudio.PyAudio()  # type: ignore
            index, name = self._get_default_output_device(instance)
            self._default_device_index = index
            self._default_device_name = name
            self._pyaudio = instance
            dprint(f"PyAudio再初期化: default_device_index={self._default_device_index}, name={self._default_device_name}")
            return instance
        except Exception as e:
            dprint(f"PyAudio再初期化に失敗: {e}")
            self._pyaudio = None
            self._default_device_index = None
            self._default_device_name = None
            return None

    def get_output_device_label(self) -> str:
        """現在利用する出力経路のラベルを取得する。"""
        if pyaudio is None:
            return "システムサウンド (PyAudio未利用)"
        self._maybe_update_output_device_info()
        pa_instance = self._ensure_pyaudio_ready()
        if pa_instance is None:
            return "システムサウンド (フォールバック)"
        with self._pyaudio_lock:
            name = self._default_device_name
        if name:
            return f"PyAudio: {name}"
        return "PyAudio: 既定出力デバイス"

    # 音量レベルのゲッター/セッター
    @property
    def volume_level(self) -> int:
        with self._lock:
            return self._volume_level

    @volume_level.setter
    def volume_level(self, level: int) -> None:
        level = max(0, min(3, int(level)))
        with self._lock:
            self._volume_level = level

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @enabled.setter
    def enabled(self, flag: bool) -> None:
        with self._lock:
            self._enabled = bool(flag)
            if not self._enabled:
                # 再生中なら止める
                self.stop()
        
    def generate_beep(self, frequency, duration):
        """指定された周波数と長さのSin波を生成（フェードアウト付き）"""
        samples = int(self.sample_rate * duration)
        t = np.linspace(0, duration, samples, False)
        # 音量レベルを 0..3 -> 0.0..1.0 に変換
        vol_level = self.volume_level
        if vol_level <= 0:
            # 無音を返す
            return (np.zeros(samples)).astype(np.int16)
        volume_scale = vol_level / 3.0
        wave = (0.2 * volume_scale) * np.sin(2 * np.pi * frequency * t)
        
        # フェードアウトを追加（最後の10msをフェードアウト）
        fade_samples = int(self.sample_rate * 0.01)  # 10ms
        if fade_samples < samples:
            fade = np.linspace(1, 0, fade_samples)
            wave[-fade_samples:] *= fade
        
        return (wave * 32767).astype(np.int16)
    
    def play_beeps(self):
        """10秒間、ランダムな間隔とピッチで音を鳴らす"""
        if self.playing:
            return
            
        self.playing = True
        self.stop_event.clear()
        
        def _play():
            # サウンドが無効なら何もしない
            if not self.enabled:
                self.playing = False
                return

            stream = None
            
            # ランダムな音程の範囲（Hz）
            min_freq = 400
            max_freq = 1600
            beep_duration = 0.05  # 短めのビープ音
            
            start_time = time.time()
            
            try:
                while time.time() - start_time < 10.0 and not self.stop_event.is_set():
                    if not self.enabled:
                        break
                    if stream is None:
                        pa_instance = self._ensure_pyaudio_ready()
                        if pa_instance is not None:
                            try:
                                open_kwargs = dict(
                                    format=pyaudio.paInt16,  # type: ignore
                                    channels=1,
                                    rate=self.sample_rate,
                                    output=True
                                )
                                if self._default_device_index is not None:
                                    open_kwargs['output_device_index'] = self._default_device_index
                                stream = pa_instance.open(**open_kwargs)
                            except Exception as e:
                                print(f"音声出力エラー: {e}")
                                self._invalidate_pyaudio()
                                stream = None
                        if stream is None:
                            self._play_system_beeps_fallback(single=True)
                            silence_duration = np.random.uniform(0.2, 1.0)
                            time.sleep(silence_duration)
                            continue

                    # ランダムな周波数を生成
                    freq = np.random.randint(min_freq, max_freq)
                    
                    # ビープ音を生成して再生
                    beep = self.generate_beep(freq, beep_duration)
                    if stream is not None:
                        try:
                            stream.write(beep.tobytes())
                        except Exception as e:
                            print(f"音声再生エラー: {e}")
                            # ストリームが壊れた場合はPyAudioを再初期化してフォールバックに切り替え
                            try:
                                stream.stop_stream()
                                stream.close()
                            except Exception:
                                pass
                            stream = None
                            self._invalidate_pyaudio()
                            self._play_system_beeps_fallback(single=True)
                            silence_duration = np.random.uniform(0.2, 1.0)
                            time.sleep(silence_duration)
                            continue
                    else:
                        # PyAudioが使えない場合はシステムサウンド
                        self._play_system_beeps_fallback(single=True)
                    
                    # ランダムな無音期間（0.2秒〜1.0秒）
                    silence_duration = np.random.uniform(0.2, 1.0)
                    time.sleep(silence_duration)
                    
                    if time.time() - start_time >= 10.0:
                        break
                            
            finally:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                self.playing = False
        
        # 別スレッドで音声再生
        thread = threading.Thread(target=_play)
        thread.daemon = True
        thread.start()

    def _play_system_beeps_fallback(self, single: bool = False):
        """PyAudioが使えない場合にmacOSのシステムサウンドで代替再生
        single=True の場合は即時に1回だけ鳴らす。
        """
        if not self.enabled:
            return
        if single:
            self.play_quick_system_beep()
            return
        dprint("フォールバック: システムサウンドで通知します")
        start_time = time.time()
        end_time = start_time + 10.0
        while time.time() < end_time and not self.stop_event.is_set():
            if not self.enabled:
                break
            played = False
            # afplay のシステム音を試す
            try:
                subprocess.run(['afplay', '/System/Library/Sounds/Pop.aiff'], capture_output=True)
                played = True
            except Exception:
                pass
            # osascript beep にフォールバック
            if not played:
                try:
                    subprocess.run(['osascript', '-e', 'beep'], capture_output=True)
                    played = True
                except Exception:
                    pass
            # 少し間隔を空ける
            time.sleep(float(np.random.uniform(0.2, 0.9)))
    
    def play_quick_system_beep(self):
        """短いシステムビープ（1回）を非ブロッキングで実行"""
        def _beep_once():
            if not self.enabled:
                return
            # 可能なら afplay のシステム音、ダメなら osascript beep
            try:
                subprocess.run(['afplay', '/System/Library/Sounds/Pop.aiff'], capture_output=True)
                return
            except Exception:
                pass
            try:
                subprocess.run(['osascript', '-e', 'beep'], capture_output=True)
            except Exception:
                pass
        t = threading.Thread(target=_beep_once)
        t.daemon = True
        t.start()
    
    def play_neko(self):
        """入力待ち通知の猫の鳴き声を1回再生（非ブロッキング、最低5秒間隔）"""
        if not self.enabled:
            return
        vol_level = self.volume_level
        if vol_level <= 0:
            return
        now = time.time()
        with self._lock:
            if now - self._last_neko_time < 5.0:
                return
            self._last_neko_time = now

        def _play():
            subprocess.run(
                ['afplay', '-v', str(vol_level / 3.0), str(NEKO_SOUND_FILE)],
                capture_output=True)
        t = threading.Thread(target=_play)
        t.daemon = True
        t.start()

    def stop(self):
        """音声再生を停止"""
        self.stop_event.set()
        self.playing = False
    
    def cleanup(self):
        """PyAudioのクリーンアップ"""
        self.stop()
        self._invalidate_pyaudio()


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
    最終行が task_complete の event_msg なら turn_id を返す。それ以外は None。
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
    payload = d.get('payload') or {}
    if payload.get('type') == 'task_complete':
        return f"task:{payload.get('turn_id')}"
    return None


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
        self.last_waiting_marker = None

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
                    if waiting_marker != self.last_waiting_marker:
                        self.last_waiting_marker = waiting_marker
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

    # ディレクトリの存在確認
    claude_exists = claude_watch_dir.exists()
    codex_exists = codex_watch_dir.exists()
    existing_cursor_watch_dirs = [p for p in cursor_watch_dirs if p.exists()]
    cursor_exists = bool(existing_cursor_watch_dirs)
    opencode_exists = opencode_watch_dir.exists()
    kimi_exists = kimi_watch_dir.exists()

    if not claude_exists and not codex_exists and not cursor_exists and not opencode_exists and not kimi_exists:
        print("エラー: Claude Code / Codex / Cursor / Opencode / Kimi Code いずれも見つかりません。")
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
            "CCMon - Claude / Codex / Cursor / Opencode / Kimi Monitor",
            "-" * 50,
            (f"✓ Claude監視ディレクトリ: {claude_watch_dir}" if claude_exists else f"✗ Claude未検出: {claude_watch_dir}"),
            (f"✓ Codex監視ディレクトリ: {codex_watch_dir}" if codex_exists else f"✗ Codex未検出: {codex_watch_dir}"),
            (f"✓ Cursor監視ディレクトリ: {', '.join(str(p) for p in existing_cursor_watch_dirs)}" if cursor_exists else f"✗ Cursor未検出: {cursor_user_dir}"),
            (f"✓ Opencode監視ディレクトリ: {opencode_watch_dir}" if opencode_exists else f"✗ Opencode未検出: {opencode_watch_dir}"),
            (f"✓ Kimi監視ディレクトリ: {kimi_watch_dir}" if kimi_exists else f"✗ Kimi未検出: {kimi_watch_dir}"),
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
    # Claudeは通常のObserver、CodexはPollingObserverを使用
    observers = []
    
    if claude_exists:
        claude_observer = Observer()
        claude_handler = ClaudeProjectsHandler(sound_player, activity_tracker)
        claude_observer.schedule(claude_handler, str(claude_watch_dir), recursive=True)
        claude_observer.start()
        observers.append(claude_observer)
        if show_start_logs:
            print("Claude: 通常の監視モードで開始")
        else:
            dprint("Claude: 通常の監視モードで開始")
    
    if codex_exists:
        # Codexはポーリング方式で監視（より確実）
        codex_observer = PollingObserver()
        codex_handler = CodexSessionsHandler(sound_player, activity_tracker)
        codex_observer.schedule(codex_handler, str(codex_watch_dir), recursive=True)
        codex_observer.start()
        observers.append(codex_observer)
        if show_start_logs:
            print("Codex: ポーリング監視モードで開始（より確実）")
        else:
            dprint("Codex: ポーリング監視モードで開始（より確実）")
    
    if cursor_exists:
        # Cursorはポーリング方式で監視（workspaceStorage + globalStorage）
        cursor_observer = PollingObserver()
        cursor_file_sizes = collect_cursor_storage_file_sizes(existing_cursor_watch_dirs)
        cursor_handler = CursorChatsHandler(sound_player, activity_tracker, cursor_file_sizes)
        for cursor_watch_dir in existing_cursor_watch_dirs:
            cursor_observer.schedule(cursor_handler, str(cursor_watch_dir), recursive=True)
        cursor_observer.start()
        observers.append(cursor_observer)
        if show_start_logs:
            print("Cursor: ポーリング監視モードで開始（workspaceStorage + globalStorage）")
        else:
            dprint("Cursor: ポーリング監視モードで開始（workspaceStorage + globalStorage）")
    
    if opencode_exists:
        # Opencodeはポーリング方式で監視
        opencode_observer = PollingObserver()
        opencode_handler = OpencodeSessionsHandler(sound_player, activity_tracker)
        opencode_observer.schedule(opencode_handler, str(opencode_watch_dir), recursive=True)
        opencode_observer.start()
        observers.append(opencode_observer)
        if show_start_logs:
            print("Opencode: ポーリング監視モードで開始")
        else:
            dprint("Opencode: ポーリング監視モードで開始")

    if kimi_exists:
        # Kimi Codeはポーリング方式で監視
        kimi_observer = PollingObserver()
        kimi_handler = KimiCodeSessionsHandler(sound_player, activity_tracker)
        kimi_observer.schedule(kimi_handler, str(kimi_watch_dir), recursive=True)
        kimi_observer.start()
        observers.append(kimi_observer)
        if show_start_logs:
            print("Kimi: ポーリング監視モードで開始")
        else:
            dprint("Kimi: ポーリング監視モードで開始")

    refresh_interval = 0.05
    try:
        if use_tui:
            # 余計な起動ログを隠すために画面をクリア
            try:
                console.clear()
            except Exception:
                pass
            with TerminalInput() as tinput:
                initial_status = {
                    'claude': activity_tracker.is_active('claude'),
                    'codex': activity_tracker.is_active('codex'),
                    'cursor': activity_tracker.is_active('cursor'),
                    'opencode': activity_tracker.is_active('opencode'),
                    'kimi': activity_tracker.is_active('kimi'),
                }
                initial_output_label = sound_player.get_output_device_label()
                with Live(build_ui(sound_player.enabled, sound_player.volume_level, initial_status, initial_output_label), refresh_per_second=10) as live:
                    while True:
                        # 入力処理
                        for key in tinput.read_keys():
                            # 右に動く = 表示上で右(3 2 1 0)へ → 値は減少
                            if key == 'SPACE' or key == 'RIGHT' or key == 'DOWN':
                                sound_player.volume_level = (sound_player.volume_level - 1) % 4
                                save_volume_level(sound_player.volume_level)
                            elif key == 'LEFT' or key == 'UP':
                                sound_player.volume_level = (sound_player.volume_level + 1) % 4
                                save_volume_level(sound_player.volume_level)
                            elif key == 'TOGGLE':
                                sound_player.enabled = not sound_player.enabled
                                if not sound_player.enabled:
                                    sound_player.stop()
                                # トグルのたびにON/OFFを保存
                                save_sound_enabled(sound_player.enabled)
                            elif key == 'QUIT':
                                raise KeyboardInterrupt

                        # UI更新
                        live.update(build_ui(sound_player.enabled, sound_player.volume_level, {
                            'claude': activity_tracker.is_active('claude'),
                            'codex': activity_tracker.is_active('codex'),
                            'cursor': activity_tracker.is_active('cursor'),
                            'opencode': activity_tracker.is_active('opencode'),
                            'kimi': activity_tracker.is_active('kimi'),
                        }, sound_player.get_output_device_label()), refresh=True)

                        time.sleep(refresh_interval)
        else:
            # TTYでない: TUIを無効化。端末から直接実行するよう案内。
            print("(TTYでないためTUIを無効化しました。端末から直接実行してください)")
            while True:
                time.sleep(3)
    except KeyboardInterrupt:
        print("\n終了します...")
        for observer in observers:
            observer.stop()
        sound_player.cleanup()
    
    for observer in observers:
        observer.join()
    print("CCMonを終了しました。")


if __name__ == "__main__":
    main()
