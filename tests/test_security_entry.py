import asyncio
import logging
import unittest
from pathlib import Path

import httpx

from security_entry_fixture import cleanup, create_app
from module.webui.security_entry import COOKIE_NAME, EntryLogFilter, SecurityEntry


class SecurityEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app()
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()
        cleanup(self.app)

    async def toggle(self, enabled):
        response = await self.client.patch('/api/system/deploy', json={'key': 'SecurityEntryEnabled', 'value': enabled})
        self.assertEqual(response.status_code, 200)
        return response

    async def test_default_off_enable_current_client_and_anonymous_health(self):
        self.assertFalse(self.app.state.security_entry.key_file.exists())
        self.assertEqual((await self.client.get('/api/protected')).status_code, 200)
        response = await self.toggle(True)
        key = response.json()['security_entry']['key']
        self.assertEqual(len(key), 43)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual((await self.client.get('/api/protected')).status_code, 200)
        self.client.cookies.clear()
        status = (await self.client.get('/api/system/status')).json()
        self.assertFalse(status['security_entry']['authorized'])
        self.assertNotIn('version', status)
        self.assertNotIn('updater_state', status)
        self.assertTrue(status['capabilities']['spa'])
        self.assertEqual((await self.client.get('/api/security/entry')).status_code, 401)

    async def test_all_http_surfaces_protected_no_loopback_or_proxy_exemption(self):
        await self.toggle(True)
        self.client.cookies.clear()
        for path in ('/', '/app/', '/static/gui/icon.png', '/avatars/a.png', '/api/instances',
                     '/api/nkas/screenshot', '/api/nkas/export', '/api/system/logs/download', '/scrcpy/nkas/'):
            response = await self.client.get(path, headers={'X-Forwarded-For': '127.0.0.1', 'Host': 'localhost'})
            self.assertEqual(response.status_code, 401, path)
        self.assertEqual((await self.client.get('/api/protected?key=' + 'a' * 43)).status_code, 401)

    async def test_entry_cookie_bearer_rotation_disable_and_reenable(self):
        old = (await self.toggle(True)).json()['security_entry']['key']
        self.client.cookies.clear()
        self.assertEqual((await self.client.get('/entry/wrong')).status_code, 404)
        response = await self.client.get('/entry/' + old)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/app/')
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        self.assertEqual((await self.client.get('/api/protected')).status_code, 200)
        response = await self.client.post('/api/security/entry/regenerate')
        current = response.json()['security_entry']['key']
        self.assertNotEqual(current, old)
        self.assertEqual((await self.client.get('/api/protected')).status_code, 200)
        self.assertEqual((await self.client.get('/api/protected', headers={'Authorization': 'Bearer ' + old})).status_code, 401)
        self.assertEqual((await self.client.get('/entry/' + old)).status_code, 404)
        self.assertNotIn(current, (await self.client.get('/api/system/deploy')).text)
        self.assertEqual(SecurityEntry(self.app.state.security_entry.config).ensure_key(), current)
        await self.toggle(False)
        self.assertEqual((await self.client.get('/api/protected')).status_code, 200)
        self.assertIsNone((await self.client.get('/api/security/entry')).json()['security_entry']['key'])
        self.assertEqual((await self.toggle(True)).json()['security_entry']['key'], current)
        self.client.cookies.clear()
        self.assertEqual((await self.client.get('/api/protected', headers={'Authorization': 'Bearer ' + current})).status_code, 200)

    async def test_deploy_reset_preserves_protection_and_csrf_rejected(self):
        response = await self.client.patch('/api/system/deploy', json={'key': 'SecurityEntryEnabled', 'value': True},
                                           headers={'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        key = (await self.toggle(True)).json()['security_entry']['key']
        response = await self.client.post('/api/system/deploy/reset', json={'template': 'intl'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.app.state.security_entry.enabled)
        self.assertEqual(self.app.state.security_entry.ensure_key(), key)
        response = await self.client.post('/api/security/entry/regenerate', headers={'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)

    async def websocket(self, path, key=None, origin=None):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({'type': 'websocket.connect'})
        headers = [(b'host', b'testserver')]
        if key:
            headers.append((b'authorization', ('Bearer ' + key).encode()))
        if origin:
            headers.append((b'origin', origin.encode()))
        scope = {'type': 'websocket', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
                 'scheme': 'ws', 'headers': headers, 'root_path': '', 'client': ('127.0.0.1', 40000),
                 'server': ('testserver', 80), 'subprotocols': []}
        task = asyncio.create_task(self.app(scope, incoming.get, outgoing.put))
        return task, outgoing

    async def test_websocket_authentication_and_live_revocation(self):
        key = (await self.toggle(True)).json()['security_entry']['key']
        for path in ('/ws/state', '/ws/nkas/log', '/ws/nkas/queue', '/ws/console'):
            task, messages = await self.websocket(path)
            self.assertEqual((await asyncio.wait_for(messages.get(), 2))['code'], 4401)
            await task
        task, messages = await self.websocket('/ws/state', key, 'https://evil.example')
        self.assertEqual((await messages.get())['code'], 4401)
        await task
        task, messages = await self.websocket('/ws/state', key)
        self.assertEqual((await messages.get())['type'], 'websocket.accept')
        await messages.get()
        await self.client.post('/api/security/entry/regenerate')
        self.assertEqual((await asyncio.wait_for(messages.get(), 2))['code'], 4401)
        await task

    async def test_access_log_redacts_entry(self):
        key = 'a' * 43
        record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
                                   ('client', 'GET', '/entry/' + key, '1.1', 303), None)
        EntryLogFilter().filter(record)
        self.assertNotIn(key, record.getMessage())
        self.assertIn('/entry/[redacted]', record.getMessage())

    async def test_https_cookie_and_production_factory_use_the_same_guard(self):
        # Build the production route tree with disposable config/assets and
        # deliberately do not run its game/updater startup lifecycle.
        Path('assets/gui/avatars').mkdir(parents=True)
        from module.webui.app import app
        production = app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=production),
                                     base_url='https://testserver') as client:
            response = await client.patch('/api/system/deploy',
                                          json={'key': 'SecurityEntryEnabled', 'value': True})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Secure', response.headers['set-cookie'])
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual((await client.get('/api/security/entry')).status_code, 200)
            client.cookies.clear()
            for path in ('/app/', '/static/gui/icon.png', '/api/instances', '/api/system/logs/download'):
                self.assertEqual((await client.get(path)).status_code, 401, path)
            self.assertFalse((await client.get('/api/system/status')).json()['security_entry']['authorized'])


if __name__ == '__main__':
    unittest.main()
