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

try:
    import pyaudio
except ImportError:
    print("PyAudioがインストールされていません。以下のコマンドでインストールしてください:")
    print("brew install portaudio")
    print("pip3 install pyaudio")
    sys.exit(1)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("watchdogがインストールされていません。以下のコマンドでインストールしてください:")
    print("pip3 install watchdog")
    sys.exit(1)


class SoundPlayer:
    """Sin波による音声生成と再生を管理するクラス"""
    
    def __init__(self):
        self.pyaudio = pyaudio.PyAudio()
        self.sample_rate = 44100
        self.playing = False
        self.stop_event = threading.Event()
        
    def generate_beep(self, frequency, duration):
        """指定された周波数と長さのSin波を生成（フェードアウト付き）"""
        samples = int(self.sample_rate * duration)
        t = np.linspace(0, duration, samples, False)
        wave = 0.3 * np.sin(2 * np.pi * frequency * t)
        
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
            stream = self.pyaudio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                output=True
            )
            
            # ランダムな音程の範囲（Hz）
            min_freq = 400
            max_freq = 1600
            beep_duration = 0.05  # 短めのビープ音
            
            start_time = time.time()
            
            try:
                while time.time() - start_time < 10.0 and not self.stop_event.is_set():
                    # ランダムな周波数を生成
                    freq = np.random.randint(min_freq, max_freq)
                    
                    # ビープ音を生成して再生
                    beep = self.generate_beep(freq, beep_duration)
                    stream.write(beep.tobytes())
                    
                    # ランダムな無音期間（0.2秒〜1.0秒）
                    silence_duration = np.random.uniform(0.2, 1.0)
                    time.sleep(silence_duration)
                    
                    if time.time() - start_time >= 10.0:
                        break
                            
            finally:
                stream.stop_stream()
                stream.close()
                self.playing = False
        
        # 別スレッドで音声再生
        thread = threading.Thread(target=_play)
        thread.daemon = True
        thread.start()
    
    def stop(self):
        """音声再生を停止"""
        self.stop_event.set()
        self.playing = False
    
    def cleanup(self):
        """PyAudioのクリーンアップ"""
        self.stop()
        self.pyaudio.terminate()


class ClaudeProcessMonitor:
    """Claude CLIプロセスの状態を監視するクラス"""
    
    def __init__(self):
        self.last_active = None
        
    def is_claude_active(self):
        """Claude CLIプロセスが実行中（R状態）かどうかを確認"""
        try:
            # ps コマンドでclaude プロセスを取得
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                text=True,
                check=True
            )
            
            # claude プロセスを探す
            for line in result.stdout.split('\n'):
                if 'claude' in line and not 'grep' in line and not 'Claude.app' in line:
                    # プロセス情報を解析
                    parts = line.split()
                    if len(parts) >= 8:
                        # STATE列（通常8列目）をチェック
                        state = parts[7]
                        # R（実行中）またはR+（フォアグラウンドで実行中）の場合
                        if 'R' in state:
                            return True
            
            return False
            
        except Exception as e:
            print(f"プロセス監視エラー: {e}")
            return False


class ClaudeProjectsHandler(FileSystemEventHandler):
    """Claude Codeプロジェクトディレクトリの変更を監視するハンドラー"""
    
    def __init__(self, sound_player):
        super().__init__()
        self.sound_player = sound_player
        self.last_played = datetime.now() - timedelta(seconds=10)
        
    def on_modified(self, event):
        """ファイルが変更されたときの処理"""
        if event.is_directory:
            return
            
        # .jsonlファイルの更新のみを対象とする
        if event.src_path.endswith('.jsonl'):
            current_time = datetime.now()
            
            # 前回の再生から10秒以上経過していれば音を鳴らす
            if current_time - self.last_played >= timedelta(seconds=10):
                print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] ファイル更新検知: {os.path.basename(event.src_path)}")
                self.sound_player.play_beeps()
                self.last_played = current_time


def main():
    """メイン処理"""
    # 監視対象ディレクトリ
    watch_dir = Path.home() / '.claude' / 'projects'
    
    if not watch_dir.exists():
        print(f"エラー: {watch_dir} が存在しません。")
        print("Claude Codeが正しくインストールされているか確認してください。")
        sys.exit(1)
    
    print("CCMon - Claude Code Monitor")
    print(f"監視ディレクトリ: {watch_dir}")
    print("監視項目: ファイル更新 + プロセス実行状態")
    print("Ctrl+Cで終了します。")
    print("-" * 50)
    
    # 音声プレイヤー、プロセスモニター、イベントハンドラーの初期化
    sound_player = SoundPlayer()
    process_monitor = ClaudeProcessMonitor()
    event_handler = ClaudeProjectsHandler(sound_player)
    
    # ファイルシステム監視の開始
    observer = Observer()
    observer.schedule(event_handler, str(watch_dir), recursive=True)
    observer.start()
    
    # プロセス監視用の変数
    last_process_sound = datetime.now() - timedelta(seconds=20)
    was_active = False
    
    try:
        while True:
            # プロセス状態をチェック（3秒ごと）
            is_active = process_monitor.is_claude_active()
            current_time = datetime.now()
            
            # 実行中になった場合、または実行中が続いている場合
            if is_active:
                if not was_active:
                    print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] プロセス実行開始を検知")
                
                # 前回の音から10秒以上経過していれば音を鳴らす
                if current_time - last_process_sound >= timedelta(seconds=10):
                    print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] プロセス実行中...")
                    sound_player.play_beeps()
                    last_process_sound = current_time
            elif was_active:
                print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] プロセス実行終了")
            
            was_active = is_active
            time.sleep(3)
            
    except KeyboardInterrupt:
        print("\n終了します...")
        observer.stop()
        sound_player.cleanup()
    
    observer.join()
    print("CCMonを終了しました。")


if __name__ == "__main__":
    main()