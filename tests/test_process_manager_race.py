"""
ProcessManager.get_manager 并发安全回归测试。

背景：后端启动时编排器线程与 restart_processes 会并发调用 get_manager，
无锁的 check-then-act 曾导致同一实例名创建出两个 manager，后写入的空对象
覆盖了真正 fork 实例进程的对象，后端误判实例已停止、串行令牌永不授予。
"""
import queue
import threading
import time
import unittest

from module.webui import process_manager
from module.webui.process_manager import ProcessManager


class DummyManager:
    """替代 State.manager（multiprocessing.Manager），Queue 加微小延迟放大竞态窗口"""

    def Queue(self, *args, **kwargs):
        time.sleep(0.002)
        return queue.Queue()


class GetManagerRaceTests(unittest.TestCase):
    def setUp(self):
        self._orig_manager = process_manager.State.manager
        self._orig_processes = dict(ProcessManager._processes)
        process_manager.State.manager = DummyManager()

    def tearDown(self):
        process_manager.State.manager = self._orig_manager
        ProcessManager._processes.clear()
        ProcessManager._processes.update(self._orig_processes)

    def test_concurrent_get_manager_returns_same_object(self):
        for _ in range(20):
            ProcessManager._processes.clear()
            results = []
            barrier = threading.Barrier(9)

            def worker():
                barrier.wait()
                results.append(ProcessManager.get_manager('nkas'))

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            barrier.wait()
            for t in threads:
                t.join()

            self.assertEqual(len(ProcessManager._processes), 1)
            self.assertEqual(len({id(m) for m in results}), 1)

    def test_campaign_revision_changes_for_same_observation_time(self):
        """同一观测时间的连续推图发布仍递增版本，确保前端能收到补充后的运行记忆。"""
        manager = ProcessManager('nkas')
        manager._campaign_started_at = 1
        manager._preview_queue.put((b'first', {'campaign': {'captured_at': 2, 'value': 1}}))
        manager._preview_queue.put((b'second', {'campaign': {'captured_at': 2, 'value': 2}}))
        manager._thread_preview_handler()
        self.assertEqual(manager.campaign_revision, 2)
        self.assertEqual(manager.latest_campaign['value'], 2)


if __name__ == '__main__':
    unittest.main()
