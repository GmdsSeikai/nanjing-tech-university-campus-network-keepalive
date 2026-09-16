import json
import tempfile
import unittest
from pathlib import Path

from config_manager import ConfigManager, DPAPI_PREFIX, decrypt_value, encrypt_value


class ConfigManagerTests(unittest.TestCase):
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
