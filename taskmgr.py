# -*- coding: utf-8 -*-
"""后台子进程任务管理（Windows spawn 上下文）。

职责：管理子进程生命周期、消息队列、取消/暂停事件。
UI 侧通过 poll_message() 轮询结果消息，消息协议见 core/ocr 的 _mp_* 入口：
('progress', done, total, message) / ('done', ...) / ('done_write', ...) /
('extract_done', ...) / ('extract_none',) / ('ocr_done', text) /
('ocr_offset', offset) / ('error', error_message)

说明：任务运行在独立子进程中（spawn 模式，兼容 Windows 无 fork），
并非线程池；子进程与 UI 进程仅通过 multiprocessing 队列/事件交互。
本模块不含看门狗/超时检测，子进程异常退出时的处理由调用方负责。
"""
import queue
from multiprocessing import get_context
from multiprocessing.context import BaseContext
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from typing import Any, Callable, Optional, Tuple


class TaskManager:
    """后台任务管理器：spawn 子进程 + 队列 + 取消/暂停事件"""

    def __init__(self) -> None:
        # spawn 上下文：队列与两个事件均为跨进程共享对象，
        # 子进程通过构造参数拿到同一实例后才能互相通信
        self._spawn_context: BaseContext = get_context('spawn')
        self.message_queue: Queue = self._spawn_context.Queue()
        self.cancel_event: Event = self._spawn_context.Event()
        self.pause_event: Event = self._spawn_context.Event()
        self._child_process: Optional[Any] = None
        self._is_task_active: bool = False

    @property
    def is_task_active(self) -> bool:
        return self._is_task_active

    def start(self, target: Callable, arguments: Tuple[Any, ...]) -> None:
        """启动子进程；arguments 不含消息队列（会自动前置）"""
        if self._is_task_active:
            raise RuntimeError('任务正在进行中')
        # 重置两个控制事件，保证新任务初始处于"未取消、未暂停"状态
        self.cancel_event.clear()
        self.pause_event.clear()
        self._is_task_active = True
        # 消息队列自动作为第一个参数前置，子进程的 _mp_* 入口据此收消息
        self._child_process = self._spawn_context.Process(
            target=target, args=(self.message_queue,) + tuple(arguments), daemon=True)
        self._child_process.start()

    def finish(self) -> None:
        """任务结束（收到结果/错误消息）后调用，释放进程引用"""
        # 进程对象在子进程退出后需释放，避免句柄泄漏与僵尸进程
        self._is_task_active = False
        self._child_process = None

    def request_stop(self) -> None:
        """请求取消任务：置位取消事件，子进程在检查点读到后自行退出"""
        self.cancel_event.set()

    def set_paused(self, is_paused: bool) -> None:
        """暂停/恢复：置位或清除暂停事件，子进程在检查点等待/继续"""
        if is_paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()

    def poll_message(self) -> Optional[Tuple]:
        """取一条结果消息；队列为空返回 None"""
        # 非阻塞读取，供 UI 主循环轮询，避免界面卡死
        try:
            return self.message_queue.get_nowait()
        except queue.Empty:
            return None
