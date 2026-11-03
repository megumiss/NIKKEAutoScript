"""Real TCP HTTP/WebSocket checks against an automatically disposed fixture."""

import asyncio
import unittest

import httpx
import uvicorn
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from security_entry_fixture import cleanup, create_app


class LiveSecurityEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app()
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host='127.0.0.1', port=0, proxy_headers=False, log_config=None, access_log=False,
        ))
        self.server.install_signal_handlers = lambda: None
        self.serving = asyncio.create_task(self.server.serve())
        for _ in range(500):
            if self.server.started:
                break
            if self.serving.done():
                await self.serving
            await asyncio.sleep(0.01)
        self.assertTrue(self.server.started)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.ws_base = f'ws://127.0.0.1:{port}'
        self.client = httpx.AsyncClient(base_url=self.base, trust_env=False)
        self.anonymous = httpx.AsyncClient(base_url=self.base, trust_env=False)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.anonymous.aclose()
        self.server.should_exit = True
        await asyncio.wait_for(self.serving, 5)
        cleanup(self.app)

    async def socket(self, path, **kwargs):
        websocket = await websockets.connect(self.ws_base + path, **kwargs)
        await asyncio.wait_for(websocket.recv(), 2)
        return websocket

    async def assert_revoked(self, websocket):
        with self.assertRaises(ConnectionClosed) as closed:
            await asyncio.wait_for(websocket.recv(), 2)
        self.assertEqual(closed.exception.code, 4401)
        await websocket.close()

    async def toggle(self, enabled):
        response = await self.client.patch(
            '/api/system/deploy', json={'key': 'SecurityEntryEnabled', 'value': enabled},
        )
        self.assertEqual(response.status_code, 200)
        return response

    async def test_live_enable_rotate_disable_reenable_and_revoke_every_socket(self):
        self.assertEqual((await self.anonymous.get('/app/')).status_code, 200)
        public_socket = await self.socket('/ws/state')
        enabled = await self.toggle(True)
        key = enabled.json()['security_entry']['key']
        await self.assert_revoked(public_socket)
        self.assertEqual((await self.client.get('/api/protected')).status_code, 200)
        self.assertEqual((await self.anonymous.get('/api/protected')).status_code, 401)
        self.assertFalse((await self.anonymous.get('/api/system/status')).json()['security_entry']['authorized'])

        for path in ('/ws/state', '/ws/nkas/log', '/ws/nkas/queue', '/ws/console'):
            with self.assertRaises(InvalidStatusCode) as denied:
                await websockets.connect(self.ws_base + path)
            self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(InvalidStatusCode):
            await websockets.connect(self.ws_base + '/ws/state', origin='http://untrusted.test',
                                     extra_headers={'Authorization': 'Bearer ' + key})

        login = await self.anonymous.get('/entry/' + key)
        self.assertEqual(login.status_code, 303)
        self.assertEqual(login.headers['location'], '/app/')
        self.assertIn('HttpOnly', login.headers['set-cookie'])
        self.assertIn('SameSite=strict', login.headers['set-cookie'])
        self.assertEqual(login.headers['cache-control'], 'no-store')
        self.assertEqual(login.headers['referrer-policy'], 'no-referrer')
        sockets = [await self.socket(path, extra_headers={'Authorization': 'Bearer ' + key})
                   for path in ('/ws/state', '/ws/nkas/log', '/ws/nkas/queue', '/ws/console')]
        cookie_socket = await self.socket('/ws/state', origin=self.base,
                                         extra_headers={'Cookie': 'nkas_entry=' + key})
        rotated = await self.client.post('/api/security/entry/regenerate')
        self.assertEqual(rotated.status_code, 200)
        current = rotated.json()['security_entry']['key']
        self.assertNotEqual(key, current)
        await asyncio.gather(*(self.assert_revoked(socket) for socket in [*sockets, cookie_socket]))
        self.assertEqual((await self.client.get('/api/protected')).status_code, 200)
        self.assertEqual((await self.anonymous.get('/api/protected')).status_code, 401)
        self.assertEqual((await self.anonymous.get('/entry/' + key)).status_code, 404)
        self.assertEqual((await self.anonymous.get('/api/protected',
                                                  headers={'Authorization': 'Bearer ' + key})).status_code, 401)
        self.assertEqual((await self.anonymous.get('/api/protected',
                                                  headers={'Authorization': 'Bearer ' + current})).status_code, 200)
        restored = await self.socket('/ws/state', extra_headers={'Authorization': 'Bearer ' + current})
        await restored.close()

        await self.toggle(False)
        self.assertEqual((await self.anonymous.get('/app/')).status_code, 200)
        self.assertIsNone((await self.client.get('/api/security/entry')).json()['security_entry']['key'])
        self.assertEqual((await self.toggle(True)).json()['security_entry']['key'], current)
        self.assertEqual((await self.anonymous.get('/api/protected')).status_code, 401)


if __name__ == '__main__':
    unittest.main()
