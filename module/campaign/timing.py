"""推图流程的计时标记：让日志能回答「这段时间花在哪」。"""

import time


class Stopwatch:
    """单调时钟秒表：日志里所有「耗时 x.xxs」都从这里取。

    用 time.monotonic 而不是 time.time：计时不受系统时钟校准影响。同一个实例可以反复 reset，
    给「一拍内的多个阶段」共用；两段并行或嵌套的计时各自建实例，不共享状态。
    """

    def __init__(self):
        """从当前时刻起算。"""
        self.started = time.monotonic()

    def reset(self):
        """把起点挪到当前时刻并返回自身，便于 `watch = watch.reset()` 复用同一个实例。"""
        self.started = time.monotonic()
        return self

    @property
    def seconds(self):
        """自起点以来的用时（秒）；取整与小数位由调用方决定。"""
        return time.monotonic() - self.started

    def __str__(self):
        """直接拼进 f-string：`耗时 {watch}` 得到 `耗时 1.23s`。"""
        return f'{self.seconds:.2f}s'
