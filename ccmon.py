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
        wave = 0.2 * np.sin(2 * np.pi * frequency * t)
        
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


class ClaudeNetworkMonitor:
    """Claude CLIプロセスのネットワーク活動を監視するクラス"""
    
    def __init__(self):
        self.last_bytes_sent = {}
        self.last_bytes_received = {}
        
    def get_claude_processes(self):
        """Claude CLIプロセスのPIDリストを取得"""
        try:
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                text=True,
                check=True
            )
            
            pids = []
            for line in result.stdout.split('\n'):
                if 'claude' in line and not 'grep' in line and not 'Claude.app' in line and not 'ccmon' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        pids.append(parts[1])
            
            return pids
            
        except Exception as e:
            print(f"プロセス取得エラー: {e}")
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
                
            except:
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
    print("監視項目: ファイル更新 + ネットワーク活動")
    print("Ctrl+Cで終了します。")
    print("-" * 50)
    
    # 音声プレイヤー、ネットワークモニター、イベントハンドラーの初期化
    sound_player = SoundPlayer()
    network_monitor = ClaudeNetworkMonitor()
    event_handler = ClaudeProjectsHandler(sound_player)
    
    # ファイルシステム監視の開始
    observer = Observer()
    observer.schedule(event_handler, str(watch_dir), recursive=True)
    observer.start()
    
    # ネットワーク監視用の変数
    last_network_sound = datetime.now() - timedelta(seconds=20)
    was_active = False
    
    try:
        while True:
            # ネットワーク活動をチェック（3秒ごと）
            has_activity = network_monitor.has_network_activity()
            current_time = datetime.now()
            
            # ネットワーク活動が検知された場合
            if has_activity:
                if not was_active:
                    print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] ネットワーク活動を検知")
                
                # 前回の音から10秒以上経過していれば音を鳴らす
                if current_time - last_network_sound >= timedelta(seconds=10):
                    print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] Claude通信中...")
                    sound_player.play_beeps()
                    last_network_sound = current_time
            elif was_active:
                print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] ネットワーク活動終了")
            
            was_active = has_activity
            time.sleep(3)
            
    except KeyboardInterrupt:
        print("\n終了します...")
        observer.stop()
        sound_player.cleanup()
    
    observer.join()
    print("CCMonを終了しました。")


if __name__ == "__main__":
    main()
