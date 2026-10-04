import unittest
from unittest.mock import Mock

from drcom_api import EPortalAPI, NetworkStatus


class FakeResponse:
    def __init__(self, text='', status_code=200, headers=None, url='http://a.njtech.edu.cn/'):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class DrcomApiTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.api = EPortalAPI(session=self.session)
        self.api._get_local_ip = Mock(return_value='10.38.71.101')
        self.api._get_local_mac = Mock(return_value='4CD577CCE53D')

    def test_online_short_circuit_skips_login(self):
        self.api.detect_network_status = Mock(return_value=NetworkStatus(online=True))
        result = self.api.login('student', 'secret', '校园用户')
        self.assertTrue(result.success)
        self.session.get.assert_not_called()

    def test_telecom_suffix_and_login_request(self):
        self.api.detect_network_status = Mock(
            side_effect=[NetworkStatus(need_login=True, message='login required'),
                         NetworkStatus(online=True)]
        )
        self.api._load_portal_config = Mock(
            return_value={
                'program_index': 'program-1',
                'page_index': 'page-1',
                'login_method': '1',
            }
        )
        self.session.get.return_value = FakeResponse(
            'dr1003({"result":1,"msg":"认证成功"});'
        )
        result = self.api.login('student', 'secret', '校园电信')
        self.assertTrue(result.success)
        _, kwargs = self.session.get.call_args
        self.assertEqual(kwargs['params']['user_account'], 'student@dx')
        self.assertEqual(kwargs['params']['program_index'], 'program-1')
        self.assertEqual(kwargs['params']['page_index'], 'page-1')

    def test_login_failure_is_reported(self):
        self.api.detect_network_status = Mock(
            return_value=NetworkStatus(need_login=True, message='login required')
        )
        self.api._load_portal_config = Mock(
            return_value={'program_index': 'p', 'page_index': 'i', 'login_method': '1'}
        )
        self.session.get.return_value = FakeResponse(
            'dr1003({"result":0,"msg":"密码错误"});'
        )
        result = self.api.login('student', 'bad', '校园用户')
        self.assertFalse(result.success)
        self.assertEqual(result.message, '密码错误')

    def test_parse_jsonp_and_bad_response(self):
        payload = EPortalAPI._parse_jsonp('dr1({"result": 2, "msg": "ok"});')
        self.assertEqual(payload['result'], 2)
        with self.assertRaises(ValueError):
            EPortalAPI._parse_jsonp('not json')

    def test_service_account_mapping(self):
        cases = {
            '校园用户': 'student',
            '校园电信': 'student@dx',
            '校园联通': 'student@lt',
            '校园其他': 'student',
        }
        for service, expected in cases.items():
            with self.subTest(service=service):
                self.assertEqual(
                    self.api._account_for_service('student', service), expected
                )


if __name__ == '__main__':
    unittest.main()
