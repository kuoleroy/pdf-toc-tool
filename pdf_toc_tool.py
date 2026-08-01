# -*- coding: utf-8 -*-
"""PDF 书签工具入口：无参数启动GUI，带参数走CLI"""
import multiprocessing
import sys


def main() -> int:
    """程序入口：冻结支持（打包exe多进程必需），按参数分发 CLI/GUI"""
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:
        import cli
        return cli.main(sys.argv[1:])
    import gui
    return gui.main()


if __name__ == '__main__':
    sys.exit(main())
