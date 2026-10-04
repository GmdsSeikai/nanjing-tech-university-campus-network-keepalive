import logging
import subprocess
import sys
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock

from drcom_api import EPortalAPI, LoginResult, NetworkStatus
from monitor import MaintenanceMonitor, MonitorSettings, SingleInstance
from test_router_auth import PortalTransport


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.settings = MonitorSettings('student', 'secret', '中国移动', router_mode=True)
        self.events = []
        self.monitor = MaintenanceMonitor(self.api, self.settings,
                                          lambda kind, value: self.events.append((kind, value)))

    def test_online_does_not_login(self):
        self.api.detect_network_status.return_value = NetworkStatus(online=True)
        self.assertEqual(self.monitor.step(), 30)
        self.api.login.assert_not_called()

    def test_backoff_and_recovery_reset(self):
        self.api.detect_network_status.return_value = NetworkStatus(state='network_error')
        self.assertEqual([self.monitor.step() for _ in range(7)], [30, 60, 120, 240, 300, 300, 300])
        self.api.login.assert_not_called()
        self.api.detect_network_status.return_value = NetworkStatus(online=True)
        self.assertEqual(self.monitor.step(), 30)
        self.api.detect_network_status.return_value = NetworkStatus(state='unknown')
        self.assertEqual(self.monitor.step(), 30)

    def test_expired_session_restores_using_production_api(self):
        transport = PortalTransport()
        api = EPortalAPI(session=transport, router_mode=True)
        monitor = MaintenanceMonitor(api, self.settings)
        self.assertEqual(monitor.step(), 30)
        self.assertTrue(monitor.last_status.online)
        self.assertEqual(len(transport.submissions()), 1)
        monitor.step()
        self.assertEqual(len(transport.submissions()), 1)

    def test_password_error_pauses_until_configuration_changes(self):
        self.api.detect_network_status.return_value = NetworkStatus(need_login=True)
        self.api.login.return_value = LoginResult(message='密码错误', permanent_error=True)
        self.monitor.step()
        self.monitor.step()
        self.assertEqual(self.api.login.call_count, 1)
        self.assertEqual(self.monitor.paused_reason, 'permanent')
        self.monitor._job('configure', replace(self.settings, password='correct'), self.monitor._generation)
        self.monitor.step()
        self.assertEqual(self.api.login.call_count, 2)
        self.assertEqual(self.api.login.call_args.args[1], 'correct')

    def test_disabled_and_manual_pause_do_not_detect_or_login(self):
        self.monitor._apply_settings(replace(self.settings, auto_maintain=False))
        self.monitor.step()
        self.api.detect_network_status.assert_not_called()
        self.monitor.pause()
        self.monitor._job('configure', self.settings, self.monitor._generation)
        self.assertEqual(self.monitor.paused_reason, 'manual')
        self.monitor.step()
        self.api.detect_network_status.assert_not_called()

    def test_manual_login_clears_manual_pause(self):
        self.monitor.pause()
        self.api.login.return_value = LoginResult(success=True, status=NetworkStatus(online=True))
        self.monitor._job('login', ('student', 'secret', '中国移动', False), self.monitor._generation)
        self.assertIsNone(self.monitor.paused_reason)
        self.assertTrue(self.monitor.last_status.online)

    def test_recovery_logs_elapsed_time(self):
        self.monitor.clock = Mock(side_effect=[10.0, 22.5])
        self.api.detect_network_status.side_effect = [NetworkStatus(state='unknown'), NetworkStatus(online=True)]
        self.monitor.step()
        self.monitor.step()
        self.assertTrue(any('12.5 秒' in value[0] for kind, value in self.events if kind == 'log'))

    def test_pause_during_context_cancels_auth_before_password_submission(self):
        transport = PortalTransport()
        original = transport.get
        entered, release, logged_out = threading.Event(), threading.Event(), threading.Event()
        counts = [0]
        def get(url, **kwargs):
            if url.endswith('/page/loadConfig'):
                counts[0] += 1
                if counts[0] == 3:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Test barrier timed out')
            return original(url, **kwargs)
        transport.get = get
        api = EPortalAPI(session=transport, router_mode=True)
        monitor = MaintenanceMonitor(api, self.settings,
                    lambda kind, value: logged_out.set() if kind == 'logout' else None)
        try:
            monitor.start()
            self.assertTrue(entered.wait(3), 'Authentication context not reached')
            monitor.request_logout()
            release.set()
            self.assertTrue(logged_out.wait(3), 'Logout not completed')
            self.assertEqual(transport.submissions(), [])
            self.assertEqual(len(transport.submissions('/logout')), 1)
            self.assertEqual(monitor.paused_reason, 'manual')
        finally:
            release.set()
            monitor.request_stop()
            monitor.join(3)
        self.assertFalse(monitor.is_running())
        self.assertTrue(transport.closed)

    def test_stop_during_probe_has_no_auth_or_late_callbacks(self):
        entered, release = threading.Event(), threading.Event()
        def detect():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test barrier timed out')
            return NetworkStatus(need_login=True)
        self.api.detect_network_status.side_effect = detect
        try:
            self.monitor.start()
            self.assertTrue(entered.wait(3))
            self.monitor.request_stop()
            event_count = len(self.events)
            self.monitor.request_login('student', 'secret', '中国移动')
            release.set()
            self.monitor.join(3)
            self.assertFalse(self.monitor.is_running())
            self.assertEqual(len(self.events), event_count)
            self.api.login.assert_not_called()
            self.api.close.assert_called_once()
        finally:
            release.set()
            self.monitor.request_stop()
            self.monitor.join(3)

    def test_duplicate_start_and_toggles_keep_one_worker(self):
        entered, release = threading.Event(), threading.Event()
        def detect():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test barrier timed out')
            return NetworkStatus(online=True)
        self.api.detect_network_status.side_effect = detect
        try:
            self.monitor.start()
            self.assertTrue(entered.wait(3))
            thread = self.monitor._thread
            for _ in range(20):
                self.monitor.start()
                self.monitor.configure(replace(self.settings, auto_maintain=False))
                self.monitor.configure(self.settings)
            self.assertIs(self.monitor._thread, thread)
            self.monitor.request_stop()
            release.set()
            self.monitor.join(3)
            self.assertFalse(thread.is_alive())
            with self.assertRaises(RuntimeError):
                self.monitor.start()
        finally:
            release.set()
            self.monitor.request_stop()
            self.monitor.join(3)

    def test_unknown_state_never_submits_password(self):
        self.api.detect_network_status.return_value = NetworkStatus(state='unknown')
        self.monitor.step()
        self.api.login.assert_not_called()

    def test_unapplied_configuration_blocks_stale_credentials(self):
        self.api.detect_network_status.return_value = NetworkStatus(need_login=True)
        self.api.login.return_value = LoginResult(success=True, status=NetworkStatus(online=True))
        settings = replace(self.settings, password='new-secret')
        self.monitor.configure(settings)
        # 队列检查与检测之间到达新配置时，也不能使用旧凭据。
        self.monitor.step()
        self.api.detect_network_status.assert_not_called()
        kind, value, generation = self.monitor._jobs.get_nowait()
        self.monitor._job(kind, value, generation)
        self.monitor.step()
        self.assertEqual(self.api.login.call_args.args[1], 'new-secret')


class SingleInstanceTests(unittest.TestCase):
    def test_lock_blocks_second_process_and_releases(self):
        instance = SingleInstance()
        self.assertTrue(instance.acquire())
        code = ('from monitor import SingleInstance; s=SingleInstance(); '
                'ok=s.acquire(); print(int(ok)); s.release()')
        try:
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), '0')
        finally:
            instance.release()
            instance.release()
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1')


if __name__ == '__main__':
    unittest.main()
