import os
import shutil
import socket
import threading
from datetime import datetime
from ftplib import FTP, FTP_TLS, all_errors
import ssl
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


class PatchedFTP_TLS(FTP_TLS):
    """
    FTP_TLS с фиксами:
    1) Обход рассинхрона ответов при PASV — вызываем FTP.ntransfercmd напрямую.
    2) TLS session resumption — FileZilla Server (и другие) требуют,
       чтобы data-канал переиспользовал сессию контрольного соединения.
    """

    def ntransfercmd(self, cmd, rest=None):
        conn, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            session = self.sock.session
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=session,
            )
        return conn, size


class Theme:
    """Палитра тёмной темы и шрифты."""

    BG = "#1e1e2e"
    BG_SECONDARY = "#252538"
    BG_INPUT = "#2a2a3d"
    BG_LIST = "#20203a"
    BG_LOG = "#181828"
    FG = "#cdd6f4"
    FG_DIM = "#6c7086"
    FG_PATH = "#89b4fa"
    ACCENT = "#cba6f7"
    ACCENT_HOVER = "#b4befe"
    GREEN = "#a6e3a1"
    RED = "#f38ba8"
    YELLOW = "#f9e2af"
    BORDER = "#313244"
    SELECT_BG = "#45475a"
    SELECT_FG = "#cdd6f4"

    FONT = ("Segoe UI", 10)
    FONT_BOLD = ("Segoe UI", 10, "bold")
    FONT_MONO = ("Cascadia Code", 10)
    FONT_MONO_SMALL = ("Cascadia Code", 9)
    FONT_TITLE = ("Segoe UI", 11, "bold")
    FONT_BTN = ("Segoe UI", 9)


