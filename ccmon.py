#!/usr/bin/env python3
"""
CCMon - Claude Code Monitor
macOS用のClaude Codeの活動状況を音で表現するモニターツール
"""

import os
import sys
import time
import threading
import subprocess
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


class SoundPlayer:
    """Sin波による音声生成と再生を管理するクラス"""
    
    def __init__(self):
        self._pyaudio: Optional["pyaudio.PyAudio"] = None
        if pyaudio is not None:
            try:
                self._pyaudio = pyaudio.PyAudio()  # type: ignore
            except Exception:
                self._pyaudio = None
        self.sample_rate = 44100
        self.playing = False
        self.stop_event = threading.Event()
        # 0,1,2,3 の4段階ボリューム（既定=2）
        self._volume_level = 2
        # サウンド有効/無効
        self._enabled = True
        self._lock = threading.Lock()

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
            if self._pyaudio is not None:
                try:
                    stream = self._pyaudio.open(
                        format=pyaudio.paInt16,  # type: ignore
                        channels=1,
                        rate=self.sample_rate,
                        output=True
                    )
                except Exception as e:
                    # 出力デバイス問題などで失敗した場合でも状態を戻す
                    print(f"音声出力エラー: {e}")
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
                    # ランダムな周波数を生成
                    freq = np.random.randint(min_freq, max_freq)
                    
                    # ビープ音を生成して再生
                    beep = self.generate_beep(freq, beep_duration)
                    if stream is not None:
                        try:
                            stream.write(beep.tobytes())
                        except Exception as e:
                            print(f"音声再生エラー: {e}")
                            # ストリームが壊れた場合はフォールバックに切り替え
                            try:
                                stream.stop_stream()
                                stream.close()
                            except Exception:
                                pass
                            stream = None
                            self._play_system_beeps_fallback()
                            break
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
    
    def stop(self):
        """音声再生を停止"""
        self.stop_event.set()
        self.playing = False
    
    def cleanup(self):
        """PyAudioのクリーンアップ"""
        self.stop()
        if hasattr(self, "_pyaudio") and self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass


class ActivityTracker:
    """各プラットフォームの最近のアクティビティ時刻を保持し、アクティブ判定を行う。"""
    def __init__(self, window_seconds: int = 10):
        self.window = timedelta(seconds=window_seconds)
        self._last = {
            'claude': datetime.min,
            'codex': datetime.min,
            'cursor': datetime.min,
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


class ClaudeNetworkMonitor:
    """Claude CLIプロセスのネットワーク活動を監視するクラス"""
    
    def __init__(self):
        self.last_bytes_sent = {}
        self.last_bytes_received = {}
        
    def get_claude_processes(self):
        """Claude/Codex/Cursor エージェント系プロセスのPIDリストを取得"""
        try:
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                text=True,
                check=True
            )
            
            pids = []
            for line in result.stdout.split('\n'):
                # claude または codex プロセスを検出
                # さらに cursor-agent も検出対象に含める（Cursor.app は除外）
                lower_line = line.lower()
                if (
                    ('claude' in lower_line) or
                    ('codex' in lower_line) or
                    ('cursor-agent' in lower_line)
                ) and ('grep' not in line) and ('Claude.app' not in line) and ('Cursor.app' not in line) and ('ccmon' not in line):
                    parts = line.split()
                    if len(parts) >= 2:
                        pids.append(parts[1])
            
            return pids
            
        except Exception as e:
            dprint(f"プロセス取得エラー: {e}")
            return []
    
    def get_network_activity(self, pid):
        """特定のプロセスのネットワーク活動を取得"""
        try:
            # nettopコマンドでネットワーク統計を取得
            result = subprocess.run(
                ['nettop', '-x', '-l', '1', '-p', pid],
                capture_output=True,
                text=True,
                check=True
            )
            
            # 送受信バイト数を解析
            for line in result.stdout.split('\n'):
                if pid in line:
                    # nettopの出力形式: PID ... bytes_in bytes_out
                    parts = line.split()
                    if len(parts) >= 2:
                        # 数値を見つける
                        numbers = []
                        for part in parts:
                            try:
                                # K, M, Gなどの単位を処理
                                if part.endswith('K'):
                                    numbers.append(float(part[:-1]) * 1024)
                                elif part.endswith('M'):
                                    numbers.append(float(part[:-1]) * 1024 * 1024)
                                elif part.endswith('G'):
                                    numbers.append(float(part[:-1]) * 1024 * 1024 * 1024)
                                else:
                                    try:
                                        numbers.append(float(part))
                                    except:
                                        pass
                            except:
                                pass
                        
                        if len(numbers) >= 2:
                            return numbers[-2], numbers[-1]  # bytes_in, bytes_out
            
            return 0, 0
            
        except Exception:
            # nettopが使えない場合は、lsofでTCP接続の存在を確認
            try:
                result = subprocess.run(
                    ['lsof', '-p', pid],
                    capture_output=True,
                    text=True,
                    check=True
                )
                
                # TCP接続があるかチェック
                tcp_connections = 0
                for line in result.stdout.split('\n'):
                    if 'TCP' in line and 'ESTABLISHED' in line:
                        tcp_connections += 1
                
                # 接続があれば活動ありとみなす
                return tcp_connections, tcp_connections
                
            except Exception:
                return 0, 0
    
    def has_network_activity(self):
        """Claude CLIプロセスにネットワーク活動があるかどうかを確認"""
        pids = self.get_claude_processes()
        
        for pid in pids:
            bytes_in, bytes_out = self.get_network_activity(pid)
            
            # 前回の値と比較
            last_in = self.last_bytes_received.get(pid, 0)
            last_out = self.last_bytes_sent.get(pid, 0)
            
            # データ転送があれば活動中
            if bytes_in > last_in or bytes_out > last_out:
                self.last_bytes_received[pid] = bytes_in
                self.last_bytes_sent[pid] = bytes_out
                return True
            
            # 初回またはTCP接続がある場合も活動中とみなす
            if pid not in self.last_bytes_received and (bytes_in > 0 or bytes_out > 0):
                self.last_bytes_received[pid] = bytes_in
                self.last_bytes_sent[pid] = bytes_out
                return True
        
        return False


