import os
import sys
import subprocess
import shlex
import threading
import queue
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES

from config import MODE_INFO, VIDEO_EXTENSIONS, LEGACY_EXTENSIONS, COMPRESS_4K_DEFAULT, ENABLE_FPS_LIMIT
from utils import get_timestamp
from analyzer import process_mode_0
from mode_1_2 import process_mode_1_2
from mode_3 import process_mode_3
from mode_4 import process_mode_4
from mode_5 import process_mode_5
from mode_6 import process_mode_6
from mode_7 import process_mode_7


class VideoBatchProcessor:
    def __init__(self, root):
        self.root = root
        self.root.title("VaultPress v2.7 · 有洁癖的视频压缩工具")
        # 设置窗口图标（兼容源码运行与 PyInstaller 打包后运行）
        try:
            base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            icon_path = os.path.join(base_dir, "VaultPress.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception:
            pass
        self.root.geometry("900x600")
        self.root.resizable(True, True)
        
        self.MANUAL_FILES_KEY = "___手动选择的文件___"
        self.folder_files = {}
        self.video_paths = []
        self.processing_thread = None
        self.scan_thread = None
        self.scan_threads = []
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.scan_queue = queue.Queue()
        self.stats_lock = threading.Lock()
        
        self.stats = {
            'total': 0,
            'completed': 0,
            'success': 0,
            'failed': 0
        }
        
        self.current_mode = 0
        
        self.auto_mode = tk.BooleanVar(value=True)
        self.custom_crf = tk.IntVar(value=23)
        self.compress_4k = tk.BooleanVar(value=COMPRESS_4K_DEFAULT)
        self.fps_limit = tk.BooleanVar(value=ENABLE_FPS_LIMIT)
        
        self.create_widgets()
        
        self.root.after(100, self.poll_log_queue)
        self.root.after(500, self.set_initial_sash_position)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def on_close(self):
        is_processing = self.processing_thread and self.processing_thread.is_alive()
        
        if is_processing:
            result = messagebox.askyesno(
                "确认退出",
                "正在处理视频中，确定要退出吗？"
            )
        else:
            result = messagebox.askyesno(
                "确认退出",
                "确定要退出吗？"
            )
        
        if result:
            if is_processing:
                self.stop_event.set()
            self.root.destroy()
    
    def set_initial_sash_position(self):
        pass
    
    def toggle_auto_mode(self):
        if self.auto_mode.get():
            self.crf_entry.config(state=tk.DISABLED)
            self.apply_crf_button.config(state=tk.DISABLED)
        else:
            self.crf_entry.config(state=tk.NORMAL)
            self.apply_crf_button.config(state=tk.NORMAL)
    
    def apply_crf(self):
        try:
            crf_value = int(self.crf_entry.get())
            if 0 <= crf_value <= 51:
                self.custom_crf.set(crf_value)
                self.log(f"✓ CRF值已设置为: {crf_value}")
            else:
                self.log(f"✗ CRF值必须在 0-51 之间")
        except ValueError:
            self.log(f"✗ 请输入有效的数字")
    
    def create_widgets(self):
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        toolbar_frame = tk.Frame(main_frame)
        toolbar_frame.pack(fill=tk.X, pady=2)
        
        mode_label = tk.Label(toolbar_frame, text="处理模式：", font=('Microsoft YaHei', 10))
        mode_label.pack(side=tk.LEFT, padx=(0, 3))
        
        mode_names = [MODE_INFO[i]["name"] for i in range(9)]
        self.mode_combo = ttk.Combobox(toolbar_frame, values=mode_names, state='readonly', width=40)
        self.mode_combo.current(0)
        self.mode_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.mode_combo.bind('<<ComboboxSelected>>', self.on_mode_changed)
        
        spacer = tk.Frame(toolbar_frame)
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.auto_mode_checkbox = tk.Checkbutton(toolbar_frame, text="自动模式", variable=self.auto_mode,
                                               command=self.toggle_auto_mode, font=('Microsoft YaHei', 9))
        self.auto_mode_checkbox.pack(side=tk.LEFT, padx=(0, 10))
        
        crf_label = tk.Label(toolbar_frame, text="CRF值：", font=('Microsoft YaHei', 9))
        crf_label.pack(side=tk.LEFT, padx=(0, 2))
        
        self.crf_entry = tk.Entry(toolbar_frame, width=5, font=('Microsoft YaHei', 9), state=tk.DISABLED)
        self.crf_entry.insert(0, "23")
        self.crf_entry.pack(side=tk.LEFT, padx=(0, 5))
        
        self.apply_crf_button = tk.Button(toolbar_frame, text="应用", command=self.apply_crf,
                                        font=('Microsoft YaHei', 9), width=5, state=tk.DISABLED)
        self.apply_crf_button.pack(side=tk.LEFT)
        
        path_frame = tk.Frame(main_frame)
        path_frame.pack(fill=tk.X, pady=2)
        
        tk.Label(path_frame, text="视频路径列表：", font=('Microsoft YaHei', 10)).pack(side=tk.LEFT)
        
        gpu_frame = tk.Frame(path_frame)
        gpu_frame.pack(side=tk.LEFT, padx=10)

        tk.Label(gpu_frame, text="GPU加速：", font=('Microsoft YaHei', 9)).pack(side=tk.LEFT)
        self.gpu_combo = ttk.Combobox(gpu_frame, values=["关闭", "开启", "智能判断"],
                                       font=('Microsoft YaHei', 9), width=10, state='readonly')
        self.gpu_combo.current(0)
        self.gpu_combo.pack(side=tk.LEFT)

        # 4K 压缩开关（仅模式5启用）
        self.compress_4k_checkbox = tk.Checkbutton(path_frame, text="4K压缩",
                                                   variable=self.compress_4k,
                                                   font=('Microsoft YaHei', 9),
                                                   state=tk.DISABLED)
        self.compress_4k_checkbox.pack(side=tk.LEFT, padx=(0, 10))

        # 帧率限制开关（压缩时>30fps降为30fps，默认关闭）
        self.fps_limit_checkbox = tk.Checkbutton(path_frame, text="帧率限制",
                                                 variable=self.fps_limit,
                                                 font=('Microsoft YaHei', 9),
                                                 state=tk.DISABLED)
        self.fps_limit_checkbox.pack(side=tk.LEFT, padx=(0, 10))
        
        button_frame = tk.Frame(path_frame)
        button_frame.pack(side=tk.RIGHT)
        
        tk.Button(button_frame, text="添加文件", command=self.add_files,
               font=('Microsoft YaHei', 9), width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="添加文件夹", command=self.add_folder,
               font=('Microsoft YaHei', 9), width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="移除选中", command=self.remove_selected,
               font=('Microsoft YaHei', 9), width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="清空列表", command=self.clear_list,
               font=('Microsoft YaHei', 9), width=8).pack(side=tk.LEFT, padx=2)
        
        self.file_panel_frame = tk.Frame(main_frame)
        self.file_panel_frame.pack(fill=tk.BOTH, expand=True, pady=1)
        
        self.file_panel_title = tk.Frame(self.file_panel_frame, bg='#e0e0e0', relief=tk.RAISED)
        self.file_panel_title.pack(fill=tk.X)
        
        self.file_panel_expanded = True
        
        self.file_panel_btn = tk.Button(self.file_panel_title, text="▼ 文件列表", 
                                     font=('Microsoft YaHei', 9), bg='#e0e0e0', 
                                     relief=tk.FLAT, command=self.toggle_file_panel)
        self.file_panel_btn.pack(side=tk.LEFT, padx=5, pady=2)
        
        paned_window = tk.PanedWindow(self.file_panel_frame, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        paned_window.pack(fill=tk.BOTH, expand=True)
        
        left_panel = tk.Frame(paned_window)
        
        tk.Label(left_panel, text="文件夹路径：", font=('Microsoft YaHei', 9), fg='gray').pack(fill=tk.X, pady=1)
        
        left_scrollbar = tk.Scrollbar(left_panel, orient=tk.VERTICAL)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.folder_listbox = tk.Listbox(left_panel, yscrollcommand=left_scrollbar.set, 
                                       font=('Microsoft YaHei', 9), selectmode='single')
        self.folder_listbox.pack(fill=tk.BOTH, expand=True)
        self.folder_listbox.bind('<<ListboxSelect>>', self.on_folder_selected)
        self.folder_listbox.bind('<Double-1>', self.on_folder_double_click)
        left_scrollbar.config(command=self.folder_listbox.yview)
        
        paned_window.add(left_panel, width=280, minsize=200)
        
        right_panel = tk.Frame(paned_window)
        
        tk.Label(right_panel, text="视频文件：", font=('Microsoft YaHei', 9), fg='gray').pack(fill=tk.X, pady=1)
        
        right_scrollbar = tk.Scrollbar(right_panel, orient=tk.VERTICAL)
        right_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.video_listbox = tk.Listbox(right_panel, yscrollcommand=right_scrollbar.set, 
                                      font=('Microsoft YaHei', 9), selectmode='extended')
        self.video_listbox.pack(fill=tk.BOTH, expand=True)
        self.video_listbox.bind('<Double-1>', self.on_video_double_click)
        right_scrollbar.config(command=self.video_listbox.yview)
        
        paned_window.add(right_panel)
        
        self.paned_window = paned_window
        
        self.paned_window.drop_target_register(DND_FILES)
        self.paned_window.dnd_bind('<<Drop>>', self.on_drop)
        
        control_frame = tk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=2)
        
        self.start_button = tk.Button(control_frame, text="▸ 开始处理", command=self.start_processing,
                                   font=('Microsoft YaHei', 10), bg='#4CAF50', fg='white', width=12)
        self.start_button.pack(side=tk.LEFT, padx=3)
        
        self.stop_button = tk.Button(control_frame, text="■ 停止处理", command=self.stop_processing,
                                  font=('Microsoft YaHei', 10), bg='#f44336', fg='white', 
                                  width=12, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=3)
        
        stats_frame = tk.Frame(control_frame)
        stats_frame.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        
        self.stats_label = tk.Label(stats_frame, 
                                 text="共 0 | 已完成 0 | 成功 0 | 失败 0",
                                 font=('Microsoft YaHei', 9), fg='gray')
        self.stats_label.pack(side=tk.LEFT)
        
        progress_frame = tk.Frame(control_frame)
        progress_frame.pack(side=tk.RIGHT, padx=10, fill=tk.X, expand=True)
        
        self.progress_label = tk.Label(progress_frame, 
                                    text="████████████████████████████████████████████████████ 0.0%",
                                    font=('Consolas', 9), fg='blue', anchor='w')
        self.progress_label.pack(side=tk.RIGHT, fill=tk.X)
        
        log_frame = tk.Frame(main_frame)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=2)
        
        log_text_frame = tk.Frame(log_frame)
        log_text_frame.pack(fill=tk.BOTH, expand=True)
        
        log_scrollbar_v = tk.Scrollbar(log_text_frame, orient=tk.VERTICAL)
        log_scrollbar_h = tk.Scrollbar(log_text_frame, orient=tk.HORIZONTAL)
        
        self.log_text = tk.Text(log_text_frame, wrap=tk.NONE, font=('Consolas', 9),
                              yscrollcommand=log_scrollbar_v.set,
                              xscrollcommand=log_scrollbar_h.set,
                              state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        log_scrollbar_v.pack(side=tk.RIGHT, fill=tk.Y)
        log_scrollbar_h.pack(side=tk.BOTTOM, fill=tk.X)
        log_scrollbar_v.config(command=self.log_text.yview)
        log_scrollbar_h.config(command=self.log_text.xview)
    
    def on_mode_changed(self, event):
        self.current_mode = self.mode_combo.current()
        # 4K 压缩开关仅模式5启用
        if self.current_mode == 5:
            self.compress_4k_checkbox.config(state=tk.NORMAL)
        else:
            self.compress_4k_checkbox.config(state=tk.DISABLED)
        # 帧率限制开关仅对有压缩的模式（1/2/5/6/7/8）启用
        if self.current_mode in (1, 2, 5, 6, 7, 8):
            self.fps_limit_checkbox.config(state=tk.NORMAL)
        else:
            self.fps_limit_checkbox.config(state=tk.DISABLED)
    
    def add_files(self):
        files = filedialog.askopenfilenames(
            title="选择视频文件",
            filetypes=[("视频文件", "*.mp4 *.mov *.avi *.mkv *.wmv *.flv *.webm *.m4v *.mpg *.mpeg *.ts *.m2ts *.mts *.m2t *.vob *.evo *.mod *.tod *.mxf *.gxf *.lxf *.3gp *.3g2 *.asf *.rm *.rmvb *.divx *.xvid *.ogv *.ogm *.drc *.dv *.fli *.flc *.f4v *.h264 *.h265 *.hevc *.264 *.265 *.nsv *.nut *.m4p *.mjpeg *.mjpg *.yuv *.rgb"),
                      ("所有文件", "*.*")]
        )
        
        if self.MANUAL_FILES_KEY not in self.folder_files:
            self.folder_files[self.MANUAL_FILES_KEY] = []
            self.folder_listbox.insert(tk.END, self.MANUAL_FILES_KEY)
        
        for file in files:
            if file not in self.folder_files[self.MANUAL_FILES_KEY]:
                self.folder_files[self.MANUAL_FILES_KEY].append(file)
        
        self.folder_listbox.selection_clear(0, tk.END)
        folder_keys = list(self.folder_files.keys())
        self.folder_listbox.selection_set(folder_keys.index(self.MANUAL_FILES_KEY))
        
        self.refresh_video_list(self.MANUAL_FILES_KEY)
        self.update_stats()
    
    def add_folder(self):
        folder = filedialog.askdirectory(title="选择文件夹")
        if not folder:
            return
        
        if folder in self.folder_files:
            self.folder_listbox.selection_clear(0, tk.END)
            folder_keys = list(self.folder_files.keys())
            self.folder_listbox.selection_set(folder_keys.index(folder))
            self.refresh_video_list(folder)
            self.update_stats()
            return
        
        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showwarning("提示", "正在扫描文件夹，请稍候！")
            return
        
        self.log(f"  正在扫描文件夹: {folder}...")
        
        def scan_folder():
            video_files = []
            try:
                for root, dirs, files in os.walk(folder):
                    for file in files:
                        full_path = os.path.join(root, file)
                        ext = os.path.splitext(file)[1].lower()
                        if ext in VIDEO_EXTENSIONS:
                            video_files.append(full_path)
            except Exception as e:
                self.scan_queue.put(('error', str(e)))
                return
            self.scan_queue.put(('done', folder, video_files))
        
        self.scan_thread = threading.Thread(target=scan_folder)
        self.scan_thread.start()
        self.scan_threads.append(self.scan_thread)
        self.root.after(100, self.poll_scan_queue)
    
    def on_drop(self, event):
        data = event.data
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
        
        video_files = []
        folders = []
        
        for path in paths:
            if os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    video_files.append(path)
            elif os.path.isdir(path):
                folders.append(path)
        
        if video_files:
            if self.MANUAL_FILES_KEY not in self.folder_files:
                self.folder_files[self.MANUAL_FILES_KEY] = []
                self.folder_listbox.insert(tk.END, self.MANUAL_FILES_KEY)
            
            for file in video_files:
                if file not in self.folder_files[self.MANUAL_FILES_KEY]:
                    self.folder_files[self.MANUAL_FILES_KEY].append(file)
            
            self.folder_listbox.selection_clear(0, tk.END)
            folder_keys = list(self.folder_files.keys())
            self.folder_listbox.selection_set(folder_keys.index(self.MANUAL_FILES_KEY))
            self.refresh_video_list(self.MANUAL_FILES_KEY)
        
        if folders:
            for folder in folders:
                if folder not in self.folder_files:
                    self.folder_files[folder] = []
                    self.folder_listbox.insert(tk.END, folder)
                    
                    self.log(f"  正在扫描文件夹: {folder}...")
                    
                    def scan_drop_folder(folder_path):
                        video_files = []
                        try:
                            for root, dirs, files in os.walk(folder_path):
                                for file in files:
                                    full_path = os.path.join(root, file)
                                    ext = os.path.splitext(file)[1].lower()
                                    if ext in VIDEO_EXTENSIONS:
                                        video_files.append(full_path)
                        except Exception as e:
                            self.scan_queue.put(('drop_error', folder_path, str(e)))
                            return
                        self.scan_queue.put(('drop_done', folder_path, video_files))
                    
                    t = threading.Thread(target=scan_drop_folder, args=(folder,))
                    t.start()
                    self.scan_threads.append(t)
            self.root.after(100, self.poll_scan_queue)
        
        self.update_stats()
    
    def remove_selected(self):
        video_indices = self.video_listbox.curselection()
        if video_indices:
            folder_keys = list(self.folder_files.keys())
            if folder_keys:
                current_folder = folder_keys[self.folder_listbox.curselection()[0]] if self.folder_listbox.curselection() else folder_keys[0]
                files_list = self.folder_files[current_folder]
                
                sorted_indices = sorted(video_indices, reverse=True)
                for index in sorted_indices:
                    if index < len(files_list):
                        files_list.pop(index)
                
                if not self.folder_files[current_folder]:
                    folder_index = folder_keys.index(current_folder)
                    del self.folder_files[current_folder]
                    self.folder_listbox.delete(folder_index)
                    
                    if self.folder_files:
                        new_selected = list(self.folder_files.keys())[0]
                        self.refresh_video_list(new_selected)
                        folder_keys = list(self.folder_files.keys())
                        self.folder_listbox.selection_set(folder_keys.index(new_selected))
                    else:
                        self.video_listbox.delete(0, tk.END)
                        self.video_paths.clear()
                else:
                    self.refresh_video_list(current_folder)
                
                self.update_stats()
            return
        
        folder_indices = self.folder_listbox.curselection()
        if folder_indices:
            folder_keys = list(self.folder_files.keys())
            folder_index = folder_indices[0]
            
            if folder_index >= len(folder_keys):
                return
            
            removed_folder = folder_keys[folder_index]
            del self.folder_files[removed_folder]
            self.folder_listbox.delete(folder_index)
                
            if self.folder_files:
                new_selected = list(self.folder_files.keys())[0]
                self.refresh_video_list(new_selected)
                folder_keys = list(self.folder_files.keys())
                self.folder_listbox.selection_set(folder_keys.index(new_selected))
            else:
                self.video_listbox.delete(0, tk.END)
                self.video_paths.clear()
            
            self.update_stats()
    
    def clear_list(self):
        self.folder_files.clear()
        self.video_paths.clear()
        self.folder_listbox.delete(0, tk.END)
        self.video_listbox.delete(0, tk.END)
        self.update_stats()
    
    def toggle_file_panel(self):
        self.file_panel_expanded = not self.file_panel_expanded
        if self.file_panel_expanded:
            self.file_panel_btn.config(text="▼ 文件列表")
            self.paned_window.pack(fill=tk.BOTH, expand=True)
            self.file_panel_frame.pack(fill=tk.BOTH, expand=True, pady=1)
        else:
            self.file_panel_btn.config(text="▶ 文件列表")
            self.paned_window.pack_forget()
            self.file_panel_frame.pack(fill=tk.X, expand=False, pady=1)
    
    def refresh_video_list(self, folder_key):
        self.video_listbox.delete(0, tk.END)
        self.video_paths = self.folder_files.get(folder_key, []).copy()
        for filepath in self.video_paths:
            self.video_listbox.insert(tk.END, os.path.basename(filepath))
    
    def on_folder_selected(self, event):
        selected_indices = self.folder_listbox.curselection()
        if not selected_indices:
            return
        
        folder_index = selected_indices[0]
        folder_keys = list(self.folder_files.keys())
        
        if folder_index < len(folder_keys):
            folder = folder_keys[folder_index]
            self.refresh_video_list(folder)
    
    def on_folder_double_click(self, event):
        selected_indices = self.folder_listbox.curselection()
        if not selected_indices:
            return
        
        folder_index = selected_indices[0]
        folder_keys = list(self.folder_files.keys())
        
        if folder_index < len(folder_keys):
            folder = folder_keys[folder_index]
            if folder != self.MANUAL_FILES_KEY:
                try:
                    if sys.platform == 'win32':
                        os.startfile(folder)
                    else:
                        subprocess.run(['open', folder], check=True)
                except Exception as e:
                    messagebox.showerror("错误", f"无法打开文件夹: {str(e)}")
    
    def on_video_double_click(self, event):
        selected_indices = self.video_listbox.curselection()
        if not selected_indices:
            return
        
        folder_keys = list(self.folder_files.keys())
        if not folder_keys:
            return
        current_folder = folder_keys[self.folder_listbox.curselection()[0]] if self.folder_listbox.curselection() else folder_keys[0]
        video_paths = self.folder_files.get(current_folder, [])
        
        for index in selected_indices:
            if index < len(video_paths):
                video_path = video_paths[index]
                try:
                    folder = os.path.dirname(video_path)
                    if sys.platform == 'win32':
                        os.startfile(folder)
                    else:
                        subprocess.run(['open', folder], check=True)
                    break
                except Exception as e:
                    messagebox.showerror("错误", f"无法打开文件夹: {str(e)}")
                    break
    
    def update_stats(self):
        total_files = 0
        for files in self.folder_files.values():
            total_files += len(files)
        
        self.stats['total'] = total_files
        self.stats_label.config(text=
            f"共 {self.stats['total']} | 已完成 {self.stats['completed']} | 成功 {self.stats['success']} | 失败 {self.stats['failed']}")
    
    def log(self, message, end='\n'):
        timestamp = get_timestamp()
        if end == '\n':
            self.log_queue.put(f"{timestamp} {message}\n")
        else:
            self.log_queue.put(f"{timestamp} {message}")
    
    def poll_log_queue(self):
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, message)
            self.log_text.config(state=tk.DISABLED)
            self.log_text.see(tk.END)
        self.root.after(100, self.poll_log_queue)
    
    def poll_scan_queue(self):
        while not self.scan_queue.empty():
            result = self.scan_queue.get()
            if result[0] == 'done':
                _, folder, video_files = result
                self.folder_files[folder] = video_files
                self.folder_listbox.insert(tk.END, folder)
                
                self.folder_listbox.selection_clear(0, tk.END)
                folder_keys = list(self.folder_files.keys())
                self.folder_listbox.selection_set(folder_keys.index(folder))
                
                self.refresh_video_list(folder)
                self.update_stats()
                
                self.log(f"  ✓ 扫描完成，找到 {len(video_files)} 个视频文件")
            elif result[0] == 'drop_done':
                _, folder, video_files = result
                self.folder_files[folder] = video_files
                
                folder_keys = list(self.folder_files.keys())
                if folder in folder_keys:
                    self.folder_listbox.selection_clear(0, tk.END)
                    self.folder_listbox.selection_set(folder_keys.index(folder))
                    self.refresh_video_list(folder)
                
                self.update_stats()
                self.log(f"  ✓ 扫描完成 {folder}，找到 {len(video_files)} 个视频文件")
            elif result[0] == 'error':
                _, error_msg = result
                self.log(f"  ❌ 扫描失败: {error_msg}")
                messagebox.showerror("错误", f"扫描文件夹失败: {error_msg}")
            elif result[0] == 'drop_error':
                _, folder, error_msg = result
                self.log(f"  ❌ 扫描失败 {folder}: {error_msg}")
                messagebox.showerror("错误", f"扫描文件夹失败: {error_msg}")
        
        self.scan_threads = [t for t in self.scan_threads if t.is_alive()]
        
        if self.scan_threads or not self.scan_queue.empty():
            self.root.after(100, self.poll_scan_queue)
    
    def start_processing(self):
        all_files = []
        for files in self.folder_files.values():
            all_files.extend(files)
        
        if not all_files:
            messagebox.showwarning("提示", "请先添加视频文件或文件夹！")
            return
        
        if self.processing_thread and self.processing_thread.is_alive():
            messagebox.showwarning("提示", "正在处理中，请等待完成！")
            return
        
        output_base_dir = None
        
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        self.log(f"已选择模式：{MODE_INFO[self.current_mode]['name']}")
        self.log(f"待处理文件数量：{len(all_files)}")
        self.log("-" * 60)
        
        with self.stats_lock:
            self.stats = {
                'total': len(all_files),
                'completed': 0,
                'success': 0,
                'failed': 0
            }
        self.update_stats()
        
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.stop_event.clear()
        
        self.processing_thread = threading.Thread(target=self.process_files, args=(all_files, output_base_dir))
        self.processing_thread.start()
    
    def stop_processing(self):
        self.stop_event.set()
        self.log("正在停止处理...")
    
    def process_files(self, all_files, output_base_dir):
        for i, filepath in enumerate(all_files, 1):
            if self.stop_event.is_set():
                self.log("处理已停止")
                break
            
            self.log(f"[{i}/{len(all_files)}] {os.path.basename(filepath)}")
            
            try:
                success, message = self.process_single_file(filepath, output_base_dir)
                
                with self.stats_lock:
                    self.stats['completed'] += 1
                    if success:
                        self.stats['success'] += 1
                    else:
                        self.stats['failed'] += 1
                
                self.log(f"  结果: {message}")
                
                self.root.after(0, self.update_stats)
                
            except Exception as e:
                self.log(f"  ❌ 处理异常: {str(e)}")
                with self.stats_lock:
                    self.stats['completed'] += 1
                    self.stats['failed'] += 1
                self.root.after(0, self.update_stats)
        
        self.log("-" * 60)
        with self.stats_lock:
            self.log(f"处理完成！总计: {self.stats['completed']}/{self.stats['total']}, 成功: {self.stats['success']}, 失败: {self.stats['failed']}")
        
        self.root.after(0, self.on_processing_finished)
    
    def update_progress(self, message):
        BAR_WIDTH = 50
        if message == "完成":
            bar = '█' * BAR_WIDTH
            text = f"{bar} 100.0%"
        elif '%' in message:
            try:
                percent = float(message.replace('%', '').strip())
                filled = int(BAR_WIDTH * percent / 100)
                bar = '█' * filled + '░' * (BAR_WIDTH - filled)
                text = f"{bar} {percent:.1f}%"
            except Exception:
                text = f"{'░' * BAR_WIDTH} {message}"
        else:
            text = f"{'░' * BAR_WIDTH} {message}"
        self.root.after(0, lambda: self.progress_label.config(text=text))
    
    def clear_progress(self):
        BAR_WIDTH = 50
        self.root.after(0, lambda: self.progress_label.config(text=f"{'░' * BAR_WIDTH} 0.0%"))
    
    def process_single_file(self, filepath, output_base_dir=None):
        mode = self.current_mode
        log_callback = self.log
        progress_callback = self.update_progress
        auto_mode = self.auto_mode.get()
        custom_crf = self.custom_crf.get()

        gpu_mode_map = {0: 'off', 1: 'on', 2: 'auto'}
        gpu_mode = gpu_mode_map.get(self.gpu_combo.current(), 'off')

        # 同步帧率限制开关到全局配置（compress_video / reencode_video 读取）
        import config as _config
        _config.ENABLE_FPS_LIMIT = self.fps_limit.get()

        if mode == 0:
            return process_mode_0(filepath, log_callback)

        # 未知编码直接 Fail（ffprobe 无法识别编码类型 → 移入 Fail）
        from utils import get_video_info
        try:
            pre_check_info = get_video_info(str(filepath))
            if pre_check_info and pre_check_info.get('codec', '') == 'unknown':
                output_dir_name = MODE_INFO[mode]["output_dir"]
                if output_base_dir:
                    parent_dir = output_base_dir
                else:
                    parent_dir = os.path.dirname(filepath)
                failed_dir = Path(parent_dir) / (output_dir_name + "_失败")
                log_callback(f"  ❌ 未知编码（ffprobe 无法识别），移入 Fail 目录")
                from utils import move_file
                move_path = failed_dir / os.path.basename(filepath)
                move_file(filepath, move_path)
                return False, "未知编码"
        except Exception:
            pass

        output_dir_name = MODE_INFO[mode]["output_dir"]
        if output_base_dir:
            parent_dir = output_base_dir
        else:
            parent_dir = os.path.dirname(filepath)
        
        success_dir = Path(parent_dir) / (output_dir_name + "_成功")
        failed_dir = Path(parent_dir) / (output_dir_name + "_失败")
        
        filename = os.path.basename(filepath)
        name_without_ext = filename.rsplit('.', 1)[0]
        
        if success_dir.exists():
            if mode == 5:
                # 模式5：可能直接压缩(_processed.mp4) 或 转交模式6(.mp4/.mkv)
                check_names = [name_without_ext + '_processed.mp4',
                               name_without_ext + '.mp4',
                               name_without_ext + '.mkv', filename]
            elif mode == 7:
                # 模式7：强制 MP4
                check_names = [name_without_ext + '.mp4', filename]
            elif mode == 6:
                # 模式6：目标容器按编码选择 (.mp4 或 .mkv)
                check_names = [name_without_ext + '.mp4',
                               name_without_ext + '.mkv', filename]
            elif mode == 3:
                file_ext = os.path.splitext(filename)[1].lower()
                is_legacy = file_ext in LEGACY_EXTENSIONS
                if is_legacy:
                    check_names = [name_without_ext + '.mp4',
                                   name_without_ext + '.mkv', filename]
                else:
                    check_names = [name_without_ext + '_processed.mp4', filename]
            elif mode in (1, 2, 8):
                # 模式1/2/8：多数输出 _processed.mp4，
                # 但模式2 ≤360p老式编码会转交模式6（产物 .mp4/.mkv），需补全
                check_names = [name_without_ext + '_processed.mp4',
                               name_without_ext + '.mp4',
                               name_without_ext + '.mkv', filename]
            elif mode == 4:
                # 模式4：≥100MB走模式1，转交模式6产物可能 .mp4/.mkv；<100MB放行→原文件名
                check_names = [name_without_ext + '_processed.mp4',
                               name_without_ext + '.mp4',
                               name_without_ext + '.mkv', filename]
            else:
                check_names = [name_without_ext + '_processed.mp4', filename]
            
            for check_name in check_names:
                if (success_dir / check_name).exists():
                    log_callback(f"  [跳过] {filename} 已存在于压缩成功文件夹（{check_name}），源文件保持不动")
                    return True, "跳过"
        
        if mode == 1:
            return process_mode_1_2(filepath, success_dir, failed_dir, mode=1, log_callback=log_callback, progress_callback=progress_callback,
                                     auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode)
        elif mode == 2:
            return process_mode_1_2(filepath, success_dir, failed_dir, mode=2, log_callback=log_callback, progress_callback=progress_callback,
                                     auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode)
        elif mode == 3:
            return process_mode_3(filepath, success_dir, failed_dir, log_callback=log_callback, progress_callback=progress_callback,
                                   auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode)
        elif mode == 4:
            return process_mode_4(filepath, success_dir, failed_dir, log_callback=log_callback, progress_callback=progress_callback,
                                   auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode)
        elif mode == 5:
            return process_mode_5(filepath, success_dir, failed_dir, log_callback=log_callback, progress_callback=progress_callback,
                                   auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode,
                                   compress_4k=self.compress_4k.get())
        elif mode == 6:
            return process_mode_6(filepath, success_dir, failed_dir, log_callback=log_callback, progress_callback=progress_callback,
                                   auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode)
        elif mode == 7:
            return process_mode_7(filepath, success_dir, failed_dir, log_callback=log_callback, progress_callback=progress_callback,
                                   auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode)
        elif mode == 8:
            return process_mode_1_2(filepath, success_dir, failed_dir, mode=8, log_callback=log_callback, progress_callback=progress_callback,
                                     auto_mode=auto_mode, custom_crf=custom_crf, gpu_mode=gpu_mode)
        else:
            return False, "未知模式"
    
    def on_processing_finished(self):
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.update_stats()
        self.clear_progress()