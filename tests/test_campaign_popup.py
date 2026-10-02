import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.campaign_prototype import goto, settings
from module.campaign_prototype.manual_move import GameSession
from module.campaign_prototype.movement_feedback import TargetTriggered


class PopupTests(unittest.TestCase):
    def field(self, ex=False):
        image = np.zeros((999, 1776, 3), np.uint8)
        for template, (x, y) in [(goto.ENTER_BATTLE_TPL, (900, 800))] + (
                [(goto.EX_STAGE_TPL, (800, 200))] if ex else []):
            height, width = template.shape[:2]
            image[y:y + height, x:x + width] = template
        return image

    def session(self, purpose='enemy'):
        win = Mock()
        win.gui.ClientToScreen.return_value = (100, 200)
        return SimpleNamespace(request={'purpose': purpose}, win=win, index=0, preview=Mock(), model=Mock())

    def test_normal_popup_remains_open_without_entering_battle(self):
        session = self.session()
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, 'output', Path(folder)):
            with self.assertRaises(TargetTriggered) as caught:
                GameSession.identity(session, self.field())
        self.assertEqual(caught.exception.evidence['kind'], 'battle_popup')
        session.win.handler.mouse_click.assert_not_called()
        session.model.predict.assert_not_called()

    def test_ex_popup_is_closed_once_and_reports_exclusion(self):
        session = self.session()
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, 'output', Path(folder)), \
                patch.object(goto, 'capture_client', return_value=np.zeros((999, 1776, 3), np.uint8)), \
                patch.object(goto.runtime, 'pause'):
            with self.assertRaises(TargetTriggered) as caught:
                GameSession.identity(session, self.field(ex=True))
            self.assertTrue(Path(caught.exception.evidence['screenshot']).is_file())
        self.assertEqual(caught.exception.evidence['kind'], 'ex_stage_skipped')
        session.win.handler.mouse_click.assert_called_once_with(1255, 721)
        session.model.predict.assert_not_called()

    def test_ex_close_failure_propagates_without_retry_or_success(self):
        session = self.session()
        field = self.field(ex=True)
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, 'output', Path(folder)), \
                patch.object(goto, 'capture_client', return_value=field), patch.object(goto.runtime, 'pause'):
            with self.assertRaisesRegex(RuntimeError, 'did not close'):
                GameSession.identity(session, field)
        session.win.handler.mouse_click.assert_called_once()

    def test_non_enemy_mode_stops_without_popup_input(self):
        for purpose in ('position', 'collectible'):
            session = self.session(purpose)
            with self.subTest(purpose=purpose), self.assertRaisesRegex(RuntimeError, '战斗弹窗'):
                GameSession.identity(session, self.field(ex=True))
            session.win.handler.mouse_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
