"""
移植自 ok-script 1.0.190 的窗口交互层（PostMessage 机制）。

来源：
- ok/device/interaction_methods/post_message.py（PostMessageInteraction 本体）
- ok/device/capture_methods/hwnd_window.py（HwndWindow 窗口模型）

ok-script 许可证为 AGPL-3.0（https://github.com/ok-oldking/ok-script），
本目录为按需摘取的适配版，不引入完整 ok-script 依赖。

适配改动（相对 ok 原版）：
- ok.util.logger -> module.logger
- ok 的 BaseInteraction / capture 体系 -> 由调用方提供窗口信息（HwndWindowAdapter）
- 键盘相关方法（send_key / input_text）未移植，键盘输入仍走
  module.device.win.input.Input 的 SendInput 扫描码链路
- ok 的 swipe 已扩展为光标辅助的后台拖拽消息，滚动同样转换为拖拽：真实光标沿路径移动，
  鼠标按键和移动状态通过 PostMessage 发送到游戏窗口
"""
from module.device.win.ok_interaction.hwnd_window import HwndWindowAdapter
from module.device.win.ok_interaction.input import PostMessageInput
from module.device.win.ok_interaction.post_message import PostMessageInteraction

__all__ = ['HwndWindowAdapter', 'PostMessageInput', 'PostMessageInteraction']