class FTPClientApp:
    """Графический FTP-клиент на Tkinter с тёмной темой."""

    DEFAULT_PORT = 21
    CONNECT_TIMEOUT = 20
    BUFFER_SIZE = 8192

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FTP Client")
        self.root.geometry("1250x780")
        self.root.minsize(1000, 680)
        self.root.configure(bg=Theme.BG)

        self.ftp = None
        self.connected = False
        self.ftp_lock = threading.Lock()

        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value=str(self.DEFAULT_PORT))
        self.user_var = tk.StringVar(value="anonymous")
        self.pass_var = tk.StringVar()
        self.tls_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Отключено")

        self.local_current_dir = os.path.expanduser("~")
        self.remote_current_dir = "/"

        self.local_entries = []
        self.remote_entries = []
        self._transfer_active = False

        self._setup_styles()
        self._build_ui()
        self.refresh_local_list()
        self._update_button_states()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ========== Стили и тема ==========

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=Theme.BG, foreground=Theme.FG, font=Theme.FONT)

        style.configure("TFrame", background=Theme.BG)
        style.configure("Secondary.TFrame", background=Theme.BG_SECONDARY)

        style.configure("TLabel", background=Theme.BG, foreground=Theme.FG, font=Theme.FONT)
        style.configure("Dim.TLabel", foreground=Theme.FG_DIM, font=Theme.FONT)
        style.configure("Path.TLabel", foreground=Theme.FG_PATH, font=Theme.FONT_MONO)
        style.configure("Status.TLabel", foreground=Theme.FG_DIM, font=Theme.FONT)
        style.configure("StatusOk.TLabel", foreground=Theme.GREEN, font=Theme.FONT_BOLD)
        style.configure("StatusErr.TLabel", foreground=Theme.RED, font=Theme.FONT_BOLD)
        style.configure("Title.TLabel", foreground=Theme.ACCENT, font=Theme.FONT_TITLE)
        style.configure("Progress.TLabel", foreground=Theme.YELLOW, font=Theme.FONT_MONO_SMALL)

        style.configure("TButton", background=Theme.BG_SECONDARY, foreground=Theme.FG,
                         font=Theme.FONT_BTN, borderwidth=1, relief="flat", padding=(10, 4))
        style.map("TButton",
                  background=[("active", Theme.SELECT_BG), ("disabled", Theme.BG)],
                  foreground=[("disabled", Theme.FG_DIM)])

        style.configure("Accent.TButton", background=Theme.ACCENT, foreground="#11111b",
                         font=Theme.FONT_BTN, padding=(12, 5))
        style.map("Accent.TButton",
                  background=[("active", Theme.ACCENT_HOVER), ("disabled", Theme.BG_SECONDARY)],
                  foreground=[("disabled", Theme.FG_DIM)])

        style.configure("Danger.TButton", background="#45273a", foreground=Theme.RED,
                         font=Theme.FONT_BTN, padding=(10, 4))
        style.map("Danger.TButton",
                  background=[("active", "#5a3048")])

        style.configure("Transfer.TButton", background="#2a3a4a", foreground="#89dceb",
                         font=("Segoe UI", 10, "bold"), padding=(14, 8))
        style.map("Transfer.TButton",
                  background=[("active", "#354a5a")])

        style.configure("TEntry", fieldbackground=Theme.BG_INPUT, foreground=Theme.FG,
                         insertcolor=Theme.FG, borderwidth=1, relief="solid", padding=4)

        style.configure("TLabelframe", background=Theme.BG, foreground=Theme.ACCENT,
                         font=Theme.FONT_BOLD, borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=Theme.BG, foreground=Theme.ACCENT,
                         font=Theme.FONT_BOLD)

        style.configure("Horizontal.TProgressbar",
                         background=Theme.ACCENT, troughcolor=Theme.BG_SECONDARY,
                         borderwidth=0, thickness=6)

        style.configure("TSeparator", background=Theme.BORDER)

        style.configure("TCheckbutton", background=Theme.BG_SECONDARY, foreground=Theme.FG, font=Theme.FONT_BTN)
        style.map("TCheckbutton",
                  background=[("active", Theme.BG_SECONDARY)],
                  foreground=[("disabled", Theme.FG_DIM)])

    # ========== UI ==========

    def _build_ui(self):
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_connection_panel()
        self._build_file_manager_panel()
        self._build_progress_panel()
        self._build_log_panel()

    def _build_header(self):
        frame = ttk.Frame(self.root)
        frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))

        ttk.Label(frame, text="FTP Client", style="Title.TLabel").pack(side="left")

        self.status_indicator = tk.Canvas(frame, width=12, height=12,
                                           bg=Theme.BG, highlightthickness=0)
        self.status_indicator.pack(side="right", padx=(0, 6))
        self._draw_status_dot(Theme.RED)

        self.status_label = ttk.Label(frame, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(side="right", padx=(0, 4))

    def _draw_status_dot(self, color: str):
        self.status_indicator.delete("all")
        self.status_indicator.create_oval(2, 2, 10, 10, fill=color, outline=color)

    def _build_connection_panel(self):
        outer = ttk.Frame(self.root, style="Secondary.TFrame")
        outer.grid(row=1, column=0, sticky="ew", padx=12, pady=(8, 4))

        frame = ttk.Frame(outer, style="Secondary.TFrame")
        frame.pack(fill="x", padx=10, pady=8)

        fields = [
            ("Хост", self.host_var, 22),
            ("Порт", self.port_var, 6),
            ("Логин", self.user_var, 14),
        ]

        col = 0
        for label_text, var, width in fields:
            ttk.Label(frame, text=label_text, style="Dim.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 4))
            col += 1
            entry = ttk.Entry(frame, textvariable=var, width=width)
            entry.grid(row=0, column=col, padx=(0, 12))
            col += 1

        ttk.Label(frame, text="Пароль", style="Dim.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 4))
        col += 1
        pass_entry = ttk.Entry(frame, textvariable=self.pass_var, show="\u2022", width=14)
        pass_entry.grid(row=0, column=col, padx=(0, 16))
        col += 1

        tls_check = ttk.Checkbutton(frame, text="TLS", variable=self.tls_var, style="TCheckbutton")
        tls_check.grid(row=0, column=col, padx=(0, 12))
        col += 1

        self.connect_btn = ttk.Button(frame, text="Подключиться", style="Accent.TButton",
                                       command=self.connect_to_server)
        self.connect_btn.grid(row=0, column=col, padx=(0, 4))
        col += 1

        self.disconnect_btn = ttk.Button(frame, text="Отключиться", command=self.disconnect_from_server,
                                          state="disabled")
        self.disconnect_btn.grid(row=0, column=col)

    def _build_file_manager_panel(self):
        main_frame = ttk.Frame(self.root)
        main_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(2, weight=1)

        self._build_local_panel(main_frame)
        self._build_action_buttons(main_frame)
        self._build_remote_panel(main_frame)

    def _make_styled_listbox(self, parent):
        listbox = tk.Listbox(
            parent,
            bg=Theme.BG_LIST,
            fg=Theme.FG,
            selectbackground=Theme.SELECT_BG,
            selectforeground=Theme.SELECT_FG,
            font=Theme.FONT_MONO,
            borderwidth=0,
            highlightthickness=1,
            highlightcolor=Theme.BORDER,
            highlightbackground=Theme.BORDER,
            activestyle="none",
            selectmode="browse",
        )
        return listbox

    def _make_context_menu(self, items):
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=Theme.BG_SECONDARY,
            fg=Theme.FG,
            activebackground=Theme.SELECT_BG,
            activeforeground=Theme.FG,
            font=Theme.FONT,
            borderwidth=1,
            relief="solid",
        )
        for label, command in items:
            if label == "---":
                menu.add_separator()
            else:
                menu.add_command(label=label, command=command)
        return menu

    def _build_local_panel(self, parent: tk.Widget):
        frame = ttk.LabelFrame(parent, text="  Локальные файлы  ", padding=8)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        nav = ttk.Frame(frame)
        nav.grid(row=0, column=0, sticky="ew")
        nav.grid_columnconfigure(1, weight=1)

        ttk.Button(nav, text="\u2191", width=3, command=self.go_local_up).grid(row=0, column=0, sticky="w")
        self.local_path_var = tk.StringVar(value=self.local_current_dir)
        ttk.Label(nav, textvariable=self.local_path_var, style="Path.TLabel").grid(
            row=0, column=1, sticky="ew", padx=(8, 0))

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        self.local_listbox = self._make_styled_listbox(list_frame)
        self.local_listbox.grid(row=0, column=0, sticky="nsew")
        self.local_listbox.bind("<Double-Button-1>", self.on_local_double_click)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.local_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.local_listbox.config(yscrollcommand=scrollbar.set)

        self.local_ctx_menu = self._make_context_menu([
            ("Переименовать", self.rename_local_item),
            ("Удалить", self.delete_local_item),
            ("---", None),
            ("Создать папку", self.create_local_directory),
        ])
        self.local_listbox.bind("<Button-3>", self._show_local_context_menu)

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for i in range(3):
            actions.grid_columnconfigure(i, weight=1)

        ttk.Button(actions, text="Удалить", style="Danger.TButton",
                    command=self.delete_local_item).grid(row=0, column=0, padx=(0, 2), sticky="ew")
        ttk.Button(actions, text="Переименовать",
                    command=self.rename_local_item).grid(row=0, column=1, padx=2, sticky="ew")
        ttk.Button(actions, text="Создать папку",
                    command=self.create_local_directory).grid(row=0, column=2, padx=(2, 0), sticky="ew")

    def _build_action_buttons(self, parent: tk.Widget):
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=1, sticky="ns", padx=6)

        spacer = ttk.Frame(frame)
        spacer.pack(expand=True)

        self.upload_btn = ttk.Button(frame, text="Загрузить  \u25B6", style="Transfer.TButton",
                                      command=self.upload_selected_file)
        self.upload_btn.pack(pady=(0, 6))

        self.download_btn = ttk.Button(frame, text="\u25C0  Скачать", style="Transfer.TButton",
                                        command=self.download_selected_file)
        self.download_btn.pack(pady=(6, 0))

        spacer2 = ttk.Frame(frame)
        spacer2.pack(expand=True)

    def _build_remote_panel(self, parent: tk.Widget):
        frame = ttk.LabelFrame(parent, text="  Файлы на сервере  ", padding=8)
        frame.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        nav = ttk.Frame(frame)
        nav.grid(row=0, column=0, sticky="ew")
        nav.grid_columnconfigure(1, weight=1)

        ttk.Button(nav, text="\u2191", width=3, command=self.go_remote_up).grid(row=0, column=0, sticky="w")
        self.remote_path_var = tk.StringVar(value=self.remote_current_dir)
        ttk.Label(nav, textvariable=self.remote_path_var, style="Path.TLabel").grid(
            row=0, column=1, sticky="ew", padx=(8, 0))

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        self.remote_listbox = self._make_styled_listbox(list_frame)
        self.remote_listbox.grid(row=0, column=0, sticky="nsew")
        self.remote_listbox.bind("<Double-Button-1>", self.on_remote_double_click)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.remote_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.remote_listbox.config(yscrollcommand=scrollbar.set)

        self.remote_ctx_menu = self._make_context_menu([
            ("Переименовать", self.rename_remote_item),
            ("Удалить", self.delete_remote_item),
            ("---", None),
            ("Создать папку", self.create_remote_directory),
        ])
        self.remote_listbox.bind("<Button-3>", self._show_remote_context_menu)

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for i in range(3):
            actions.grid_columnconfigure(i, weight=1)

        ttk.Button(actions, text="Удалить", style="Danger.TButton",
                    command=self.delete_remote_item).grid(row=0, column=0, padx=(0, 2), sticky="ew")
        ttk.Button(actions, text="Переименовать",
                    command=self.rename_remote_item).grid(row=0, column=1, padx=2, sticky="ew")
        ttk.Button(actions, text="Создать папку",
                    command=self.create_remote_directory).grid(row=0, column=2, padx=(2, 0), sticky="ew")

    def _build_progress_panel(self):
        frame = ttk.Frame(self.root)
        frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 0))
        frame.grid_columnconfigure(0, weight=1)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100,
                                              style="Horizontal.TProgressbar")
        self.progress_bar.grid(row=0, column=0, sticky="ew")

        self.progress_label = ttk.Label(frame, text="", style="Progress.TLabel")
        self.progress_label.grid(row=0, column=1, padx=(8, 0))

    def _build_log_panel(self):
        frame = ttk.LabelFrame(self.root, text="  Лог  ", padding=8)
        frame.grid(row=4, column=0, sticky="nsew", padx=12, pady=(4, 10))
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.log_widget = tk.Text(
            frame, height=7, state="disabled", wrap="word",
            bg=Theme.BG_LOG, fg=Theme.FG_DIM,
            font=Theme.FONT_MONO_SMALL,
            borderwidth=0,
            highlightthickness=0,
            insertbackground=Theme.FG,
            selectbackground=Theme.SELECT_BG,
            selectforeground=Theme.FG,
        )
        self.log_widget.grid(row=0, column=0, sticky="nsew")

        log_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_widget.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_widget.config(yscrollcommand=log_scroll.set)

        self.log_widget.tag_configure("timestamp", foreground=Theme.FG_DIM)
        self.log_widget.tag_configure("error", foreground=Theme.RED)
        self.log_widget.tag_configure("success", foreground=Theme.GREEN)
        self.log_widget.tag_configure("info", foreground=Theme.FG)

    # ========== Контекстные меню ==========

    def _show_local_context_menu(self, event):
        idx = self.local_listbox.nearest(event.y)
        if idx >= 0:
            self.local_listbox.selection_clear(0, "end")
            self.local_listbox.selection_set(idx)
        try:
            self.local_ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.local_ctx_menu.grab_release()

    def _show_remote_context_menu(self, event):
        idx = self.remote_listbox.nearest(event.y)
        if idx >= 0:
            self.remote_listbox.selection_clear(0, "end")
            self.remote_listbox.selection_set(idx)
        try:
            self.remote_ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.remote_ctx_menu.grab_release()

    # ========== Утилиты UI ==========

    def _update_button_states(self):
        state_on = "normal" if self.connected else "disabled"
        state_off = "disabled" if self.connected else "normal"

        self.connect_btn.config(state=state_off)
        self.disconnect_btn.config(state=state_on)

        if self.connected:
            self._draw_status_dot(Theme.GREEN)
            self.status_label.config(style="StatusOk.TLabel")
        else:
            self._draw_status_dot(Theme.RED)
            self.status_label.config(style="Status.TLabel")

    def set_status(self, text: str):
        self.root.after(0, lambda: self.status_var.set(text))

    def log(self, text: str, level: str = "info"):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, lambda: self._append_log(stamp, text, level))

    def _append_log(self, stamp: str, text: str, level: str):
        self.log_widget.config(state="normal")
        self.log_widget.insert("end", f"[{stamp}] ", "timestamp")
        self.log_widget.insert("end", f"{text}\n", level)
        self.log_widget.see("end")
        self.log_widget.config(state="disabled")

    def _run_in_thread(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    def _is_connected(self) -> bool:
        if not self.connected or self.ftp is None:
            messagebox.showwarning("Нет соединения", "Сначала подключитесь к FTP-серверу.")
            return False
        return True

    def _parse_port(self) -> int:
        raw = self.port_var.get().strip()
        if not raw:
            return self.DEFAULT_PORT
        try:
            port = int(raw)
            if not (1 <= port <= 65535):
                raise ValueError
            return port
        except ValueError:
            messagebox.showwarning("Некорректный порт", "Порт должен быть числом от 1 до 65535.")
            return -1

    def _set_progress(self, percent: float, label: str = ""):
        self.root.after(0, lambda: (self.progress_var.set(percent), self.progress_label.config(text=label)))

    def _reset_progress(self):
        self._set_progress(0, "")

    # ========== Подключение / отключение ==========

    def connect_to_server(self):
        host = self.host_var.get().strip()
        user = self.user_var.get().strip() or "anonymous"
        password = self.pass_var.get()
        port = self._parse_port()

        if port < 0:
            return
        if not host:
            messagebox.showwarning("Некорректный ввод", "Введите адрес хоста.")
            return
        if self.connected:
            messagebox.showinfo("Уже подключено", "Подключение уже установлено.")
            return

        use_tls = self.tls_var.get()
        self.set_status("Подключение...")
        self.log(f"Подключение к {host}:{port} ({'TLS' if use_tls else 'plain'})...")
        self._run_in_thread(self._connect_worker, host, port, user, password, use_tls)

    def _connect_worker(self, host: str, port: int, user: str, password: str, use_tls: bool):
        ftp = None
        try:
            if use_tls:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ftp = PatchedFTP_TLS(context=ctx)
                ftp.connect(host=host, port=port, timeout=self.CONNECT_TIMEOUT)
                ftp.auth()
                ftp.login(user=user, passwd=password)
                ftp.prot_p()
            else:
                ftp = FTP()
                ftp.connect(host=host, port=port, timeout=self.CONNECT_TIMEOUT)
                ftp.login(user=user, passwd=password)
            welcome = ftp.getwelcome()
            current_dir = ftp.pwd()

            with self.ftp_lock:
                self.ftp = ftp
                self.connected = True
                self.remote_current_dir = current_dir

            self.set_status(f"Подключено к {host}:{port}")
            self.log(welcome, "success")
            self.log(f"Текущая папка: {current_dir}", "success")
            self.root.after(0, lambda: (
                self.remote_path_var.set(self.remote_current_dir),
                self._update_button_states()
            ))
            self.refresh_remote_list()
        except all_errors as err:
            if ftp is not None:
                try:
                    ftp.close()
                except all_errors:
                    pass
            err_text = str(err)
            self.set_status(f"Ошибка: {err_text}")
            self.log(f"Ошибка подключения: {err_text}", "error")
            self.root.after(0, lambda msg=err_text: messagebox.showerror("Ошибка подключения", msg))

    def disconnect_from_server(self):
        if not self.connected:
            return
        self._run_in_thread(self._disconnect_worker)

    def _disconnect_worker(self):
        try:
            with self.ftp_lock:
                if self.ftp and self.connected:
                    self.ftp.quit()
        except all_errors:
            try:
                if self.ftp:
                    self.ftp.close()
            except all_errors:
                pass
        finally:
            with self.ftp_lock:
                self.connected = False
                self.ftp = None
            self.remote_entries = []
            self.remote_current_dir = "/"

        self.set_status("Отключено")
        self.log("Соединение разорвано.")
        self.root.after(0, lambda: (
            self.remote_listbox.delete(0, "end"),
            self.remote_path_var.set("/"),
            self._update_button_states()
        ))

    # ========== Локальная панель ==========

    def refresh_local_list(self):
        try:
            entries = []
            with os.scandir(self.local_current_dir) as scan:
                for item in scan:
                    try:
                        entries.append((item.name, item.is_dir()))
                    except OSError:
                        continue

            entries.sort(key=lambda x: (not x[1], x[0].lower()))
            self.local_entries = entries

            self.local_listbox.delete(0, "end")
            for name, is_dir in entries:
                prefix = "\U0001F4C1  " if is_dir else "     "
                self.local_listbox.insert("end", f"{prefix}{name}")

            self.local_path_var.set(self.local_current_dir)
        except OSError as err:
            self.log(f"Ошибка чтения локальной папки: {err}", "error")
            messagebox.showerror("Ошибка", f"Не удалось открыть папку:\n{err}")

    def go_local_up(self):
        parent = os.path.dirname(self.local_current_dir)
        if parent and parent != self.local_current_dir:
            self.local_current_dir = parent
            self.refresh_local_list()

    def on_local_double_click(self, _event):
        index = self._selected_index(self.local_listbox)
        if index is None:
            return
        name, is_dir = self.local_entries[index]
        if is_dir:
            self.local_current_dir = os.path.join(self.local_current_dir, name)
            self.refresh_local_list()

    def delete_local_item(self):
        index = self._selected_index(self.local_listbox)
        if index is None:
            messagebox.showwarning("Нет выбора", "Выберите файл или папку.")
            return

        name, is_dir = self.local_entries[index]
        target = os.path.join(self.local_current_dir, name)

        if not messagebox.askyesno("Подтверждение", f"Удалить '{name}'?"):
            return

        try:
            if is_dir:
                shutil.rmtree(target)
            else:
                os.remove(target)
            self.log(f"Удалено: {target}", "success")
            self.refresh_local_list()
        except OSError as err:
            self.log(f"Ошибка удаления: {err}", "error")
            messagebox.showerror("Ошибка", f"Не удалось удалить '{name}':\n{err}")

    def rename_local_item(self):
        index = self._selected_index(self.local_listbox)
        if index is None:
            messagebox.showwarning("Нет выбора", "Выберите файл или папку для переименования.")
            return

        old_name, _ = self.local_entries[index]
        new_name = simpledialog.askstring("Переименовать", f"Новое имя для '{old_name}':", initialvalue=old_name)
        if not new_name or not new_name.strip() or new_name.strip() == old_name:
            return

        old_path = os.path.join(self.local_current_dir, old_name)
        new_path = os.path.join(self.local_current_dir, new_name.strip())

        try:
            os.rename(old_path, new_path)
            self.log(f"Переименовано: {old_name} → {new_name.strip()}", "success")
            self.refresh_local_list()
        except OSError as err:
            self.log(f"Ошибка переименования: {err}", "error")
            messagebox.showerror("Ошибка", str(err))

    def create_local_directory(self):
        folder_name = simpledialog.askstring("Создать папку", "Имя новой локальной папки:")
        if not folder_name or not folder_name.strip():
            return

        path = os.path.join(self.local_current_dir, folder_name.strip())
        try:
            os.mkdir(path)
            self.log(f"Создана папка: {path}", "success")
            self.refresh_local_list()
        except OSError as err:
            self.log(f"Ошибка создания папки: {err}", "error")
            messagebox.showerror("Ошибка", str(err))

    # ========== Удалённая панель ==========

    def refresh_remote_list(self):
        if not self.connected or self.ftp is None:
            return
        self._run_in_thread(self._refresh_remote_worker)

    def _refresh_remote_worker(self):
        try:
            lines = []
            with self.ftp_lock:
                if not self.connected or self.ftp is None:
                    return
                self.remote_current_dir = self.ftp.pwd()
                self.ftp.retrlines("LIST", lines.append)

            parsed = []
            for line in lines:
                name, is_dir = self._parse_list_line(line)
                if name in (".", ".."):
                    continue
                parsed.append((name, is_dir))

            parsed.sort(key=lambda x: (not x[1], x[0].lower()))
            self.remote_entries = parsed
            self.root.after(0, self._render_remote_entries)
        except all_errors as err:
            self.log(f"Ошибка чтения удалённой папки: {err}", "error")

    def _render_remote_entries(self):
        self.remote_listbox.delete(0, "end")
        for name, is_dir in self.remote_entries:
            prefix = "\U0001F4C1  " if is_dir else "     "
            self.remote_listbox.insert("end", f"{prefix}{name}")
        self.remote_path_var.set(self.remote_current_dir)

    @staticmethod
    def _parse_list_line(line: str):
        parts = line.split(maxsplit=8)
        name = parts[8] if len(parts) >= 9 else line
        is_dir = line.startswith("d")
        return name, is_dir

    def go_remote_up(self):
        if not self._is_connected():
            return
        self._run_in_thread(self._go_remote_up_worker)

    def _go_remote_up_worker(self):
        try:
            with self.ftp_lock:
                if not self.connected or self.ftp is None:
                    return
                self.ftp.cwd("..")
                self.remote_current_dir = self.ftp.pwd()
            self.log(f"Перешли в: {self.remote_current_dir}")
            self.root.after(0, lambda: self.remote_path_var.set(self.remote_current_dir))
            self.refresh_remote_list()
        except all_errors as err:
            self.log(f"Ошибка навигации: {err}", "error")

    def on_remote_double_click(self, _event):
        if not self._is_connected():
            return
        index = self._selected_index(self.remote_listbox)
        if index is None:
            return
        name, is_dir = self.remote_entries[index]
        if is_dir:
            self._run_in_thread(self._open_remote_dir_worker, name)

    def _open_remote_dir_worker(self, folder_name: str):
        try:
            with self.ftp_lock:
                if not self.connected or self.ftp is None:
                    return
                self.ftp.cwd(folder_name)
                self.remote_current_dir = self.ftp.pwd()
            self.log(f"Открыта папка: {self.remote_current_dir}")
            self.root.after(0, lambda: self.remote_path_var.set(self.remote_current_dir))
            self.refresh_remote_list()
        except all_errors as err:
            self.log(f"Ошибка открытия '{folder_name}': {err}", "error")

    def delete_remote_item(self):
        if not self._is_connected():
            return

        index = self._selected_index(self.remote_listbox)
        if index is None:
            messagebox.showwarning("Нет выбора", "Выберите файл или папку на сервере.")
            return

        name, is_dir = self.remote_entries[index]
        if not messagebox.askyesno("Подтверждение", f"Удалить '{name}' на сервере?"):
            return

        self._run_in_thread(self._delete_remote_item_worker, name, is_dir)

    def _delete_remote_item_worker(self, name: str, is_dir: bool):
        try:
            with self.ftp_lock:
                if not self.connected or self.ftp is None:
                    return
                if is_dir:
                    self._remove_remote_dir_recursive(name)
                else:
                    self.ftp.delete(name)
            self.log(f"Удалено на сервере: {name}", "success")
            self.refresh_remote_list()
        except all_errors as err:
            err_text = str(err)
            self.log(f"Ошибка удаления '{name}': {err_text}", "error")
            self.root.after(0, lambda msg=err_text: messagebox.showerror("Ошибка", msg))

    def _remove_remote_dir_recursive(self, path: str):
        """Рекурсивное удаление директории. Вызывать только внутри ftp_lock!"""
        original_dir = self.ftp.pwd()
        self.ftp.cwd(path)

        lines = []
        self.ftp.retrlines("LIST", lines.append)

        for line in lines:
            name, is_dir = self._parse_list_line(line)
            if name in (".", ".."):
                continue
            if is_dir:
                self._remove_remote_dir_recursive(name)
            else:
                self.ftp.delete(name)

        self.ftp.cwd(original_dir)
        self.ftp.rmd(path)

    def rename_remote_item(self):
        if not self._is_connected():
            return

        index = self._selected_index(self.remote_listbox)
        if index is None:
            messagebox.showwarning("Нет выбора", "Выберите файл или папку для переименования.")
            return

        old_name, _ = self.remote_entries[index]
        new_name = simpledialog.askstring("Переименовать", f"Новое имя для '{old_name}':", initialvalue=old_name)
        if not new_name or not new_name.strip() or new_name.strip() == old_name:
            return

        self._run_in_thread(self._rename_remote_worker, old_name, new_name.strip())

    def _rename_remote_worker(self, old_name: str, new_name: str):
        try:
            with self.ftp_lock:
                if not self.connected or self.ftp is None:
                    return
                self.ftp.rename(old_name, new_name)
            self.log(f"Переименовано: {old_name} → {new_name}", "success")
            self.refresh_remote_list()
        except all_errors as err:
            err_text = str(err)
            self.log(f"Ошибка переименования: {err_text}", "error")
            self.root.after(0, lambda msg=err_text: messagebox.showerror("Ошибка", msg))

    def create_remote_directory(self):
        if not self._is_connected():
            return

        folder_name = simpledialog.askstring("Создать папку", "Имя новой папки на сервере:")
        if not folder_name or not folder_name.strip():
            return

        self._run_in_thread(self._create_remote_dir_worker, folder_name.strip())

    def _create_remote_dir_worker(self, folder_name: str):
        try:
            with self.ftp_lock:
                if not self.connected or self.ftp is None:
                    return
                self.ftp.mkd(folder_name)
            self.log(f"Создана папка: {folder_name}", "success")
            self.refresh_remote_list()
        except all_errors as err:
            err_text = str(err)
            self.log(f"Ошибка создания папки: {err_text}", "error")
            self.root.after(0, lambda msg=err_text: messagebox.showerror("Ошибка", msg))

    # ========== Трансфер файлов ==========

    def upload_selected_file(self):
        if not self._is_connected():
            return
        index = self._selected_index(self.local_listbox)
        if index is None:
            messagebox.showwarning("Нет выбора", "Выберите файл для загрузки.")
            return

        name, is_dir = self.local_entries[index]
        if is_dir:
            messagebox.showwarning("Недоступно", "Загрузка папок не поддерживается.")
            return

        local_path = os.path.join(self.local_current_dir, name)
        self._run_in_thread(self._upload_worker, local_path, name)

    def _upload_worker(self, local_path: str, remote_name: str):
        if self._transfer_active:
            self.log("Дождитесь завершения текущей передачи.")
            return
        self._transfer_active = True

        try:
            file_size = os.path.getsize(local_path)
            sent = 0
            self.log(f"Загрузка '{remote_name}' ({self._format_size(file_size)})...")
            self._set_progress(0, f"Загрузка: {remote_name}")

            def callback(block):
                nonlocal sent
                sent += len(block)
                pct = (sent / file_size * 100) if file_size else 100
                self._set_progress(pct, f"{self._format_size(sent)} / {self._format_size(file_size)}")

            with open(local_path, "rb") as f:
                with self.ftp_lock:
                    if not self.connected or self.ftp is None:
                        return
                    self.ftp.storbinary(f"STOR {remote_name}", f, blocksize=self.BUFFER_SIZE, callback=callback)

            self._set_progress(100, "Готово")
            self.log(f"'{remote_name}' загружен", "success")
            self.refresh_remote_list()
        except all_errors as err:
            err_text = str(err)
            self.log(f"Ошибка загрузки '{remote_name}': {err_text}", "error")
            self.root.after(0, lambda msg=err_text: messagebox.showerror("Ошибка", msg))
        finally:
            self._transfer_active = False
            self.root.after(2000, self._reset_progress)

    def download_selected_file(self):
        if not self._is_connected():
            return

        index = self._selected_index(self.remote_listbox)
        if index is None:
            messagebox.showwarning("Нет выбора", "Выберите файл на сервере.")
            return

        name, is_dir = self.remote_entries[index]
        if is_dir:
            messagebox.showwarning("Недоступно", "Скачивание папок не поддерживается.")
            return

        local_path = os.path.join(self.local_current_dir, name)
        if os.path.exists(local_path):
            if not messagebox.askyesno("Файл существует", f"'{name}' уже существует. Перезаписать?"):
                return

        self._run_in_thread(self._download_worker, name, local_path)

    def _download_worker(self, remote_name: str, local_path: str):
        if self._transfer_active:
            self.log("Дождитесь завершения текущей передачи.")
            return
        self._transfer_active = True

        try:
            with self.ftp_lock:
                if not self.connected or self.ftp is None:
                    return
                file_size = self.ftp.size(remote_name)

            received = 0
            size_known = file_size is not None and file_size > 0
            label_total = self._format_size(file_size) if size_known else "?"
            self.log(f"Скачивание '{remote_name}' ({label_total})...")
            self._set_progress(0, f"Скачивание: {remote_name}")

            with open(local_path, "wb") as f:
                def callback(block):
                    nonlocal received
                    f.write(block)
                    received += len(block)
                    if size_known:
                        pct = received / file_size * 100
                        self._set_progress(pct, f"{self._format_size(received)} / {label_total}")
                    else:
                        self._set_progress(0, f"Скачано: {self._format_size(received)}")

                with self.ftp_lock:
                    if not self.connected or self.ftp is None:
                        return
                    self.ftp.retrbinary(f"RETR {remote_name}", callback, blocksize=self.BUFFER_SIZE)

            self._set_progress(100, "Готово")
            self.log(f"'{remote_name}' скачан", "success")
            self.root.after(0, self.refresh_local_list)
        except all_errors as err:
            err_text = str(err)
            self.log(f"Ошибка скачивания '{remote_name}': {err_text}", "error")
            self.root.after(0, lambda msg=err_text: messagebox.showerror("Ошибка", msg))
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass
        finally:
            self._transfer_active = False
            self.root.after(2000, self._reset_progress)

    # ========== Общие методы ==========

    @staticmethod
    def _selected_index(listbox: tk.Listbox):
        selection = listbox.curselection()
        return selection[0] if selection else None

    @staticmethod
    def _format_size(size) -> str:
        if size is None or size < 0:
            return "?"
        for unit in ("\u0411", "\u041a\u0411", "\u041c\u0411", "\u0413\u0411"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} \u0422\u0411"

    def on_close(self):
        if self.ftp is not None:
            try:
                with self.ftp_lock:
                    if self.connected:
                        self.ftp.quit()
            except all_errors:
                try:
                    self.ftp.close()
                except all_errors:
                    pass
            finally:
                self.connected = False
                self.ftp = None
        self.root.destroy()


def main():
    root = tk.Tk()
    FTPClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
