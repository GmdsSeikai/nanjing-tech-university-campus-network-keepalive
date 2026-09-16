import os
import unittest

from tray_icon import SystemTrayIcon


@unittest.skipUnless(os.name == 'nt', 'Windows only')
class SystemTrayIconTests(unittest.TestCase):
    def test_start_and_stop(self):
        tray = SystemTrayIcon('CampusNetLogin Test')
        try:
            tray.start()
            self.assertTrue(tray.is_running())
        finally:
            tray.stop()
        self.assertFalse(tray.is_running())


if __name__ == '__main__':
    unittest.main()
