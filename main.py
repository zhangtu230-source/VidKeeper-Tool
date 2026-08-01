import sys
import subprocess

import tkinter as tk
from tkinter import messagebox
import tkinterdnd2

from gui import VideoBatchProcessor


def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        return True
    except FileNotFoundError:
        return False


def main():
    if not check_ffmpeg():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "错误",
            "未找到 FFmpeg！\n\n请先安装 FFmpeg 并添加到系统 PATH：\nWindows: 下载 https://ffmpeg.org/download.html\nmacOS: brew install ffmpeg\nLinux: sudo apt install ffmpeg"
        )
        root.destroy()
        sys.exit(1)
    
    root = tkinterdnd2.Tk()
    app = VideoBatchProcessor(root)
    root.mainloop()


if __name__ == '__main__':
    main()