import unittest
from unittest.mock import Mock

from drcom_api import EPortalAPI
from test_drcom_api import FakeResponse


class PlanRegressions(unittest.TestCase):
    def test_mobile_suffix_is_not_duplicated(self):
        self.assertEqual(EPortalAPI._account_for_service('student', '中国移动'),
                         'student@cmcc')
        self.assertEqual(EPortalAPI._account_for_service('student@cmcc', '中国移动'),
                         'student@cmcc')

    def test_portal_access_alone_does_not_authorize_login(self):
        session = Mock()
        session.get.return_value = FakeResponse('login page')
        api = EPortalAPI(session=session)
        api._load_portal_config = Mock(return_value={'login_method': '1'})
        status = api.detect_network_status()
        self.assertFalse(status.need_login)


if __name__ == '__main__':
    unittest.main()