class ClaudeProjectsHandler(FileSystemEventHandler):
    """Claude Codeプロジェクトディレクトリの変更を監視するハンドラー"""
    
    def __init__(self, sound_player, activity_tracker: ActivityTracker):
        super().__init__()
        self.sound_player = sound_player
        self.activity_tracker = activity_tracker
        self.last_played = datetime.now() - timedelta(seconds=10)
        
    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return
            
        # .jsonlファイルを対象とする
        if event.src_path.endswith('.jsonl'):
            current_time = datetime.now()
            
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
        
    def _handle_file_event(self, event, event_type):
        """ファイルイベントの共通処理"""
        if event.is_directory:
            return
        
        current_time = datetime.now()
        dprint(f"[DEBUG {current_time.strftime('%H:%M:%S')}] {event_type}: {event.src_path}")
            
        # .jsonまたは.jsonlファイルを対象とする
        if event.src_path.endswith('.json') or event.src_path.endswith('.jsonl'):
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


class CursorChatsHandler(FileSystemEventHandler):
    """Cursor会話ログディレクトリの変更を監視するハンドラー (.cursor/chats)"""
    
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
        
        # 拡張子に関わらず、非ディレクトリの作成/更新で鳴らす（10秒スロットル）
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


# --- 端末入力（非ブロッキング） ---
class TerminalInput:
    """非ブロッキングでキー入力を読み取る簡易ヘルパー。
    Space: 次の音量
    ←/→: 音量変更
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
                    # 可能なシーケンス: ESC [ C / ESC [ D
                    if i + 2 < len(self._buffer) and self._buffer[i+1] == '[':
                        code = self._buffer[i+2]
                        if code == 'C':
                            keys.append('RIGHT')
                            i += 3
                            continue
                        elif code == 'D':
                            keys.append('LEFT')
                            i += 3
                            continue
                    # 未知のESC: 1文字進める
                    i += 1
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

            # すべて消費
            self._buffer = ""
        return keys


def build_ui(enabled: bool, volume_level: int, active_status: dict) -> Panel:
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

    # 下段: Volume 3 2 1 0（現在値をハイライト）
    vol_text = Text("Volume:  ")
    for lvl in (3, 2, 1, 0):
        if lvl == volume_level:
            vol_text.append(f"{lvl}", style="reverse bold")
        else:
            vol_text.append(f"{lvl}", style="dim")
        if lvl != 0:
            vol_text.append(" ")

    body = Text()
    body.append_text(status_text)
    body.append("\n")
    body.append_text(plat)
    body.append("\n")
    body.append_text(vol_text)
    body.append("\n\n")
    body.append("[Space]/←/→: 音量  o/Enter: On/Off  q: 終了", style="italic dim")

    return Panel(body, title="CCMon", border_style="cyan")


def main():
    """メイン処理"""
    # 監視対象ディレクトリ
    claude_watch_dir = Path.home() / '.claude' / 'projects'
    codex_watch_dir = Path.home() / '.codex' / 'sessions'
    cursor_watch_dir = Path.home() / '.cursor' / 'chats'
    
    # ディレクトリの存在確認
    claude_exists = claude_watch_dir.exists()
    codex_exists = codex_watch_dir.exists()
    cursor_exists = cursor_watch_dir.exists()
    
    if not claude_exists and not codex_exists and not cursor_exists:
        print("エラー: Claude Code / Codex / Cursor いずれも見つかりません。")
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
            "CCMon - Claude / Codex / Cursor Monitor",
            "-" * 50,
            (f"✓ Claude監視ディレクトリ: {claude_watch_dir}" if claude_exists else f"✗ Claude未検出: {claude_watch_dir}"),
            (f"✓ Codex監視ディレクトリ: {codex_watch_dir}" if codex_exists else f"✗ Codex未検出: {codex_watch_dir}"),
            (f"✓ Cursor監視ディレクトリ: {cursor_watch_dir}" if cursor_exists else f"✗ Cursor未検出: {cursor_watch_dir}"),
            "-" * 50,
            "監視項目: ファイル作成/更新 + ネットワーク活動",
            "qで終了。TUI上のヘルプも参照してください。",
            "-" * 50,
        ]
        for line in header_lines:
            print(line)
    
    # 音声プレイヤー、ネットワークモニター、イベントハンドラーの初期化
    sound_player = SoundPlayer()
    network_monitor = ClaudeNetworkMonitor()
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
        # Cursorはポーリング方式で監視（.cursor/chats）
        cursor_observer = PollingObserver()
        cursor_handler = CursorChatsHandler(sound_player, activity_tracker)
        cursor_observer.schedule(cursor_handler, str(cursor_watch_dir), recursive=True)
        cursor_observer.start()
        observers.append(cursor_observer)
        if show_start_logs:
            print("Cursor: ポーリング監視モードで開始（.cursor/chats）")
        else:
            dprint("Cursor: ポーリング監視モードで開始（.cursor/chats）")
    
    # ネットワーク監視用の変数
    last_network_sound = datetime.now() - timedelta(seconds=20)
    was_active = False
    
    # Rich Live を使ってTUIを更新（なければ従来ループ）
    def loop_step(now: datetime):
        nonlocal was_active, last_network_sound
        has_activity = network_monitor.has_network_activity()
        # ネットワーク活動が検知された場合
        if has_activity:
            if not was_active:
                dprint(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] ネットワーク活動を検知")
            # 前回の音から10秒以上経過していれば音を鳴らす
            if now - last_network_sound >= timedelta(seconds=10):
                dprint(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Claude通信中...")
                sound_player.play_beeps()
                last_network_sound = now
        elif was_active:
            dprint(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] ネットワーク活動終了")
        was_active = has_activity

    refresh_interval = 0.05
    last_net_check = time.time() - 3.0
    try:
        if use_tui:
            # 余計な起動ログを隠すために画面をクリア
            try:
                console.clear()
            except Exception:
                pass
            with TerminalInput() as tinput:
                with Live(build_ui(sound_player.enabled, sound_player.volume_level, {
                    'claude': activity_tracker.is_active('claude'),
                    'codex': activity_tracker.is_active('codex'),
                    'cursor': activity_tracker.is_active('cursor'),
                }), refresh_per_second=10) as live:
                    while True:
                        # 入力処理
                        for key in tinput.read_keys():
                            # 右に動く = 表示上で右(3 2 1 0)へ → 値は減少
                            if key == 'SPACE' or key == 'RIGHT':
                                sound_player.volume_level = (sound_player.volume_level - 1) % 4
                            elif key == 'LEFT':
                                sound_player.volume_level = (sound_player.volume_level + 1) % 4
                            elif key == 'TOGGLE':
                                sound_player.enabled = not sound_player.enabled
                                if not sound_player.enabled:
                                    sound_player.stop()
                            elif key == 'QUIT':
                                raise KeyboardInterrupt

                        # UI更新
                        live.update(build_ui(sound_player.enabled, sound_player.volume_level, {
                            'claude': activity_tracker.is_active('claude'),
                            'codex': activity_tracker.is_active('codex'),
                            'cursor': activity_tracker.is_active('cursor'),
                        }), refresh=True)

                        # ネットワークチェック（約3秒毎）
                        now = datetime.now()
                        if time.time() - last_net_check >= 3.0:
                            loop_step(now)
                            last_net_check = time.time()

                        time.sleep(refresh_interval)
        else:
            # TTYでない: TUIを無効化。端末から直接実行するよう案内。
            print("(TTYでないためTUIを無効化しました。端末から直接実行してください)")
            while True:
                now = datetime.now()
                loop_step(now)
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
