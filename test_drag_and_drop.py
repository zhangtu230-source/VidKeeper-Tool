"""
拖拽文件功能测试程序（与主程序使用相同的技术栈 tkinterdnd2）

目标：
1. 验证 tkinterdnd2 是否可用、DND_FILES 事件能否触发
2. 验证 PanedWindow 容器上的 drop_target_register 能否接收拖拽
3. 验证多文件、带空格路径、文件夹等多种输入
4. 逐级缩小测试范围，帮助定位主程序拖拽失效的具体原因

使用方式：
  python test_drag_and_drop.py
"""

import os
import sys
import shlex
import ctypes
import tkinter as tk
from tkinter import ttk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False
    print("⚠ 未安装 tkinterdnd2，尝试使用 pip install tkinterdnd2")
    sys.exit(1)


# ============================================================
# 权限检测：UAC 管理员权限会导致拖放被 Windows UIPI 阻止（显示🚫）
# ============================================================
def is_admin():
    """检测当前进程是否以管理员权限运行"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def print_diag_info():
    """启动时打印诊断信息"""
    print("=" * 60)
    print("拖拽诊断信息")
    print("=" * 60)
    print("Python:", sys.executable)
    print("进程PID:", os.getpid())
    print("当前工作目录:", os.getcwd())

    admin = is_admin()
    if admin:
        print("⚠⚠⚠ 当前进程以【管理员权限】运行！⚠⚠⚠")
        print("  → Windows UIPI 会阻止从普通权限的资源管理器拖文件到管理员进程")
        print("  → 这就是 🚫 图标的根因，无论代码怎么写都无法绕过")
        print("  → 解决方法：用【普通权限】的终端/双击运行本程序，不要用管理员终端")
    else:
        print("✅ 当前进程以【普通权限】运行（UAC 不是问题原因）")

    # 检测 tkinterdnd2 内部 tkdnd 库是否真正加载
    try:
        import tkinterdnd2
        print("tkinterdnd2 版本:", getattr(tkinterdnd2, '__version__', '未知'))
        print("tkinterdnd2 路径:", tkinterdnd2.__file__)
    except Exception as ex:
        print("⚠ tkinterdnd2 信息读取失败:", ex)

    print("=" * 60)


def verify_tkdnd_tcl_loaded(root):
    """
    关键验证：Python 层 import 成功 ≠ Tcl tkdnd 扩展真正加载。
    用 Tcl 命令 package require tkdnd 验证底层扩展是否可用。
    返回 (True, version) 或 (False, error)
    """
    try:
        ver = root.tk.call('package', 'require', 'tkdnd')
        return True, str(ver)
    except Exception as ex:
        return False, str(ex)



# ============================================================
# 递归给 widget 及其所有子控件注册 DND（解决🚫图标问题）
# ============================================================
def register_drop_recursive(widget, callback):
    """
    给 widget 及其所有子控件递归注册 DND_FILES + <<Drop>>。

    原因：PanedWindow 注册了 DND，但内部的 Listbox/Label/Scrollbar 等子控件
    覆盖了父控件的整个区域，鼠标实际悬停在子控件上，而子控件没注册 DND，
    Windows 会显示 🚫 禁止图标，<<Drop>> 事件也不会触发。
    """
    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind('<<Drop>>', callback)
    except Exception as ex:
        # 某些控件（如 Scrollbar）可能不支持，忽略
        print("⚠ 注册DND失败 %s: %s" % (widget.__class__.__name__, ex))
    for child in widget.winfo_children():
        register_drop_recursive(child, callback)


# ============================================================
# 与主程序 gui.py 完全一致的 on_drop 解析逻辑
# ============================================================
def parse_drop_data_original(data):
    """主程序 gui.py L338-L364 的原始解析方式"""
    paths = []

    if data.startswith('{'):
        data = data[1:-1]
        parts = data.split('} {')
        for part in parts:
            part = part.strip()
            if part:
                paths.append(part)
    else:
        try:
            paths = shlex.split(data)
        except Exception:
            paths = data.split()

    return paths


# ============================================================
# 备用解析方式（更稳健，解决路径中花括号、非平衡引号等问题）
# ============================================================
def parse_drop_data_robust(data):
    """更稳健的解析：按 Tk DND 的 {path with space} 格式逐个截取"""
    paths = []
    i = 0
    n = len(data)
    while i < n:
        ch = data[i]
        if ch == '{':
            # 花括号段：找下一个未转义的 }
            j = i + 1
            while j < n:
                if data[j] == '}' and (j == n - 1 or data[j + 1] in (' ', '\t', '\n', '\r')):
                    break
                if data[j] == '\\' and j + 1 < n:
                    j += 2
                    continue
                j += 1
            paths.append(data[i + 1:j])
            i = j + 1
            while i < n and data[i] in ' \t\n\r':
                i += 1
        elif ch in ' \t\n\r':
            i += 1
        else:
            # 非花括号段：空格分隔的单 token
            j = i
            while j < n and data[j] not in ' \t\n\r':
                j += 1
            paths.append(data[i:j])
            i = j
    return [p for p in paths if p]


class DragDropTestApp:
    """拖拽功能测试界面"""

    def __init__(self, root):
        self.root = root
        self.root.title("拖拽文件功能测试（tkinterdnd2）")
        self.root.geometry("900x680")

        self._build_ui()
        self._log("✅ tkinterdnd2 已导入，DND_FILES=%s 可用" % DND_FILES)
        # 权限状态
        if is_admin():
            self._log("⚠⚠⚠ 当前以【管理员权限】运行 → 拖拽会被 Windows UIPI 阻止（🚫图标根因）")
            self._log("  → 关闭本终端，用【普通权限】重新运行（不要用管理员终端）")
        else:
            self._log("✅ 当前以【普通权限】运行（UAC 不是问题）")
        self._log("=" * 70)
        self._log("操作说明：")
        self._log("  1. 把文件/文件夹拖到下方三个区域中的任意一个")
        self._log("  2. 查看日志，确认 on_drop 事件是否触发、原始 event.data 内容")
        self._log("  3. 如某个区域响应而 PanedWindow 不响应，说明问题出在容器层级")

    # ---- UI ----
    def _build_ui(self):
        # 第一步：先建日志面板（确保后面的 _log 可用）
        log_frame = tk.LabelFrame(self.root, text="详细日志（原始 event.data + 解析结果）",
                                  font=('Microsoft YaHei', 10, 'bold'), padx=6, pady=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        self.log_text = tk.Text(log_frame, wrap='word', font=('Consolas', 9),
                                height=10, state=tk.DISABLED)
        scrollbar = tk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 顶部：测试结果面板
        top = tk.LabelFrame(self.root, text="测试结果面板", font=('Microsoft YaHei', 10, 'bold'), padx=8, pady=6)
        top.pack(fill=tk.X, padx=8, pady=6)

        self.lbl_trigger = tk.Label(top, text="❌ on_drop 未触发", fg='red', font=('Microsoft YaHei', 11, 'bold'))
        self.lbl_trigger.grid(row=0, column=0, padx=10, pady=4, sticky='w')

        self.lbl_region = tk.Label(top, text="触发区域: —", fg='#666', font=('Microsoft YaHei', 9))
        self.lbl_region.grid(row=0, column=1, padx=10, pady=4, sticky='w')

        self.lbl_paths = tk.Label(top, text="解析路径数: —", fg='#666', font=('Microsoft YaHei', 9))
        self.lbl_paths.grid(row=0, column=2, padx=10, pady=4, sticky='w')

        tk.Button(top, text="清空日志", command=self.clear_log,
                  font=('Microsoft YaHei', 9), width=10).grid(row=0, column=3, padx=8, sticky='e')
        top.columnconfigure(3, weight=1)

        # 中部：三个拖拽区域（模拟主程序不同层级）
        zones = tk.LabelFrame(self.root, text="拖拽测试区域（拖到任一区域）",
                              font=('Microsoft YaHei', 10, 'bold'), padx=8, pady=6)
        zones.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 区域 A：PanedWindow（与主程序 L183-220 完全一致的容器层级）
        zone_a = tk.LabelFrame(zones, text="A. PanedWindow（与主程序完全一致的容器）",
                               font=('Microsoft YaHei', 9, 'bold'), padx=6, pady=6)
        zone_a.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

        self.paned_window = tk.PanedWindow(zone_a, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        self.paned_window.pack(fill=tk.BOTH, expand=True)

        left_a = tk.Frame(self.paned_window)
        tk.Label(left_a, text="左子面板（Listbox）", fg='gray',
                 font=('Microsoft YaHei', 9)).pack(pady=3)
        lbox_a = tk.Listbox(left_a, font=('Microsoft YaHei', 9))
        lbox_a.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.paned_window.add(left_a, width=280, minsize=180)

        right_a = tk.Frame(self.paned_window)
        tk.Label(right_a, text="右子面板（Listbox）", fg='gray',
                 font=('Microsoft YaHei', 9)).pack(pady=3)
        lbox_b = tk.Listbox(right_a, font=('Microsoft YaHei', 9))
        lbox_b.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.paned_window.add(right_a)

        # ✅ 关键修复：递归注册 PanedWindow 及所有子控件，否则子控件遮挡会导致 🚫 图标
        register_drop_recursive(self.paned_window, lambda e: self.on_drop_wrapper('A(PanedWindow)', e, lbox_a))
        self._log("✅ 区域A PanedWindow + 所有子控件 递归注册 DND_FILES 成功")

        # 区域 B：普通 Frame（对比测试）
        zone_b = tk.LabelFrame(zones, text="B. 普通 Frame（基线测试）",
                               font=('Microsoft YaHei', 9, 'bold'), padx=6, pady=6)
        zone_b.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

        self.frame_drop = tk.Frame(zone_b, bg='#f5f5ff', relief=tk.SUNKEN, bd=2)
        self.frame_drop.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        tk.Label(self.frame_drop, text="把文件拖到这里（Frame）",
                 font=('Microsoft YaHei', 10), bg='#f5f5ff', fg='#555').pack(pady=30)
        self.frame_listbox = tk.Listbox(self.frame_drop, font=('Microsoft YaHei', 9))
        self.frame_listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.frame_drop.drop_target_register(DND_FILES)
        self.frame_drop.dnd_bind('<<Drop>>', lambda e: self.on_drop_wrapper('B(Frame)', e, self.frame_listbox))
        # 同样递归注册子控件（Label + Listbox）
        register_drop_recursive(self.frame_drop, lambda e: self.on_drop_wrapper('B(Frame)', e, self.frame_listbox))
        self._log("✅ 区域B Frame + 所有子控件 递归注册 DND_FILES 成功")

        # 区域 C：顶层 root（最大的拖拽接收区域）
        zone_c = tk.LabelFrame(zones, text="C. 整个主窗口 Tk 根（最宽松）",
                               font=('Microsoft YaHei', 9, 'bold'), padx=6, pady=6)
        zone_c.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

        self.root_frame = tk.Frame(zone_c, bg='#fff5f5', relief=tk.SUNKEN, bd=2)
        self.root_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        tk.Label(self.root_frame, text="把文件拖到这里（整个窗口级）",
                 font=('Microsoft YaHei', 10), bg='#fff5f5', fg='#555').pack(pady=30)
        self.root_listbox = tk.Listbox(self.root_frame, font=('Microsoft YaHei', 9))
        self.root_listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', lambda e: self.on_drop_wrapper('C(窗口根)', e, self.root_listbox))
            # 注意：root 的递归注册会覆盖整个窗口所有控件（包括A/B），这里只注册 root 本身
            self._log("✅ 区域C 顶层 root 注册 DND_FILES 成功")
        except Exception as ex:
            self._log("⚠ 区域C 顶层 root 注册 DND_FILES 失败: %s（不影响A/B测试）" % ex)

    # ---- 事件处理（完全复刻主程序 + 稳健版本对比）----
    def on_drop_wrapper(self, region, event, target_listbox):
        """
        包装 on_drop：
          1. 记录原始 event.data
          2. 用主程序方式解析 → 校验
          3. 用稳健方式解析 → 对比
          4. 高亮更新状态标签
        """
        self.lbl_trigger.config(text="✅ on_drop 已触发", fg='green')
        self.lbl_region.config(text="触发区域: %s" % region, fg='#0055cc')

        data = event.data
        self._log("-" * 70)
        self._log("[%s] 触发 Drop" % region)
        self._log("  event.data (原始): %s" % repr(data))

        # 方法1：主程序原始解析
        try:
            paths_original = parse_drop_data_original(data)
        except Exception as ex:
            paths_original = []
            self._log("  ❌ 原始解析抛出异常: %s" % ex)
        self._log("  解析方式① (主程序原始): %d 个路径" % len(paths_original))
        for i, p in enumerate(paths_original):
            exists = os.path.exists(p)
            mark = "✅" if exists else "❌不存在"
            self._log("    [%d] %s  %s" % (i + 1, mark, p))

        # 方法2：稳健解析
        try:
            paths_robust = parse_drop_data_robust(data)
        except Exception as ex:
            paths_robust = []
            self._log("  ❌ 稳健解析抛出异常: %s" % ex)
        self._log("  解析方式② (稳健版):     %d 个路径" % len(paths_robust))
        for i, p in enumerate(paths_robust):
            exists = os.path.exists(p)
            mark = "✅" if exists else "❌不存在"
            self._log("    [%d] %s  %s" % (i + 1, mark, p))

        # 判定解析是否一致（不一致说明原始解析有bug）
        if paths_original != paths_robust:
            self._log("  ⚠ 两种解析结果不一致！主程序原始解析可能有问题（改用稳健解析）")
        else:
            self._log("  ✅ 两种解析结果一致")

        # 实际使用：优先稳健解析的结果
        paths = paths_robust if paths_robust else paths_original
        self.lbl_paths.config(text="解析路径数: %d" % len(paths), fg='#0055cc')

        # 分类：文件 vs 文件夹
        video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
                      '.ts', '.m2ts', '.mpg', '.mpeg', '.3gp', '.m4v')
        video_files = []
        folders = []
        for p in paths:
            if os.path.isfile(p):
                if p.lower().endswith(video_exts):
                    video_files.append(p)
            elif os.path.isdir(p):
                folders.append(p)

        self._log("  → 视频文件: %d 个, 文件夹: %d 个" % (len(video_files), len(folders)))

        # 填入 Listbox
        target_listbox.delete(0, tk.END)
        for p in video_files:
            target_listbox.insert(tk.END, "[视频] " + p)
        for p in folders:
            # 统计文件夹下视频数量
            count = 0
            for r, ds, fs in os.walk(p):
                for f in fs:
                    if f.lower().endswith(video_exts):
                        count += 1
                if count > 1000:
                    break
            target_listbox.insert(tk.END, "[文件夹:%d视频] %s" % (count, p))

    # ---- 日志 ----
    def _log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.lbl_trigger.config(text="❌ on_drop 未触发", fg='red')
        self.lbl_region.config(text="触发区域: —", fg='#666')
        self.lbl_paths.config(text="解析路径数: —", fg='#666')


def main():
    print("正在启动拖拽测试程序...")
    print("DND_AVAILABLE =", DND_AVAILABLE)
    print_diag_info()

    # 与主程序 main.py#L31 完全一致：使用 tkinterdnd2.Tk()
    root = TkinterDnD.Tk()

    # 关键：Tcl 级别验证 tkdnd 扩展是否真正加载
    ok, info = verify_tkdnd_tcl_loaded(root)
    if ok:
        print("✅ Tcl tkdnd 扩展已加载，版本:", info)
    else:
        print("❌ Tcl tkdnd 扩展加载失败:", info)
        print("  → 这说明 tkinterdnd2 Python 包虽装了，但底层 Tcl 扩展没找到")
        print("  → 需要重装: pip uninstall tkinterdnd2 -y && pip install tkinterdnd2")

    app = DragDropTestApp(root)
    # 把 Tcl 验证结果也输出到 GUI 日志
    if ok:
        app._log("✅ Tcl tkdnd 扩展已加载，版本: %s" % info)
    else:
        app._log("❌ Tcl tkdnd 扩展加载失败: %s" % info)
        app._log("  → 需要重装: pip uninstall tkinterdnd2 -y && pip install tkinterdnd2")

    # 如果是管理员权限，弹窗警告（不自动重启，因为 explorer.exe 无法启动 .py）
    if is_admin():
        from tkinter import messagebox
        messagebox.showwarning(
            "权限问题导致拖拽失效",
            "检测到当前程序以【管理员权限】运行。\n\n"
            "Windows UIPI 安全机制会阻止从普通权限的资源管理器\n"
            "拖文件到管理员进程，这就是 🚫 图标的根因。\n\n"
            "请手动操作：\n"
            "1. 关闭当前管理员终端\n"
            "2. 打开普通 PowerShell（不要右键'以管理员身份运行'）\n"
            "3. 或直接在资源管理器中双击 test_drag_and_drop.py\n\n"
            "点击确定继续以管理员权限运行（拖拽仍会失效）。",
            icon=messagebox.WARNING
        )

    root.mainloop()


if __name__ == '__main__':
    main()
