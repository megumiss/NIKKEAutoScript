import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deploy.mirrorchyan import Client, MirrorError, atomic_json, credential_path, load_cdk, save_cdk
from deploy.source_update import check_source, current_version, run_update
from deploy.update_package import (
    HISTORY,
    MANIFEST,
    STATE,
    VERSION,
    UpdateLock,
    assert_ready,
    recover,
    recover_desktop,
    stage_desktop,
)


def source_files(version, files):
    files = dict(files)
    files[VERSION] = json.dumps({'version': version}).encode()
    files[HISTORY] = json.dumps(
        {
            'version': version,
            'commits': [{'sha': version, 'author': 'Test', 'date': '2026-10-03', 'message': 'Test release'}],
        }
    ).encode()
    files[MANIFEST] = json.dumps(
        {'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    ).encode()
    return files


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        for name, data in files.items():
            package.writestr(name, data)
    return output.getvalue()


def write_files(root, files):
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class LocalMirror:
    def __init__(self):
        self.requests = []
        self.data = {}
        self.body = None
        self.http_status = 200
        self.download = b''
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                owner.requests.append((parsed.path, urllib.parse.parse_qs(parsed.query)))
                if parsed.path == '/package.zip':
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(owner.download)
                    return
                self.send_response(owner.http_status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(
                    owner.body if owner.body is not None else json.dumps({'code': 0, 'data': owner.data}).encode()
                )

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def publish(self, version, files, kind='full'):
        self.download = archive(files)
        self.data = {
            'version_name': version,
            'url': self.url + '/package.zip',
            'filesize': len(self.download),
            'sha256': hashlib.sha256(self.download).hexdigest(),
            'update_type': kind,
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/deploy.yaml').write_text('Deploy:\n  Update:\n    MirrorChyanEnabled: true\n')
        self.server = LocalMirror()
        self.client = Client(self.root, api=self.server.url + '/api/resources', cdk='')
        self.manager = SimpleNamespace(root_filepath=str(self.root), git='git', git_update=Mock(), pip_install=Mock())
        self.a, self.b = 'a' * 40, 'b' * 40

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def test_no_cdk_latest_without_link_is_normal(self):
        write_files(self.root, source_files(self.a, {'module/main.py': b'old'}))
        self.server.data = {'version_name': self.a}
        _, available = check_source(self.root, client=self.client)
        self.assertFalse(available)
        self.assertNotIn('cdk', self.server.requests[0][1])
        self.assertEqual(self.server.requests[0][1]['current_version'], [self.a])

    def test_new_version_without_link_errors_and_never_falls_back(self):
        self.server.data = {'version_name': self.b}
        with self.assertRaisesRegex(MirrorError, '下载链接'):
            run_update(self.manager, client=self.client)
        self.manager.git_update.assert_not_called()
        self.manager.pip_install.assert_not_called()
        self.assertIn('true', (self.root / 'config/deploy.yaml').read_text())

    def test_business_and_http_errors_are_sanitized(self):
        self.client.cdk = 'secret-value'
        for code in (7001, 7002, 7003, 7004, 7005, 8001, 8002, 8003, 8004):
            self.server.http_status = 403
            self.server.body = json.dumps(
                {'code': code, 'msg': 'bad secret-value https://example.com/?cdk=abc'}
            ).encode()
            with self.assertRaises(MirrorError) as error:
                run_update(self.manager, client=self.client)
            self.assertIn(str(code), str(error.exception))
            self.assertNotIn('secret-value', str(error.exception))
            self.assertNotIn('https://', str(error.exception))
        self.manager.git_update.assert_not_called()

    def test_non_json_and_network_errors_never_fall_back(self):
        self.server.body = b'<html>error</html>'
        with self.assertRaisesRegex(MirrorError, 'JSON'):
            run_update(self.manager, client=self.client)
        with patch('urllib.request.urlopen', side_effect=TimeoutError):
            with self.assertRaisesRegex(MirrorError, '超时'):
                run_update(self.manager, client=self.client)
        self.manager.git_update.assert_not_called()

    def test_disabled_uses_git_without_mirror_request(self):
        (self.root / 'config/deploy.yaml').write_text('MirrorChyanEnabled: false\n')
        run_update(self.manager, client=self.client)
        self.manager.git_update.assert_called_once()
        self.manager.pip_install.assert_called_once()
        self.assertEqual(self.server.requests, [])

    def test_invalid_switch_does_not_silently_select_git(self):
        (self.root / 'config/deploy.yaml').write_text('MirrorChyanEnabled: invalid\n')
        with self.assertRaisesRegex(MirrorError, 'true'):
            run_update(self.manager, client=self.client)
        self.manager.git_update.assert_not_called()
        self.assertEqual(self.server.requests, [])

    def test_full_install_preserves_user_files_and_cleans_old_owned_files(self):
        old = source_files(self.a, {'module/removed.py': b'old', 'gui.py': b'old'})
        write_files(self.root, old)
        user_files = {'config/nkas.json': b'private', 'config/.security/other.acc': b'account', 'log/test.txt': b'log'}
        write_files(self.root, user_files)
        new = source_files(self.b, {'gui.py': b'new', 'config/notices.yaml': b'announcement'})
        self.server.publish(self.b, new)
        self.assertTrue(run_update(self.manager, client=self.client))
        self.assertEqual(current_version(self.root), self.b)
        self.assertFalse((self.root / 'module/removed.py').exists())
        self.assertEqual((self.root / 'gui.py').read_bytes(), b'new')
        for name, data in user_files.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.manager.git_update.assert_not_called()
        assert_ready(self.root)

    def test_incremental_add_modify_delete_and_missing_arrays(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                old = source_files(self.a, {'gui.py': b'old', 'module/old.py': b'stable'})
                write_files(self.root, old)
                content = {'gui.py': b'new', 'module/add.py': b'added'}
                if not removed:
                    content['module/old.py'] = b'stable'
                new = source_files(self.b, content)
                changed = {name: data for name, data in new.items() if old.get(name) != data}
                changes = {
                    'added': [name for name in changed if name not in old],
                    'modified': [name for name in changed if name in old],
                }
                if removed:
                    changes['deleted'] = ['module/old.py']
                changed['changes.json'] = json.dumps(changes).encode()
                self.server.publish(self.b, changed, 'incremental')
                self.assertTrue(run_update(self.manager, client=self.client))
                self.assertEqual((self.root / 'module/add.py').read_bytes(), b'added')
                self.assertEqual((self.root / 'module/old.py').exists(), not removed)
                self.assertEqual(current_version(self.root), self.b)
                (self.root / 'module/add.py').unlink()

    def test_hash_size_failures_do_not_touch_installation(self):
        write_files(self.root, source_files(self.a, {'gui.py': b'old'}))
        self.server.publish(self.b, source_files(self.b, {'gui.py': b'new'}))
        for field, value in [('sha256', '0' * 64), ('filesize', len(self.server.download) + 1)]:
            saved = self.server.data[field]
            self.server.data[field] = value
            with self.assertRaises(MirrorError):
                run_update(self.manager, client=self.client)
            self.server.data[field] = saved
            self.assertEqual((self.root / 'gui.py').read_bytes(), b'old')
            self.assertEqual(current_version(self.root), self.a)
        self.manager.git_update.assert_not_called()

    def test_traversal_and_protected_files_are_rejected(self):
        for name in ('../escape.py', 'C:/escape', 'module\\escape', 'config/deploy.yaml', 'config/.security/a.acc'):
            self.server.publish(self.b, source_files(self.b, {name: b'invalid'}))
            with self.subTest(name=name), self.assertRaises(MirrorError):
                run_update(self.manager, client=self.client)
        self.manager.pip_install.assert_not_called()
        self.assertIn('true', (self.root / 'config/deploy.yaml').read_text())

    def test_dependency_failure_restores_files_and_blocks_until_repaired(self):
        write_files(self.root, source_files(self.a, {'gui.py': b'old'}))
        self.server.publish(self.b, source_files(self.b, {'gui.py': b'new'}))
        self.manager.pip_install.side_effect = RuntimeError('pip failed')
        with self.assertRaisesRegex(RuntimeError, 'pip failed'):
            run_update(self.manager, client=self.client)
        self.assertEqual((self.root / 'gui.py').read_bytes(), b'old')
        self.assertEqual(current_version(self.root), self.a)
        with self.assertRaises(MirrorError):
            assert_ready(self.root)
        self.manager.pip_install.side_effect = None
        with UpdateLock(self.root):
            recover(self.root, self.manager.pip_install)
        assert_ready(self.root)

    def test_cross_process_lock(self):
        code = 'from deploy.update_package import UpdateLock; import sys; UpdateLock(sys.argv[1]).__enter__()'
        with UpdateLock(self.root):
            child = subprocess.run([sys.executable, '-c', code, str(self.root)], capture_output=True)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b'MirrorError', child.stderr)
        with UpdateLock(self.root):
            pass

    def test_developer_worktree_is_not_overwritten(self):
        (self.root / '.git').write_text('gitdir: elsewhere')
        self.server.publish(self.b, source_files(self.b, {'gui.py': b'new'}))
        with self.assertRaisesRegex(MirrorError, 'worktree'):
            run_update(self.manager, client=self.client)
        self.assertFalse((self.root / 'gui.py').exists())

    def test_cdk_encryption_mask_and_clear(self):
        save_cdk(self.root, 'test-secret-cdk')
        self.assertNotIn('test-secret-cdk', credential_path(self.root).read_text())
        self.assertEqual(load_cdk(self.root), 'test-secret-cdk')
        for placeholder in ('', '******', '••••'):
            save_cdk(self.root, placeholder)
            self.assertEqual(load_cdk(self.root), 'test-secret-cdk')
        save_cdk(self.root, clear=True)
        self.assertEqual(load_cdk(self.root), '')

    def test_desktop_full_zip_validated_and_staged(self):
        data = b'MZ simulated executable'
        manifest = {
            'schema': 1,
            'desktop_version': '1.1.4',
            'target': 'x86_64-pc-windows-msvc',
            'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
        }
        self.server.publish('1.1.4', {'nkas.exe': data, 'nkas-desktop.json': json.dumps(manifest).encode()})
        result = stage_desktop(self.client, self.server.data)
        self.assertEqual(Path(result['path']).read_bytes(), data)
        self.assertFalse((self.root / 'nkas.exe').exists())
        self.server.data['update_type'] = 'incremental'
        with self.assertRaisesRegex(MirrorError, '全量'):
            stage_desktop(self.client, self.server.data)

    def test_incremental_deletion_cannot_touch_user_config(self):
        old = source_files(self.a, {'gui.py': b'old'})
        write_files(self.root, old)
        new = source_files(self.b, {'gui.py': b'new'})
        new['changes.json'] = json.dumps({'modified': list(new), 'deleted': ['config/deploy.yaml']}).encode()
        self.server.publish(self.b, new, 'incremental')
        with self.assertRaises(MirrorError):
            run_update(self.manager, client=self.client)
        self.assertEqual((self.root / 'gui.py').read_bytes(), b'old')
        self.assertIn('true', (self.root / 'config/deploy.yaml').read_text())

    def test_invalid_local_baseline_requests_full_without_changing_channel(self):
        write_files(self.root, source_files(self.a, {'gui.py': b'old'}))
        (self.root / 'gui.py').write_bytes(b'changed')
        self.server.publish(self.b, source_files(self.b, {'gui.py': b'new'}))
        run_update(self.manager, client=self.client)
        self.assertNotIn('current_version', self.server.requests[0][1])
        self.manager.git_update.assert_not_called()

    def test_git_migration_and_manual_switch_back_reconcile_owned_files(self):
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)

        git('init')
        (self.root / 'gui.py').write_bytes(b'git source')
        git('add', 'gui.py')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'baseline')
        old_sha = git('rev-parse', 'HEAD').decode().strip()
        self.server.publish(self.b, source_files(self.b, {'gui.py': b'mirror source', 'module/extra.py': b'added'}))
        run_update(self.manager, client=self.client)
        self.assertEqual(current_version(self.root), self.b)
        self.assertEqual(git('rev-parse', 'HEAD').decode().strip(), old_sha)
        (self.root / 'config/deploy.yaml').write_text('MirrorChyanEnabled: false\n')
        self.manager.git_update.side_effect = lambda: git('reset', '--hard', old_sha)
        self.server.requests.clear()
        self.manager.pip_install.side_effect = RuntimeError('pip failed')
        with self.assertRaisesRegex(MirrorError, 'pip failed'):
            run_update(self.manager, client=self.client)
        with self.assertRaises(MirrorError):
            assert_ready(self.root)
        self.manager.pip_install.side_effect = None
        run_update(self.manager, client=self.client)
        self.assertEqual(current_version(self.root), old_sha)
        self.assertEqual((self.root / 'gui.py').read_bytes(), b'git source')
        self.assertFalse((self.root / 'module/extra.py').exists())
        self.assertFalse((self.root / MANIFEST).exists())
        self.assertEqual(self.server.requests, [])

    def test_source_builder_uses_fixed_commit_and_excludes_private_files(self):
        from deploy.build_mirror_package import source_package

        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)

        git('init')
        write_files(
            self.root,
            {
                'gui.py': b'committed',
                'config/notices.yaml': b'notice',
                'config/test.acc': b'private',
                'webui/dist/index.html': b'ui',
            },
        )
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'baseline')
        sha = git('rev-parse', 'HEAD').decode().strip()
        (self.root / 'gui.py').write_bytes(b'uncommitted')
        package = self.root / 'source.zip'
        source_package(self.root, sha, package)
        with zipfile.ZipFile(package) as zipped:
            self.assertEqual(zipped.read('gui.py'), b'committed')
            self.assertNotIn('config/test.acc', zipped.namelist())
            self.assertNotIn('config/deploy.yaml', zipped.namelist())
            self.assertEqual(json.loads(zipped.read(VERSION)), {'version': sha})
            self.assertEqual(json.loads(zipped.read(HISTORY))['commits'][0]['sha'], sha)

    def test_interrupted_desktop_replacement_recovers_only_verified_backup(self):
        write_files(self.root, {'nkas.exe': b'partial', 'tmp/desktop-update/nkas-current.exe.old': b'previous'})
        pending = self.root / STATE / 'desktop-pending.json'
        atomic_json(
            pending,
            {
                'pid': 2147483647,
                'previous_sha256': hashlib.sha256(b'previous').hexdigest(),
                'target_sha256': hashlib.sha256(b'new').hexdigest(),
            },
        )
        recover_desktop(self.root)
        self.assertEqual((self.root / 'nkas.exe').read_bytes(), b'previous')
        self.assertFalse(pending.exists())
        atomic_json(pending, {'pid': os.getpid()})
        with self.assertRaisesRegex(MirrorError, '仍在运行'):
            recover_desktop(self.root)

    def test_missing_url_still_allows_public_version_and_history_metadata(self):
        from deploy.source_update import history, refresh_history
        self.server.data = {'version_name': self.b}
        info, available = check_source(self.root, client=self.client, require_url=False)
        self.assertTrue(available)
        self.assertEqual(info['version_name'], self.b)
        local = {'version': self.a, 'commits': [{'sha': self.a, 'message': 'local'}]}
        atomic_json(self.root / HISTORY, local)
        with patch('urllib.request.urlopen', side_effect=TimeoutError):
            self.assertFalse(refresh_history(self.root, self.b))
        self.assertEqual(history(self.root)[0]['sha'], self.a)
        remote = {'version': self.b, 'commits': [{'sha': self.b, 'message': 'remote'}]}
        response = io.BytesIO(json.dumps(remote).encode())
        with patch('urllib.request.urlopen', return_value=response):
            self.assertTrue(refresh_history(self.root, self.b))
        self.assertEqual(history(self.root)[0]['sha'], self.b)


if __name__ == '__main__':
    unittest.main()
