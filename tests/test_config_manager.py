import json
import tempfile
import unittest
from pathlib import Path

from config_manager import ConfigManager, DPAPI_PREFIX, decrypt_value, encrypt_value


class ConfigManagerTests(unittest.TestCase):
    def test_auto_flags_migrate_and_router_preserves_old_mode(self):
        for keepalive, reconnect in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(keepalive=keepalive, reconnect=reconnect), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / 'config.json').write_text(json.dumps({
                    'auto_keepalive': keepalive, 'auto_reconnect': reconnect,
                    'service': '中国移动',
                }), encoding='utf-8')
                manager = ConfigManager(path)
                self.assertEqual(manager.auto_maintain, keepalive or reconnect)
                self.assertFalse(manager.router_mode)
                manager.auto_maintain = False
                manager.router_mode = True
                reloaded = ConfigManager(path)
                self.assertFalse(reloaded.auto_maintain)
                self.assertTrue(reloaded.router_mode)
                self.assertEqual(reloaded.service, '中国移动')

    def test_new_config_defaults_to_router_and_maintain(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory))
            self.assertTrue(manager.router_mode)
            self.assertTrue(manager.auto_maintain)

    def test_new_auto_flag_takes_precedence_over_old_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'config.json').write_text(json.dumps({
                'auto_maintain': False, 'auto_keepalive': True,
                'auto_reconnect': True, 'router_mode': True,
            }), encoding='utf-8')
            manager = ConfigManager(path)
            self.assertFalse(manager.auto_maintain)
            self.assertTrue(manager.router_mode)

    def test_dpapi_round_trip(self):
        encrypted = encrypt_value('test-password')
        self.assertTrue(encrypted.startswith(DPAPI_PREFIX))
        self.assertNotIn('test-password', encrypted)
        self.assertEqual(decrypt_value(encrypted), 'test-password')

    def test_config_does_not_store_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory))
            manager.username = 'student'
            manager.password = 'test-password'
            raw = (Path(directory) / 'config.json').read_text(encoding='utf-8')
            self.assertNotIn('test-password', raw)
            loaded = json.loads(raw)
            self.assertTrue(loaded['password_encrypted'].startswith(DPAPI_PREFIX))
            self.assertEqual(manager.password, 'test-password')
            self.assertEqual(manager.portal_ip, '10.255.20.10')
            self.assertEqual(manager.portal_port, 801)


if __name__ == '__main__':
    unittest.main()
