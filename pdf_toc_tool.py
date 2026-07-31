# -*- coding: utf-8 -*-
"""PDF 书签工具入口：无参数启动GUI，带参数走CLI"""
import sys


def main():
    if len(sys.argv) > 1:
        import cli
        return cli.main(sys.argv[1:])
    import gui
    return gui.main()


if __name__ == '__main__':
    sys.exit(main())
