import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import ccmon


class AudioLifecycleTests(unittest.TestCase):
    def test_activity_at_ten_seconds_is_not_lost_during_process_exit(self):
        player = ccmon.SoundPlayer()
        first_started = threading.Event()
        release_first = threading.Event()
        next_started = threading.Event()
        requests = []

        def playback(cancel):
            requests.append(cancel)
            if len(requests) == 1:
                first_started.set()
                release_first.wait(2)
            else:
                next_started.set()
                cancel.wait(2)

        try:
            with patch.object(player, '_play_beeps', side_effect=playback):
                player.play_beeps()
                self.assertTrue(first_started.wait(1))
                player.play_beeps()
                self.assertIsNone(player._pending)
                with player._condition:
                    player._last_beep_time -= 10.0
                player.play_beeps()
                queued = player._pending
                self.assertIsNotNone(queued)
                player.play_beeps()
                self.assertIs(player._pending, queued)
                release_first.set()
                self.assertTrue(next_started.wait(1))
                self.assertTrue(player.playing)
                self.assertEqual(len(requests), 2)
                player.cleanup()
        finally:
            release_first.set()
            player.cleanup()

    def test_beep_spacing_is_encoded_in_one_ten_second_wav(self):
        player = ccmon.SoundPlayer()
        self.addCleanup(player.cleanup)

        def inspect_wav(path, cancel, *, duration):
            with wave.open(str(path), 'rb') as source:
                self.assertEqual(source.getframerate(), 44100)
                self.assertEqual(source.getnframes(), 441000)
                samples = ccmon.np.frombuffer(source.readframes(441000), dtype='<i2')
            # 0.05秒の音 + 0.6秒の無音を、起動時間を挟まず16回配置する。
            step = 28665
            for start in range(0, len(samples), step):
                self.assertTrue(ccmon.np.any(samples[start:start + 2205]))
                self.assertFalse(ccmon.np.any(samples[start + 2205:start + step]))
            self.assertEqual(duration, 10.0)
            return True

        with patch('ccmon.np.random.uniform', return_value=0.6), \
             patch.object(player, '_play_file', side_effect=inspect_wav) as playback:
            player._play_beeps(threading.Event())
        playback.assert_called_once()

    def test_sequence_longer_than_five_seconds_is_not_timed_out(self):
        player = ccmon.SoundPlayer()
        self.addCleanup(player.cleanup)
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired('afplay', 0.05), 0]
        process.poll.return_value = 0
        with patch('ccmon.subprocess.Popen', return_value=process), \
             patch('ccmon.time.monotonic', side_effect=[0.0, 0.0, 6.0]):
            self.assertTrue(player._play_file('beeps.wav', threading.Event(), duration=10.0))
        process.kill.assert_not_called()

    def test_volume_change_replaces_active_sequence(self):
        player = ccmon.SoundPlayer()
        self.addCleanup(player.cleanup)
        first_started = threading.Event()
        second_started = threading.Event()
        volumes = []

        def playback(cancel):
            volumes.append(player.volume_level)
            (first_started if len(volumes) == 1 else second_started).set()
            cancel.wait(2)

        with patch.object(player, '_play_beeps', side_effect=playback):
            player.play_beeps()
            self.assertTrue(first_started.wait(1))
            player.volume_level = 1
            self.assertTrue(second_started.wait(1))
            self.assertEqual(volumes, [2, 1])
            player.cleanup()

    def test_hung_process_is_killed_and_next_notification_works(self):
        real_popen = subprocess.Popen
        processes = []

        def start_audio(*args, **kwargs):
            script = ('import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                      'time.sleep(60)') if not processes else 'pass'
            process = real_popen([sys.executable, '-c', script], **kwargs)
            processes.append(process)
            return process

        with patch('ccmon.subprocess.Popen', side_effect=start_audio):
            player = ccmon.SoundPlayer()
            player.PLAYBACK_TIMEOUT = 0.4
            player.BEEP_DURATION = 0.1
            try:
                player.play_beeps()
                with player._condition:
                    self.assertTrue(player._condition.wait_for(
                        lambda: player._current is None, timeout=3))
                self.assertEqual(len(processes), 1)
                self.assertEqual(processes[0].returncode, -signal.SIGKILL)
                self.assertFalse(player.playing)
                self.assertIn('afplay', player.get_output_device_label())
                player.play_neko()
                with player._condition:
                    self.assertTrue(player._condition.wait_for(
                        lambda: player._current is None, timeout=3))
                self.assertEqual(len(processes), 2)
                self.assertEqual(processes[1].returncode, 0)
            finally:
                player.cleanup()
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)

    def test_stop_restart_does_not_revive_old_playback_or_overlap(self):
        player = ccmon.SoundPlayer()
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        release_second = threading.Event()
        cancellations = []

        def playback(cancel):
            cancellations.append(cancel)
            if len(cancellations) == 1:
                first_started.set()
                release_first.wait(3)
            else:
                second_started.set()
                release_second.wait(3)

        try:
            with patch.object(player, '_play_beeps', side_effect=playback):
                player.play_beeps()
                self.assertTrue(first_started.wait(1))
                player.stop()
                player.play_beeps()
                self.assertTrue(cancellations[0].is_set())
                self.assertFalse(second_started.is_set())
                release_first.set()
                self.assertTrue(second_started.wait(1))
                self.assertIsNot(cancellations[0], cancellations[1])
                self.assertFalse(cancellations[1].is_set())
                self.assertTrue(player.playing)
                player.enabled = False
                self.assertTrue(cancellations[1].is_set())
                release_second.set()
        finally:
            release_first.set()
            release_second.set()
            player.cleanup()
        self.assertFalse(player._worker.is_alive())

    def test_muted_or_closed_player_does_not_start_audio(self):
        with patch('ccmon.subprocess.Popen') as popen:
            player = ccmon.SoundPlayer()
            try:
                player.volume_level = 0
                player.play_beeps()
                player.play_neko()
                self.assertFalse(player.playing)
            finally:
                player.cleanup()
            player.volume_level = 3
            player.play_beeps()
            player.play_neko()
            popen.assert_not_called()

    def test_blocked_watcher_shutdown_has_deadline(self):
        release = threading.Event()
        observer = Mock()
        observer.stop.side_effect = lambda: release.wait(3)
        try:
            start = time.monotonic()
            ccmon.stop_observers([observer], timeout=0.05)
            self.assertLess(time.monotonic() - start, 0.5)
        finally:
            release.set()

    def test_ctrl_c_exits_main_and_reaps_stuck_audio(self):
        # SIGINTの処理を本物のPythonプロセスで確認。音声の代わりに停止する子を使う。
        script = r'''
import os, signal, subprocess, sys, tempfile, threading
from pathlib import Path
from unittest.mock import Mock, patch
import ccmon
real_popen = subprocess.Popen
children = []
started = threading.Event()
def audio(*args, **kwargs):
    child = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
    children.append(child)
    started.set()
    return child
def ui(console, player, tracker):
    player.play_beeps()
    assert started.wait(2)
    os.kill(os.getpid(), signal.SIGINT)
with tempfile.TemporaryDirectory() as home:
    (Path(home) / '.codex' / 'sessions').mkdir(parents=True)
    with patch('ccmon.Path.home', return_value=Path(home)), \
         patch('ccmon.load_sound_enabled', return_value=True), \
         patch('ccmon.load_volume_level', return_value=2), \
         patch('ccmon.create_observer', return_value=Mock()), \
         patch('ccmon.subprocess.Popen', side_effect=audio), \
         patch('ccmon.run_tui', side_effect=ui), \
         patch.object(sys.stdin, 'isatty', return_value=True), \
         patch.object(sys.stdout, 'isatty', return_value=True):
        ccmon.main()
assert children and all(child.poll() is not None for child in children)
'''
        result = subprocess.run([sys.executable, '-c', script],
                                cwd=Path(ccmon.__file__).parent,
                                capture_output=True, text=True, timeout=6)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CCMonを終了しました', result.stdout)


if __name__ == '__main__':
    unittest.main()
