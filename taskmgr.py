# -*- coding: utf-8 -*-
"""后台子进程任务管理（Windows spawn）：
负责进程/队列/取消/暂停事件，UI 通过 poll() 轮询结果消息。
消息协议见 core/ocr 的 _mp_* 入口：('progress',d,t,m) / ('done',...) / ('done_write',...) /
('extract_done',...) / ('extract_none',) / ('ocr_done',txt) / ('ocr_offset',offset) / ('error',msg)
"""
import multiprocessing as mp
import queue


class TaskManager:
    def __init__(self):
        self._ctx = mp.get_context('spawn')
        self.q = self._ctx.Queue()
        self.cancel_event = self._ctx.Event()
        self.pause_event = self._ctx.Event()
        self._proc = None
        self._active = False

    @property
    def active(self):
        return self._active

    def start(self, target, args):
        """启动子进程；args 不含队列（自动前置）"""
        if self._active:
            raise RuntimeError('任务正在进行中')
        self.cancel_event.clear()
        self.pause_event.clear()
        self._active = True
        self._proc = self._ctx.Process(target=target, args=(self.q,) + tuple(args), daemon=True)
        self._proc.start()

    def finish(self):
        """任务结束（收到结果/错误消息）后调用"""
        self._active = False
        self._proc = None

    def stop(self):
        self.cancel_event.set()

    def pause(self, on):
        if on:
            self.pause_event.set()
        else:
            self.pause_event.clear()

    def poll(self):
        """取一条消息；无消息返回 None"""
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None
