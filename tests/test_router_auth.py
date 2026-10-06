import base64
import json
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit

import requests

from drcom_api import EPortalAPI, NetworkStatus, PROBES, PORTAL_ENTRY
from test_drcom_api import FakeResponse


class PortalTransport:
    """模拟门户及外网边界；完整执行客户端上下文/探测/认证路径。"""

    def __init__(self):
        self.headers = {}
        self.calls = []
        self.online = False
        self.internet = False
        self.accept_login = True
        self.restore_internet = True
        self.login_message = '认证成功'
        self.ip = '10.40.20.1'
        self.page_count = 0
        self.change_ip = False
        self.missing_identity = False
        self.missing_mac = False
        self.page_mac = ''
        self.status_identity = None
        self.chkstatus_unknown = False
        self.missing_config = False
        self.prefix = '1'
        self.check_online_method = '1'
        self.query_result = None
        self.query_error = None
        self.probe_failure = None
        self.redirect = False
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get('params', {})))
        if url in dict(PROBES):
            if self.probe_failure and url == PROBES[0][0]:
                raise requests.ConnectionError('single probe failure')
            if self.internet:
                if url == PROBES[0][0]:
                    return FakeResponse('Microsoft Connect Test')
                if url == PROBES[1][0]:
                    return FakeResponse('<HTML><TITLE>Success</TITLE><BODY>Success</BODY></HTML>')
                return FakeResponse('', 204)
            if self.redirect:
                return FakeResponse('', 302, {'Location': PORTAL_ENTRY})
            return FakeResponse('unrelated response')
        path = urlsplit(url).path
        if path == '/':
            self.page_count += 1
            if self.change_ip and self.page_count > 1:
                self.ip = '10.40.20.2'
            page = '<script>v4ip="' + ('' if self.missing_identity else self.ip) + '";'
            page += 'ss4="' + self.page_mac + '";'
            page += '// v4ip="192.168.1.123";\n</script>'
            return FakeResponse(page, url=PORTAL_ENTRY)
        if path == '/drcom/chkstatus':
            payload = {'result': 1 if self.online else 0, 'msg': '用户不在线',
                       'v46ip': self.ip, 'ss4': '000000000000',
                       'olmac': '' if self.missing_mac else '123456789abc'}
            if self.missing_identity:
                payload = {'result': 0, 'msg': '用户不在线'}
            if self.status_identity is not None:
                payload.update(self.status_identity)
            if self.chkstatus_unknown:
                payload['result'] = -1
                payload['msg'] = '内核状态未知'
        elif path.endswith('/page/loadConfig'):
            payload = {'code': 1, 'data': {'login_method': '1', 'program_index': 'p',
                       'page_index': 'i', 'account_prefix': self.prefix,
                       'check_online_method': self.check_online_method, 'ac_logout': '1'}}
            if self.missing_config:
                del payload['data']['page_index']
        elif path.endswith('/online_list'):
            if self.query_error:
                raise self.query_error
            payload = self.query_result if self.query_result is not None else {
                'result': 1, 'msg': '查询成功', 'total': 1 if self.online else 0,
                'list': [{'online_ip': self.ip, 'online_mac': '123456789abc'}] if self.online else []}
        elif path.endswith('/login'):
            payload = {'result': 1 if self.accept_login else 0, 'msg': self.login_message}
            if self.accept_login:
                self.online = True
                self.internet = self.restore_internet
        elif path.endswith('/logout'):
            payload = {'result': 1}
            self.online = self.internet = False
        else:
            raise AssertionError('Unexpected URL: ' + url)
        return FakeResponse('dr1003(' + json.dumps(payload) + ');', url=url)

    def close(self):
        self.closed = True

    def submissions(self, suffix='/login'):
        return [params for url, params in self.calls if urlsplit(url).path.endswith(suffix)]


