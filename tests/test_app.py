import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
from config_manager import ConfigManager
from drcom_api import EPortalAPI
from test_router_auth import PortalTransport


class AppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = ConfigManager(Path(self.directory.name))
        self.config.auto_maintain = False
        self.config.service = '中国移动'
        self.transport = PortalTransport()
        self.transport.internet = True
        self.client = EPortalAPI(session=self.transport, router_mode=True)
        self.patches = [patch('app.ConfigManager', return_value=self.config),
                        patch('app.EPortalAPI', return_value=self.client),
                        patch('app._configure_logger', return_value=Mock())]
        for patcher in self.patches:
            patcher.start()
        self.gui = app.CampusNetApp()
        self.gui.root.withdraw()
        self.gui._auto_start_monitor()

    def tearDown(self):
        if not self.gui._closing:
            self.gui._exit_application()
        self.pump_until(lambda: not self.gui.monitor.is_running())
        try:
            self.gui._finish_exit()
        except app.tk.TclError:
            pass
        for patcher in reversed(self.patches):
            patcher.stop()
        self.directory.cleanup()

    def pump_until(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() >= deadline:
                self.fail('GUI condition did not settle')
            self.gui.root.update()
            threading.Event().wait(0.01)

    def test_service_restores_and_ui_updates_only_on_main_thread(self):
        self.assertEqual(self.gui.service_var.get(), '中国移动')
        self.assertIn('中国移动', self.gui.service_entry['values'])
        owner = threading.get_ident()
        original = self.gui._on_status_result
        seen = []
        def on_status(status):
            seen.append(threading.get_ident())
            original(status)
        self.gui._on_status_result = on_status
        self.gui._check_status_async()
        self.pump_until(lambda: bool(seen))
        self.assertEqual(set(seen), {owner})
        self.assertEqual(self.gui.status_light._state, 'online')

    def test_login_labels_describe_submission_phase(self):
        cases = [('missing_identity', '未提交认证'), ('accept_login', '认证失败'),
                 ('restore_internet', '认证已接受，等待外网恢复'), ('online', '✅ 已在线')]
        for scenario, label in cases:
            with self.subTest(scenario=scenario):
                transport = PortalTransport()
                if scenario == 'missing_identity':
                    transport.missing_identity = True
                elif scenario in ('accept_login', 'restore_internet'):
                    setattr(transport, scenario, False)
                api = EPortalAPI(session=transport, router_mode=True)
                try:
                    result = api.login('student', 'secret', '中国移动')
                    self.gui._on_login_result(result)
                    self.assertEqual(self.gui.status_label['text'], label)
                    self.assertEqual(self.gui.status_detail['text'], result.message)
                    self.assertEqual(str(self.gui.login_btn['state']), 'normal')
                finally:
                    api.close()

    def test_automatic_login_result_reaches_ui(self):
        self.transport.internet = False
        self.transport.missing_mac = True
        self.gui.username_var.set('student')
        self.gui.password_var.set('secret')
        self.gui.maintain_var.set(True)
        results = []
        original = self.gui._on_login_result
        def on_result(result):
            results.append((threading.get_ident(), result))
            original(result)
        self.gui._on_login_result = on_result
        self.gui._save_credentials()
        self.gui._on_maintain_toggle()
        self.pump_until(lambda: bool(results))
        self.assertEqual(results[0][0], threading.get_ident())
        self.assertTrue(results[0][1].success)
        self.assertEqual(self.gui.status_label['text'], '✅ 已在线')
        self.assertEqual(len(self.transport.submissions()), 1)

    def test_save_starts_auto_monitor_and_logout_stays_paused(self):
        self.gui.username_var.set('student')
        self.gui.password_var.set('secret')
        self.gui.maintain_var.set(True)
        self.gui._on_maintain_toggle()
        self.gui._save_credentials()
        self.pump_until(lambda: self.gui.monitor.settings.username == 'student')
        self.assertTrue(self.gui.monitor.settings.auto_maintain)
        self.assertTrue(self.gui.monitor.is_running())
        self.gui._logout_async()
        self.pump_until(lambda: str(self.gui.logout_btn['state']) == 'normal')
        self.assertEqual(self.gui.monitor.paused_reason, 'manual')
        self.gui._save_credentials()
        self.pump_until(lambda: self.gui.monitor._jobs.empty())
        self.assertEqual(self.gui.monitor.paused_reason, 'manual')
        self.assertEqual(self.transport.submissions(), [])
        self.gui._login_async()
        self.pump_until(lambda: str(self.gui.login_btn['state']) == 'normal')
        self.assertTrue(self.gui.monitor.last_status.online)
        self.assertIsNone(self.gui.monitor.paused_reason)
        self.assertEqual(len(self.transport.submissions()), 1)

    def test_close_keeps_tray_monitor_and_exit_releases_worker(self):
        self.gui._hide_to_tray()
        tray = self.gui.tray_icon
        self.assertIsNotNone(tray)
        self.assertTrue(tray.is_running())
        self.assertTrue(self.gui.monitor.is_running())
        self.gui._restore_window()
        self.assertEqual(self.gui.root.state(), 'normal')
        self.gui.root.withdraw()
        self.gui._on_logout_result(True)
        self.assertIs(self.gui.tray_icon, tray)
        self.gui._exit_application()
        self.pump_until(lambda: not self.gui.monitor.is_running())
        self.assertFalse(tray.is_running())
        self.assertTrue(self.transport.closed)
        size = self.gui._ui_events.qsize()
        thread = threading.Thread(target=lambda: self.gui._post(self.gui._log, 'late event'))
        thread.start()
        thread.join()
        self.assertEqual(self.gui._ui_events.qsize(), size)

    def test_exit_cancels_all_tk_callbacks_before_destroy(self):
        pending = []
        original = self.gui.root.destroy
        def destroy():
            pending.append(tuple(self.gui.root.tk.splitlist(self.gui.root.tk.call('after', 'info'))))
            original()
        self.gui.root.destroy = destroy
        self.gui._exit_application()
        self.pump_until(lambda: bool(pending))
        self.assertEqual(pending, [()])


if __name__ == '__main__':
    unittest.main()