class RouterAuthTests(unittest.TestCase):
    def setUp(self):
        self.transport = PortalTransport()
        self.api = EPortalAPI(router_mode=True, session=self.transport)
        self.api._get_local_ip = Mock(side_effect=AssertionError('local IP used'))
        self.api._get_local_mac = Mock(side_effect=AssertionError('local MAC used'))

    def test_mobile_login_refreshes_exit_identity_and_verifies_internet(self):
        self.transport.change_ip = True
        result = self.api.login('student@cmcc', 'secret', '中国移动')
        self.assertTrue(result.success)
        self.assertTrue(result.status.online)
        self.assertEqual(result.user_index, '')
        params = self.transport.submissions()[0]
        self.assertEqual(params['user_account'], ',0,student@cmcc')
        self.assertEqual(params['wlan_user_ip'], '10.40.20.2')
        self.assertEqual(params['wlan_user_mac'], '123456789ABC')
        self.assertEqual(params['program_index'], 'p')
        self.assertEqual(params['page_index'], 'i')
        self.assertNotIn('secret', repr(result))

    def test_portal_acceptance_is_not_internet_success(self):
        self.transport.restore_internet = False
        result = self.api.login('student', 'secret', '中国移动')
        self.assertFalse(result.success)
        self.assertEqual(result.status.state, 'network_error')
        self.assertEqual(len(self.transport.submissions()), 1)
        self.assertEqual(getattr(result, 'phase', None), 'accepted')

    def test_missing_mac_uses_portal_zero_placeholder_and_restores(self):
        self.assertTrue(self.api.login('student', 'secret', '中国移动').success)
        self.assertEqual(self.transport.submissions()[0]['wlan_user_mac'], '123456789ABC')
        self.transport.missing_mac = True
        self.transport.change_ip = True
        for page_mac in ('', '000000000000', '111111111111'):
            with self.subTest(page_mac=page_mac):
                self.transport.page_mac = page_mac
                self.transport.internet = self.transport.online = False
                result = self.api.login('student', 'secret', '中国移动')
                self.assertTrue(result.success, result.message)
                params = self.transport.submissions()[-1]
                self.assertEqual(params['wlan_user_ip'], self.transport.ip)
                self.assertEqual(params['wlan_user_mac'], '000000000000')
                self.assertEqual(params['user_account'], ',0,student@cmcc')
                self.assertEqual(getattr(result, 'phase', None), 'online')

    def test_query_success_requires_matching_current_exit(self):
        cases = [
            ({'result': 1, 'total': 0, 'list': []}, 'need_login'),
            ({'result': '1', 'total': '1', 'list': [{'online_ip': self.transport.ip}]}, 'network_error'),
            ({'result': 1, 'total': 1, 'list': [{'online_ip': '10.40.20.99'}]}, 'need_login'),
            ({'result': 1, 'total': 2, 'list': [{'online_ip': '10.40.20.99'},
                                             {'online_ip': self.transport.ip}]}, 'network_error'),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.transport.query_result = payload
                status = self.api.detect_network_status()
                self.assertEqual(status.state, expected)
                self.assertEqual(status.need_login, expected == 'need_login')
                self.assertFalse(status.online)

    def test_malformed_or_contradictory_lists_stay_unknown(self):
        self.transport.chkstatus_unknown = True
        cases = [
            {}, {'result': 1}, {'result': 1, 'total': 0}, {'result': 1, 'list': []},
            {'result': 1, 'total': 0, 'list': {}},
            {'result': 1, 'total': 1, 'list': []},
            {'result': 1, 'total': 0, 'list': [{'online_ip': self.transport.ip}]},
            {'result': 1, 'total': -1, 'list': []},
            {'result': 1, 'total': False, 'list': []},
            {'result': 1, 'total': 0.0, 'list': []},
            {'result': 1, 'total': 1, 'list': [{}]},
            {'result': 1, 'total': 1, 'list': [self.transport.ip]},
            {'result': 1, 'total': 1, 'list': [{'online_ip': 'invalid'}]},
            {'result': 1, 'total': 1, 'list': [{'online_ip': False}]},
            {'result': 1, 'total': 0, 'list': [], 'online': True},
            {'result': 0, 'total': 1, 'list': [{'online_ip': self.transport.ip}], 'msg': '用户不在线'},
            {'result': 1, 'total': 1, 'list': [{'online_ip': self.transport.ip}], 'online': False},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.transport.query_result = payload
                result = self.api.login('student', 'secret', '中国移动')
                self.assertFalse(result.success)
                self.assertEqual(result.status.state, 'unknown')
                self.assertEqual(getattr(result, 'phase', None), 'not_submitted')
        self.assertEqual(self.transport.submissions(), [])

    def test_redirect_survives_online_query_success(self):
        self.transport.redirect = True
        self.transport.query_result = {'result': 1}
        status = self.api.detect_network_status()
        self.assertTrue(status.need_login)
        self.assertEqual(status.redirect_url, PORTAL_ENTRY)
        self.assertTrue(self.api.login('student', 'secret', '中国移动').success)
        self.assertEqual(len(self.transport.submissions()), 1)

    def test_matching_list_does_not_override_redirect(self):
        self.transport.redirect = True
        self.transport.query_result = {'result': 1, 'total': 1,
                                       'list': [{'online_ip': self.transport.ip}]}
        status = self.api.detect_network_status()
        self.assertEqual(status.state, 'need_login')
        self.assertEqual(status.redirect_url, PORTAL_ENTRY)

    def test_invalid_and_conflicting_identity_never_submits(self):
        cases = [
            ('not-a-mac', {}),
            ('12:3456:78:9A:BC', {}),
            ('123456789ABC', {'olmac': 'AABBCCDDEEFF'}),
            ('', {'olmac': 'not-a-mac'}),
            ('', {'v46ip': 'bad-ip'}),
            ('', {'v46ip': '10.40.20.99'}),
            ('', {'v46ip': '127.0.0.1'}),
            ('', {'v46ip': False}),
        ]
        for page_mac, identity in cases:
            with self.subTest(page_mac=page_mac, identity=identity):
                self.transport.page_mac = page_mac
                self.transport.status_identity = identity
                self.transport.redirect = True
                result = self.api.login('student', 'secret', '中国移动')
                self.assertFalse(result.success)
                self.assertEqual(getattr(result, 'phase', None), 'not_submitted')
        self.assertEqual(self.transport.submissions(), [])

    def test_redirect_identity_conflict_blocks_submission(self):
        self.transport.redirect = True
        original = self.transport.get
        def get(url, **kwargs):
            response = original(url, **kwargs)
            if url in dict(PROBES) and response.status_code == 302:
                response.headers['Location'] = PORTAL_ENTRY + '?wlan_user_ip=10.40.20.99'
            return response
        self.transport.get = get
        result = self.api.login('student', 'secret', '中国移动')
        self.assertFalse(result.success)
        self.assertIn('不一致', result.message)
        self.assertEqual(self.transport.submissions(), [])

    def test_missing_ip_still_blocks_with_redirect_and_missing_mac(self):
        self.transport.missing_identity = self.transport.missing_mac = True
        self.transport.redirect = True
        result = self.api.login('student', 'secret', '中国移动')
        self.assertFalse(result.success)
        self.assertEqual(getattr(result, 'phase', None), 'not_submitted')
        self.assertIn('出口 IP', result.message)
        self.assertEqual(self.transport.submissions(), [])

    def test_zero_mac_exception_is_scoped_to_known_router_protocol(self):
        self.transport.missing_mac = True
        self.transport.redirect = True
        self.api.provider = 'other_provider'
        result = self.api.login('student', 'secret', '中国移动')
        self.assertFalse(result.success)
        self.assertEqual(self.transport.submissions(), [])

    def test_chkstatus_success_keeps_its_own_semantics(self):
        self.transport.check_online_method = '0'
        self.transport.online = True
        self.assertEqual(self.api.detect_network_status().state, 'network_error')
        self.transport.online = False
        self.assertTrue(self.api.detect_network_status().need_login)

    def test_independent_chkstatus_offline_recovers_with_unknown_list(self):
        self.transport.missing_mac = True
        self.transport.query_result = {'result': 0, 'msg': '获取用户在线信息失败！'}
        self.transport.status_identity = {'msg': ''}
        result = self.api.login('student', 'secret', '中国移动')
        self.assertTrue(result.success, result.message)
        self.assertEqual(len(self.transport.submissions()), 1)
        self.assertEqual(self.transport.submissions()[0]['wlan_user_mac'], '000000000000')
        self.assertTrue(any('chkstatus' in line for line in result.raw.get('_debug_log', []) +
                            result.status.debug_log))

    def test_independent_chkstatus_handles_online_list_request_failure(self):
        self.transport.missing_mac = True
        self.transport.query_error = requests.ConnectionError('online_list unavailable')
        self.assertTrue(self.api.login('student', 'secret', '中国移动').success)
        self.assertEqual(len(self.transport.submissions()), 1)

    def test_independent_chkstatus_online_blocks_reauthentication(self):
        self.transport.online = True
        self.transport.query_result = {'result': 0, 'msg': '内部错误'}
        status = self.api.detect_network_status()
        self.assertEqual(status.state, 'network_error')
        self.assertFalse(self.api.login('student', 'secret', '中国移动').success)
        self.assertEqual(self.transport.submissions(), [])

    def test_independent_chkstatus_identity_conflict_blocks_submission(self):
        self.transport.query_result = {'result': 0, 'msg': '内部错误'}
        original = self.transport.get
        count = 0
        def get(url, **kwargs):
            nonlocal count
            if urlsplit(url).path == '/drcom/chkstatus':
                count += 1
                if count > 1:
                    self.transport.status_identity = {'v46ip': '10.40.20.99'}
            return original(url, **kwargs)
        self.transport.get = get
        result = self.api.login('student', 'secret', '中国移动')
        self.assertFalse(result.success)
        self.assertEqual(result.phase, 'not_submitted')
        self.assertIn('不一致', result.message)
        self.assertEqual(self.transport.submissions(), [])

    def test_accepted_phase_survives_verification_exception(self):
        detect = self.api.detect_network_status
        self.api.detect_network_status = Mock(side_effect=[detect(), RuntimeError('verification failed')])
        result = self.api.login('student', 'secret', '中国移动')
        self.assertFalse(result.success)
        self.assertEqual(getattr(result, 'phase', None), 'accepted')
        self.assertEqual(len(self.transport.submissions()), 1)

    def test_online_never_submits_password(self):
        self.transport.internet = True
        result = self.api.login('student', 'secret', '中国移动')
        self.assertTrue(result.success)
        self.assertEqual(getattr(result, 'phase', None), 'online')
        self.assertEqual(self.transport.submissions(), [])

    def test_single_probe_failure_does_not_trigger_auth(self):
        self.transport.internet = True
        self.transport.probe_failure = True
        status = self.api.detect_network_status()
        self.assertTrue(status.online)
        self.assertEqual(len(self.transport.calls), 2)

    def test_authenticated_but_no_internet_does_not_login(self):
        self.transport.online = True
        status = self.api.detect_network_status()
        self.assertFalse(status.need_login)
        self.assertEqual(status.state, 'network_error')
        self.assertFalse(self.api.login('student', 'secret', '中国移动').success)
        self.assertEqual(self.transport.submissions(), [])

    def test_arbitrary_query_error_remains_unknown(self):
        self.transport.query_result = {'result': 0, 'msg': '内部错误'}
        self.transport.chkstatus_unknown = True
        status = self.api.detect_network_status()
        self.assertEqual(status.state, 'unknown')
        self.assertFalse(status.need_login)

    def test_redirect_is_confirmed_auth_evidence(self):
        self.transport.redirect = True
        self.transport.query_result = {'result': 0, 'msg': '内部错误'}
        self.assertTrue(self.api.detect_network_status().need_login)

    def test_portal_matching_checks_host_not_substring(self):
        self.assertFalse(self.api._is_portal('http://attacker.test/?a.njtech.edu.cn'))
        self.assertFalse(self.api._is_portal('http://a.njtech.edu.cn.attacker.test/'))
        self.assertTrue(self.api._is_portal('http://a.njtech.edu.cn/?wlanuserip=10.1.1.1'))

    def test_missing_identity_or_config_blocks_submission(self):
        for attr in ('missing_identity', 'missing_config'):
            with self.subTest(attr=attr):
                transport = PortalTransport()
                setattr(transport, attr, True)
                api = EPortalAPI(router_mode=True, session=transport)
                self.assertFalse(api.login('student', 'secret', '中国移动').success)
                self.assertEqual(transport.submissions(), [])

    def test_prefix_is_driven_by_config(self):
        self.transport.prefix = '0'
        self.assertTrue(self.api.login(',0,student@cmcc', 'secret', '中国移动').success)
        self.assertEqual(self.transport.submissions()[0]['user_account'], 'student@cmcc')

    def test_other_service_suffix_regressions(self):
        for service, suffix in [('校园用户', ''), ('校园电信', '@dx'), ('校园联通', '@lt'), ('校园其他', '')]:
            with self.subTest(service=service):
                self.assertEqual(EPortalAPI._account_for_service('student', service, {'account_prefix': '1'}),
                                 ',0,student' + suffix)

    def test_account_suffix_mismatch_blocks_login(self):
        result = self.api.login('student@dx', 'secret', '中国移动')
        self.assertFalse(result.success)
        self.assertEqual(self.transport.submissions(), [])

    def test_wrong_password_pauses_and_redacts_echo(self):
        self.transport.accept_login = False
        self.transport.login_message = '密码错误 secret http://host/login?user_password=secret'
        result = self.api.login('student', 'secret', '中国移动')
        self.assertTrue(result.permanent_error)
        self.assertEqual(getattr(result, 'phase', None), 'failed')
        self.assertNotIn('secret', repr(result))
        self.assertNotIn('http://host', repr(result))

    def test_http_exception_never_exposes_password_url(self):
        original = self.transport.get
        def get(url, **kwargs):
            if url.endswith('/login'):
                raise requests.ConnectionError('http://host/login?user_password=secret')
            return original(url, **kwargs)
        self.transport.get = get
        result = self.api.login('student', 'secret', '中国移动')
        self.assertNotIn('secret', repr(result))
        self.assertIn('ConnectionError', result.message)
        self.assertEqual(getattr(result, 'phase', None), 'failed')

    def test_cancel_before_submission(self):
        result = self.api.login('student', 'secret', '中国移动', cancelled=lambda: True)
        self.assertFalse(result.success)
        self.assertEqual(self.transport.calls, [])

    def test_logout_uses_portal_identity_without_user_index(self):
        self.assertTrue(self.api.logout())
        params = self.transport.submissions('/logout')[0]
        self.assertEqual(params['wlan_user_ip'], self.transport.ip)
        self.assertEqual(params['wlan_user_mac'], '123456789ABC')

    def test_forced_login_serially_logs_out_first(self):
        self.transport.internet = self.transport.online = True
        self.assertTrue(self.api.login('student', 'secret', '中国移动', force_relogin=True).success)
        paths = [urlsplit(url).path for url, params in self.transport.calls]
        self.assertLess(paths.index('/eportal/portal/logout'), paths.index('/eportal/portal/login'))

    def test_context_preserves_access_parameters_and_ignores_comments(self):
        context = self.api._parse_context(
            PORTAL_ENTRY + '?wlanuserip=10.1.2.3&wlanacip=10.9.8.7&mac=12:34:56:78:9a:bc'
            '&wlanacname=AC&vlan=9&ssid=dorm&apmac=abc&gw_port=5',
            '<script>v4ip="10.2.3.4"; // v4ip="192.168.1.1";\n</script>')
        self.assertEqual(context.user_ip, '10.1.2.3')
        self.assertEqual(context.ac_ip, '10.9.8.7')
        self.assertEqual(context.ac_name, 'AC')
        self.assertEqual(context.vlan, '9')
        self.assertEqual(context.ssid, 'dorm')
        self.api._merge_identity(context, {})
        self.assertEqual(context.user_mac, '123456789ABC')
        self.api._load_portal_config(context.user_ip, context.user_mac, context=context)
        params = self.transport.calls[-1][1]
        self.assertEqual(base64.b64decode(params['wlan_ac_ip']).decode(), context.ac_ip)
        self.assertEqual(params['wlan_vlan_id'], '9')

    def test_comment_example_ip_does_not_replace_identity(self):
        context = self.api._parse_context(PORTAL_ENTRY,
            '<script>v4ip="10.2.3.4"; // v4ip="192.168.1.1";\n</script>')
        self.assertEqual(context.user_ip, '10.2.3.4')

    def test_fake_success_page_is_not_probe_success(self):
        for kind, text, code in [('apple', 'Login Success', 200),
                                 ('microsoft', '<html>Microsoft Connect Test</html>', 200),
                                 ('204', 'login', 204)]:
            self.assertFalse(self.api._probe_matches(FakeResponse(text, code), kind))

    def test_conflicting_exit_identity_is_rejected(self):
        context = self.api._parse_context(PORTAL_ENTRY, '<script>v4ip="10.1.2.3";</script>')
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.api._merge_identity(context, {'v4ip': '10.1.2.4', 'olmac': '123456789abc'})

    def test_router_config_must_define_account_prefix(self):
        original = self.transport.get
        def get(url, **kwargs):
            response = original(url, **kwargs)
            if url.endswith('/page/loadConfig'):
                data = self.api._parse_jsonp(response.text)
                del data['data']['account_prefix']
                response.text = json.dumps(data)
            return response
        self.transport.get = get
        self.assertFalse(self.api.login('student', 'secret', '中国移动').success)
        self.assertEqual(self.transport.submissions(), [])


if __name__ == '__main__':
    unittest.main()
