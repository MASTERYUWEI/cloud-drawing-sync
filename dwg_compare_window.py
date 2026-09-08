"""Read-only DWG revision preview with a shared, draggable comparison viewport.

The renderer supplies equally sized images on the same drawing coordinates.  This
module only reads those images; it does not open drawings for editing or persist
paths in the synchronizer's configuration.
"""

import math
import os
import queue
import tempfile
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image, ImageTk
from comparison_tiles import ComparisonTileCache, TileRenderCancelled
from pdf_viewport import max_view_scale


BG = "#F7F7F8"
SURFACE = "#FFFFFF"
INK = "#17191C"
MUTED = "#777B86"
APRICOT = "#FBE1D1"
RUST = "#5D2A1A"
ACCENT = "#3B6DB4"


def render_viewport(image, size, scale, offset):
    """Antialias shrinking previews while keeping one shared canvas transform.

    Pillow's affine BICUBIC samples only a small source neighborhood, so using it
    directly at fit-to-window scale can miss entire one-pixel CAD lines. First
    low-pass/downsample with LANCZOS, then account for the exact rounded raster
    dimensions in the affine transform. Zooming in still samples directly into
    the canvas-sized result and never allocates a full enlarged drawing.
    """
    if scale <= 0:
        raise ValueError("Preview scale must be positive.")
    width, height = size
    if width < 1 or height < 1:
        raise ValueError("Preview viewport must have a positive size.")
    sampled = image
    source_x = source_y = 1.0
    if scale < 1:
        sampled_size = (max(1, math.ceil(image.width * scale)),
                        max(1, math.ceil(image.height * scale)))
        sampled = image.resize(sampled_size, Image.Resampling.LANCZOS,
                               reducing_gap=3.0)
        source_x = sampled.width / image.width
        source_y = sampled.height / image.height
    return sampled.transform(
        (width, height), Image.Transform.AFFINE,
        (source_x / scale, 0, -offset[0] * source_x / scale,
         0, source_y / scale, -offset[1] * source_y / scale),
        resample=Image.Resampling.BICUBIC, fillcolor="white",
    )


def compose_swipe(old_view, new_view, fraction):
    """Combine aligned old-left/new-right previews at the selected boundary."""
    if old_view.size != new_view.size:
        raise ValueError("The two previews must have identical dimensions.")
    width, height = old_view.size
    split = round(width * max(0.0, min(1.0, fraction)))
    result = old_view.copy()
    if split < width:
        result.paste(new_view.crop((split, 0, width, height)), (split, 0))
    return result


def _read_preview(path):
    with Image.open(path) as opened:
        rgba = opened.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")


class DwgCompareWindow(ctk.CTkToplevel):
    """Select two DWGs and compare their model-space previews in one viewport."""

    def __init__(self, parent, initial_new_path=None):
        super().__init__(parent)
        self.title("DWG 圖面變更比對")
        self.geometry("1320x900")
        self.minsize(1100, 750)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self._on_destroy, add="+")

        self._closed = False
        self._busy = False
        self._worker = None
        self._cancel_event = threading.Event()
        self._events = queue.Queue()
        self._job_id = 0
        self._poll_id = None
        self._render_id = None
        self._originals = None
        self._pdfs = None
        self._views = None
        self._viewport_diff = None
        self._regions = []
        self._selected_region = None
        self._generation = 0
        self._display_signature = None
        self._failed_signature = None
        self._viewport_pending = None
        self._viewport_cancel = threading.Event()
        self._tile_cache = None
        self._refine_id = None
        self._last_interaction = 0.0
        self._last_refine = 0.0
        self._zoom_target = None
        self._zoom_animation_id = None
        self._zoom_tick = 0.0
        self._view_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dwg-vector-view")
        self._view_dirty = True
        self._photo = None
        self._sharp_photo = None
        self._sharp_photo_key = None
        self._image_item = None
        self._scale = 1.0
        self._offset = (0.0, 0.0)
        self._canvas_size = (1, 1)
        self._fit_mode = True
        self._fraction = 0.5
        self._drag = None
        self._fullscreen = False
        self._fullscreen_sidebar = False
        self._restore_window_state = None
        self._old_path = tk.StringVar(master=self, value="")
        self._new_path = tk.StringVar(
            master=self, value=os.fspath(initial_new_path) if initial_new_path else "")
        self._status = tk.StringVar(master=self, value="選擇舊版與新版 DWG，然後按「產生比較」。")
        self._zoom_text = tk.StringVar(master=self, value="—")
        self._mode = tk.StringVar(master=self, value="差異疊圖")
        self._show_regions = tk.BooleanVar(master=self, value=True)
        self._make_widgets()
        self.bind("<F11>", self._toggle_fullscreen, add="+")
        self.bind("<Escape>", self._exit_fullscreen, add="+")
        self._set_controls()
        self._poll_id = self.after(80, self._poll_worker)
        self.after(120, self._raise_window)

    def _make_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        header = ctk.CTkFrame(self, fg_color=APRICOT, corner_radius=0)
        self._header = header
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(header, text="DWG 圖面變更比對", text_color=RUST,
                     font=("Microsoft JhengHei UI", 22, "bold")).pack(
                         anchor="w", padx=24, pady=(16, 2))
        ctk.CTkLabel(header, text="自行選擇左側舊版與右側新版；紅色是刪除、綠色是新增，點選差異清單可放大定位。",
                     text_color=RUST, font=("Microsoft JhengHei UI", 12)).pack(
                         anchor="w", padx=24, pady=(0, 13))

        files = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12)
        self._files_panel = files
        files.grid(row=1, column=0, padx=20, pady=(14, 10), sticky="ew")
        files.grid_columnconfigure(1, weight=1)
        self._choose_buttons = []
        for row, (label, variable) in enumerate(
                (("舊版 · 左側", self._old_path), ("新版 · 右側", self._new_path))):
            ctk.CTkLabel(files, text=label, text_color=RUST,
                         font=("Microsoft JhengHei UI", 13, "bold"), width=100).grid(
                             row=row, column=0, padx=(14, 8), pady=10)
            entry = ctk.CTkEntry(files, textvariable=variable, state="readonly",
                                text_color=INK, fg_color=BG, height=32)
            entry.grid(row=row, column=1, sticky="ew", pady=10)
            button = ctk.CTkButton(files, text="選擇 DWG…", width=115, height=32,
                                   fg_color=INK, hover_color="#33363C",
                                   command=lambda v=variable: self._choose_file(v))
            button.grid(row=row, column=2, padx=14, pady=10)
            self._choose_buttons.append(button)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        self._toolbar = toolbar
        toolbar.grid(row=2, column=0, padx=20, pady=(0, 6), sticky="ew")
        toolbar.grid_columnconfigure(3, weight=1)
        self._compare_button = ctk.CTkButton(
            toolbar, text="產生比較", width=120, height=34,
            fg_color=INK, hover_color="#33363C", command=self._start_render)
        self._compare_button.grid(row=0, column=0, padx=(0, 8))
        self._swap_button = ctk.CTkButton(
            toolbar, text="交換新舊", width=94, height=34,
            fg_color=APRICOT, hover_color="#F6D3BD", text_color=RUST,
            command=self._swap_files)
        self._swap_button.grid(row=0, column=1, padx=(0, 8))
        self._cancel_button = ctk.CTkButton(
            toolbar, text="取消轉換", width=94, height=34,
            fg_color="#ECEAE6", hover_color="#DEDBD5", text_color=INK,
            command=self._cancel_render)
        self._cancel_button.grid(row=0, column=2)
        self._zoom_out = ctk.CTkButton(
            toolbar, text="−", width=34, height=34, fg_color="#ECEAE6",
            hover_color="#DEDBD5", text_color=INK, font=("Arial", 20),
            command=lambda: self._zoom(1 / 1.25, smooth=True))
        self._zoom_out.grid(row=0, column=4, padx=4)
        self._zoom_entry = ctk.CTkEntry(toolbar, textvariable=self._zoom_text, width=100,
                                       height=34, justify="center", text_color=INK)
        self._zoom_entry.grid(row=0, column=5)
        self._zoom_entry.bind("<Return>", self._on_zoom_entry)
        self._zoom_entry.bind("<FocusOut>", lambda _e: self._update_zoom_text())
        self._zoom_in = ctk.CTkButton(
            toolbar, text="+", width=34, height=34, fg_color="#ECEAE6",
            hover_color="#DEDBD5", text_color=INK, font=("Arial", 20),
            command=lambda: self._zoom(1.25, smooth=True))
        self._zoom_in.grid(row=0, column=6, padx=4)
        self._fit_button = ctk.CTkButton(
            toolbar, text="適合視窗", width=94, height=34,
            fg_color=APRICOT, hover_color="#F6D3BD", text_color=RUST,
            command=self._fit)
        self._fit_button.grid(row=0, column=7, padx=(8, 0))
        ctk.CTkButton(toolbar, text="全螢幕看圖 · F11", width=142, height=34,
                       fg_color=RUST, hover_color="#854734", command=self._toggle_fullscreen).grid(
                           row=0, column=8, padx=(10, 0))

        modes = ctk.CTkFrame(self, fg_color="transparent")
        self._modes_panel = modes
        modes.grid(row=3, column=0, padx=20, pady=(0, 8), sticky="ew")
        self._mode_switch = ctk.CTkSegmentedButton(
            modes, values=["差異疊圖", "左右滑桿", "舊版原圖", "新版原圖"],
            variable=self._mode, command=self._on_mode,
            selected_color=RUST, selected_hover_color="#854734")
        self._mode_switch.pack(side="left")
        for label, color in (("  ● 刪除", "#C83232"), ("  ● 新增", "#00894F"), ("  ● 未變更", "#777777")):
            ctk.CTkLabel(modes, text=label, text_color=color,
                         font=("Microsoft JhengHei UI", 12, "bold")).pack(side="left", padx=3)
        ctk.CTkCheckBox(modes, text="標出差異位置", variable=self._show_regions,
                        command=self._schedule_render, width=115, checkbox_width=18,
                        checkbox_height=18, fg_color=RUST).pack(side="right", padx=6)

        surface = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0,
                               border_width=1, border_color="#E4E1DD")
        self._surface = surface
        surface.grid(row=4, column=0, padx=20, sticky="nsew")
        surface.grid_rowconfigure(0, weight=1)
        surface.grid_columnconfigure(0, weight=1)
        self._canvas = tk.Canvas(surface, bg="white", highlightthickness=0,
                                 bd=0, cursor="arrow")
        self._canvas.grid(row=0, column=0, padx=1, pady=1, sticky="nsew")
        sidebar = ctk.CTkFrame(surface, fg_color="#F4F2EF", corner_radius=0, width=220)
        self._sidebar = sidebar
        sidebar.grid(row=0, column=1, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(2, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        self._region_summary = ctk.CTkLabel(sidebar, text="視覺差異區域", anchor="w",
                                            font=("Microsoft JhengHei UI", 14, "bold"))
        self._region_summary.grid(row=0, column=0, padx=12, pady=(12, 2), sticky="ew")
        ctk.CTkLabel(sidebar, text="點選清單，放大查看變更", text_color=MUTED,
                     font=("Microsoft JhengHei UI", 11)).grid(row=1, column=0, padx=10, sticky="w")
        self._region_tree = ttk.Treeview(sidebar, columns=("kind",), show="tree headings",
                                         height=8, selectmode="browse")
        self._region_tree.heading("#0", text="區域")
        self._region_tree.heading("kind", text="變更")
        self._region_tree.column("#0", width=64, stretch=False)
        self._region_tree.column("kind", width=117, stretch=True)
        self._region_tree.grid(row=2, column=0, padx=(10, 0), pady=8, sticky="nsew")
        region_scroll = ttk.Scrollbar(sidebar, orient="vertical", command=self._region_tree.yview)
        region_scroll.grid(row=2, column=1, pady=8, sticky="ns")
        self._region_tree.configure(yscrollcommand=region_scroll.set)
        self._region_tree.tag_configure("added", foreground="#00894F")
        self._region_tree.tag_configure("removed", foreground="#C83232")
        self._region_tree.bind("<<TreeviewSelect>>", self._on_region)
        navigation = ctk.CTkFrame(sidebar, fg_color="transparent")
        navigation.grid(row=3, column=0, padx=10, pady=(0, 6), sticky="ew")
        ctk.CTkButton(navigation, text="‹ 上一處", width=85, height=28, fg_color=RUST,
                       command=lambda: self._step_region(-1)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(navigation, text="下一處 ›", width=85, height=28, fg_color=RUST,
                       command=lambda: self._step_region(1)).pack(side="left")
        ctk.CTkLabel(sidebar, text="紅：僅舊版有　綠：僅新版有\n灰：兩版相同\n\n滾輪縮放 · 拖曳平移\n雙擊回到全圖\n\n此清單是影像差異區域，\n不是 CAD 圖元數量。", justify="left",
                     text_color=MUTED, font=("Microsoft JhengHei UI", 11)).grid(
                         row=4, column=0, padx=12, pady=(2, 12), sticky="w")
        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<MouseWheel>", self._on_wheel)
        self._canvas.bind("<Button-4>", lambda e: self._zoom(1.18, (e.x, e.y)))
        self._canvas.bind("<Button-5>", lambda e: self._zoom(1 / 1.18, (e.x, e.y)))
        self._canvas.bind("<Double-Button-1>", lambda e: self._fit())

        swipe = ctk.CTkFrame(self, fg_color="transparent")
        self._swipe_bar = swipe
        swipe.grid(row=5, column=0, padx=22, pady=(7, 3), sticky="ew")
        swipe.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(swipe, text="全看新版", text_color=MUTED, width=72).grid(
            row=0, column=0)
        self._slider = ctk.CTkSlider(
            swipe, from_=0, to=1, command=self._on_slider, fg_color="#E4E1DD",
            progress_color=APRICOT, button_color=RUST, button_hover_color="#854734")
        self._slider.set(self._fraction)
        self._slider.grid(row=0, column=1, padx=10, sticky="ew")
        ctk.CTkLabel(swipe, text="全看舊版", text_color=MUTED, width=72).grid(
            row=0, column=2)
        swipe.grid_remove()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        self._footer = footer
        footer.grid(row=6, column=0, padx=22, pady=(0, 12), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self._progress = ctk.CTkProgressBar(footer, mode="indeterminate", height=3,
                                           progress_color=RUST)
        self._progress.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._progress.set(0)
        self._status_label = ctk.CTkLabel(
            footer, textvariable=self._status, text_color=INK, anchor="w",
            font=("Microsoft JhengHei UI", 12), wraplength=1000, justify="left")
        self._status_label.grid(row=1, column=0, sticky="ew")
        ctk.CTkLabel(
            footer,
            text="模型空間視覺比對，需相同座標與單位；清單依全圖預覽偵測，請放大複核細節。XREF 使用目前可讀取版本。",
            text_color=MUTED, anchor="w", font=("Microsoft JhengHei UI", 11),
        ).grid(row=2, column=0, sticky="ew")
        self._log = ctk.CTkTextbox(footer, height=48, fg_color="#F1EFEC",
                                  text_color=MUTED, corner_radius=6,
                                  font=("Microsoft JhengHei UI", 11), wrap="word")
        self._log.grid(row=3, column=0, pady=(4, 0), sticky="ew")
        self._log.configure(state="disabled")
        self._make_fullscreen_controls()

    def _make_fullscreen_controls(self):
        """Compact controls in the same independent window/canvas, no second renderer."""
        self._focus_bar = ctk.CTkFrame(self, fg_color="#F4F2EF", corner_radius=0, height=48)
        self._focus_bar.grid(row=0, column=0, sticky="ew")
        self._focus_bar.grid_propagate(False)
        self._focus_bar.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(self._focus_bar, fg_color="transparent")
        left.grid(row=0, column=0, padx=8, pady=7)
        ctk.CTkOptionMenu(left, values=["差異疊圖", "左右滑桿", "舊版原圖", "新版原圖"],
                          variable=self._mode, command=self._on_mode, width=128, height=34,
                          fg_color=RUST, button_color=RUST, button_hover_color="#854734").pack(
                              side="left", padx=(0, 8))
        self._focus_navigation = []
        for label, direction in (("‹ 上一處", -1), ("下一處 ›", 1)):
            button = ctk.CTkButton(left, text=label, width=76, height=34,
                                    fg_color=INK, hover_color="#33363C",
                                    command=lambda d=direction: self._step_region(d))
            button.pack(side="left", padx=2)
            self._focus_navigation.append(button)
        self._focus_region_label = ctk.CTkLabel(left, text="差異 0 區", width=88, text_color=RUST)
        self._focus_region_label.pack(side="left", padx=4)
        self._focus_cancel = ctk.CTkButton(left, text="取消轉換", width=80, height=34,
                                           fg_color=RUST, command=self._cancel_render)
        right = ctk.CTkFrame(self._focus_bar, fg_color="transparent")
        right.grid(row=0, column=2, padx=8, pady=7)
        self._focus_zoom_controls = []
        button = ctk.CTkButton(right, text="−", width=32, height=34, fg_color=INK,
                                command=lambda: self._zoom(1/1.25, smooth=True))
        button.pack(side="left", padx=2)
        self._focus_zoom_controls.append(button)
        self._focus_zoom_entry = ctk.CTkEntry(right, textvariable=self._zoom_text,
                                             width=90, height=34, justify="center")
        self._focus_zoom_entry.pack(side="left", padx=2)
        self._focus_zoom_entry.bind("<Return>", self._on_zoom_entry)
        self._focus_zoom_entry.bind("<FocusOut>", lambda _e: self._update_zoom_text())
        self._focus_zoom_controls.append(self._focus_zoom_entry)
        for label, command, width in (("+", lambda: self._zoom(1.25, smooth=True), 32),
                                      ("全圖", self._fit, 58)):
            button = ctk.CTkButton(right, text=label, width=width, height=34,
                                    fg_color=INK, command=command)
            button.pack(side="left", padx=2)
            self._focus_zoom_controls.append(button)
        self._focus_sidebar_button = ctk.CTkButton(right, text="顯示清單", width=82, height=34,
                                                   fg_color=RUST, command=self._toggle_fullscreen_sidebar)
        self._focus_sidebar_button.pack(side="left", padx=4)
        ctk.CTkButton(right, text="檔案／紀錄", width=92, height=34,
                       fg_color=RUST, command=self._exit_fullscreen).pack(side="left", padx=2)
        ctk.CTkButton(right, text="離開全螢幕 · Esc", width=140, height=34,
                       fg_color=INK, command=self._exit_fullscreen).pack(side="left", padx=(6, 0))
        self._focus_status = ctk.CTkFrame(self, height=28, fg_color="#F4F2EF", corner_radius=0)
        self._focus_status.grid(row=6, column=0, sticky="ew")
        self._focus_status.grid_propagate(False)
        ctk.CTkLabel(self._focus_status, textvariable=self._status, anchor="w",
                     text_color=INK, font=("Microsoft JhengHei UI", 12)).place(
                         x=10, rely=0.5, relwidth=0.98, anchor="w")
        self._focus_bar.grid_remove()
        self._focus_status.grid_remove()

    def _toggle_fullscreen(self, _event=None):
        self.set_fullscreen(not self._fullscreen)
        return "break"

    def _exit_fullscreen(self, _event=None):
        if self._fullscreen:
            self.set_fullscreen(False)
            return "break"

    def set_fullscreen(self, enabled=True):
        """Keep the loaded pair, zoom, world centre, cache and selected region."""
        if self._closed or bool(enabled) == self._fullscreen:
            return
        self._stop_zoom_animation()
        self._drag = None
        self._viewport_cancel.set()
        if enabled:
            self._restore_window_state = (self.state(), self.geometry())
            self.attributes("-fullscreen", True)
        else:
            self.attributes("-fullscreen", False)
        self._fullscreen = bool(enabled)
        self._apply_view_layout()
        if not enabled and self._restore_window_state:
            state, geometry = self._restore_window_state
            # Preserve maximized and normal-window states, including monitor
            # position. Never hide an active window by restoring 'withdrawn'.
            if state == "normal":
                self.geometry(geometry)
            if state in ("normal", "zoomed"):
                self.state(state)
        self._canvas.focus_set()
        self._schedule_render()

    def _apply_view_layout(self):
        for panel in (self._header, self._files_panel, self._toolbar, self._modes_panel, self._footer):
            panel.grid_remove() if self._fullscreen else panel.grid()
        for panel in (self._focus_bar, self._focus_status):
            panel.grid() if self._fullscreen else panel.grid_remove()
        self._surface.grid_configure(padx=0 if self._fullscreen else 20)
        if self._fullscreen and not self._fullscreen_sidebar:
            self._sidebar.grid_remove()
        else:
            self._sidebar.grid()
        if self._mode.get() == "左右滑桿" and not self._fullscreen:
            self._swipe_bar.grid()
        else:
            self._swipe_bar.grid_remove()
        self._focus_sidebar_button.configure(text="收合清單" if self._fullscreen_sidebar else "顯示清單")

    def _toggle_fullscreen_sidebar(self):
        self._fullscreen_sidebar = not self._fullscreen_sidebar
        self._stop_zoom_animation()
        self._viewport_cancel.set()
        self._apply_view_layout()
        self._schedule_render()

    def _raise_window(self):
        if not self._closed:
            self.lift()
            self.focus_set()

    def set_new_path(self, path):
        """Reuse this window for another DWG selected in the synchronizer."""
        if self._closed:
            return False
        if self._busy:
            messagebox.showinfo(
                "DWG 比較正在處理",
                "目前正在轉換圖面。請等待完成，或先按「取消轉換」，再選擇要比較的檔案。",
                parent=self)
            return False
        new_path = os.path.normpath(os.fspath(path))
        current = self._new_path.get()
        if current and os.path.normcase(os.path.abspath(current)) == os.path.normcase(os.path.abspath(new_path)):
            return True
        self._new_path.set(new_path)
        self._invalidate_preview()
        return True

    def _choose_file(self, variable):
        if self._busy:
            return
        current = variable.get() or self._new_path.get() or self._old_path.get()
        path = filedialog.askopenfilename(
            parent=self, title="選擇舊版 DWG" if variable is self._old_path else "選擇新版 DWG",
            initialdir=os.path.dirname(current) if current else None,
            filetypes=[("AutoCAD 圖檔", "*.dwg")])
        if path:
            variable.set(os.path.normpath(path))
            self._invalidate_preview()

    def _swap_files(self):
        if self._busy:
            return
        old, new = self._old_path.get(), self._new_path.get()
        self._old_path.set(new)
        self._new_path.set(old)
        if self._originals:
            self._originals = self._originals[::-1]
            if self._pdfs:
                self._pdfs = self._pdfs[::-1]
            for region in self._regions:
                region["added_pixels"], region["removed_pixels"] = region["removed_pixels"], region["added_pixels"]
                region["kind"] = {"added": "removed", "removed": "added", "changed": "changed"}[region["kind"]]
            self._generation += 1
            self._viewport_cancel.set()
            self._tile_cache = None
            self._populate_regions()
            self._view_dirty = True
            self._status.set("已交換舊版與新版；紅色刪除 / 綠色新增的方向也已交換。")
            self._schedule_render()
        self._set_controls()

    def _invalidate_preview(self):
        self._stop_zoom_animation()
        self._viewport_cancel.set()
        self._tile_cache = None
        self._drag = None
        self._generation += 1
        self._originals = None
        self._pdfs = None
        self._views = None
        self._viewport_diff = None
        self._regions = []
        self._selected_region = None
        self._display_signature = None
        self._failed_signature = None
        self._photo = None
        self._sharp_photo = None
        self._sharp_photo_key = None
        self._image_item = None
        self._populate_regions()
        self._zoom_text.set("—")
        self._status.set("已選擇檔案，按「產生比較」載入兩份圖面。")
        self._canvas.delete("all")
        self._set_controls()
        self._schedule_render()

    def _set_controls(self):
        select_state = "disabled" if self._busy else "normal"
        for button in self._choose_buttons:
            button.configure(state=select_state)
        ready = bool(self._old_path.get() and self._new_path.get())
        self._compare_button.configure(state="normal" if ready and not self._busy else "disabled")
        self._swap_button.configure(state="normal" if ready and not self._busy else "disabled")
        self._cancel_button.configure(state="normal" if self._busy else "disabled")
        view_state = "normal" if self._originals else "disabled"
        for widget in (self._zoom_in, self._zoom_out, self._fit_button, self._slider, self._zoom_entry):
            widget.configure(state=view_state)
        for widget in self._focus_zoom_controls:
            widget.configure(state=view_state)
        for widget in self._focus_navigation:
            widget.configure(state="normal" if self._regions else "disabled")
        if self._busy:
            self._focus_cancel.configure(state="disabled" if self._cancel_event.is_set() else "normal")
            self._focus_cancel.pack(side="left", padx=4)
        else:
            self._focus_cancel.pack_forget()

    def _populate_regions(self):
        children = self._region_tree.get_children()
        if children:
            self._region_tree.delete(*children)
        kinds = {"added": "新增", "removed": "刪除", "changed": "新增＋刪除"}
        for index, region in enumerate(self._regions):
            self._region_tree.insert("", "end", iid=str(index), text=f"{index + 1:02d}",
                                      values=(kinds[region["kind"]],), tags=(region["kind"],))
        self._region_summary.configure(text=f"視覺差異：{len(self._regions)} 個區域" if self._originals else "視覺差異區域")
        self._update_focus_region()

    def _update_focus_region(self):
        index = self._selected_region
        self._focus_region_label.configure(text=(f"{index+1} / {len(self._regions)} 區"
                                                  if index is not None else f"差異 {len(self._regions)} 區"))

    def _on_region(self, _event=None):
        selected = self._region_tree.selection()
        if not selected or not self._originals:
            return
        index = int(selected[0])
        if index >= len(self._regions):
            return
        self._stop_zoom_animation()
        self._selected_region = index
        self._update_focus_region()
        x0, y0, x1, y1 = self._regions[index]["bbox"]
        width, height = self._canvas_size
        self._scale = min(max_view_scale(self._originals[0].size),
                          width / max(1, (x1 - x0) * 1.5),
                          height / max(1, (y1 - y0) * 1.5))
        self._offset = (width / 2 - (x0 + x1) * self._scale / 2,
                        height / 2 - (y0 + y1) * self._scale / 2)
        self._fit_mode = False
        self._view_dirty = True
        self._mode.set("差異疊圖")
        self._on_mode("差異疊圖")

    def _step_region(self, direction):
        if not self._regions:
            return
        current = self._selected_region
        index = ((current + direction) % len(self._regions) if current is not None
                 else (0 if direction > 0 else len(self._regions) - 1))
        self._region_tree.selection_set(str(index))
        self._region_tree.see(str(index))

    def _on_mode(self, _value=None):
        self._viewport_cancel.set()
        if self._mode.get() == "左右滑桿" and not self._fullscreen:
            self._swipe_bar.grid()
        else:
            self._swipe_bar.grid_remove()
        self._schedule_render()

    def _append_log(self, text):
        self._log.configure(state="normal")
        self._log.insert("end", str(text).rstrip() + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _start_render(self):
        if self._busy or self._closed:
            return
        old_path, new_path = self._old_path.get().strip(), self._new_path.get().strip()
        for label, path in (("舊版", old_path), ("新版", new_path)):
            if not path.lower().endswith(".dwg") or not os.path.isfile(path):
                messagebox.showwarning("無法讀取圖檔", f"請選擇存在的{label} DWG 檔案。", parent=self)
                return
        try:
            same_file = os.path.samefile(old_path, new_path)
        except OSError as exc:
            messagebox.showwarning("無法讀取圖檔", f"無法確認圖檔位置，請檢查磁碟或網路連線。\n\n{exc}", parent=self)
            return
        if same_file:
            messagebox.showwarning("請選擇不同版次", "舊版與新版目前是同一個檔案，請選擇兩份不同的 DWG。", parent=self)
            return
        self._invalidate_preview()
        self._busy = True
        self._job_id += 1
        job_id = self._job_id
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        self._status.set("正在準備 DWG 預覽；轉換較大的圖面可能需要一些時間…")
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._progress.start()
        self._set_controls()

        def report(text):
            self._events.put((job_id, "progress", str(text)))

        def worker():
            directory = None
            result = None
            error = None
            try:
                from dwg_preview import render_dwg_pair
                directory = tempfile.TemporaryDirectory(prefix="cloud-dwg-compare-")
                rendered = render_dwg_pair(
                    old_path, new_path, directory.name,
                    log=report, cancel_event=cancel_event)
                if not cancel_event.is_set():
                    old_image = _read_preview(rendered["old_png"])
                    new_image = _read_preview(rendered["new_png"])
                    if old_image.size != new_image.size:
                        raise ValueError("兩份預覽的尺寸不一致，無法確保圖面座標對齊。請重新產生比較。")
                    from drawing_diff import compare_previews
                    report("正在偵測線條新增與刪除，建立可定位的差異清單…")
                    differences = compare_previews(old_image, new_image, tolerance=0)
                    pdfs = (Path(rendered["old_pdf"]).read_bytes(),
                            Path(rendered["new_pdf"]).read_bytes())
                    warnings = list(rendered.get("warnings") or [])
                    if differences.get("regions_truncated"):
                        warnings.append("差異區域較多，清單僅列出前 200 區；圖面仍顯示所有差異。")
                    result = (old_image, new_image, warnings, pdfs, differences["regions"])
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            finally:
                if directory is not None:
                    try:
                        directory.cleanup()
                    except OSError:
                        report("部分暫存預覽尚被系統使用，已結束此次比較轉換。")
                if cancel_event.is_set():
                    self._events.put((job_id, "cancelled", None))
                elif error is not None:
                    self._events.put((job_id, "error", error))
                else:
                    self._events.put((job_id, "ready", result))

        # Keep the process alive briefly after closing the UI so the renderer can
        # terminate its own converter and clean up this job's temporary files.
        self._worker = threading.Thread(target=worker, name="dwg-comparison-preview", daemon=False)
        self._worker.start()

    def _cancel_render(self):
        if self._busy:
            self._cancel_event.set()
            self._cancel_button.configure(state="disabled")
            self._focus_cancel.configure(state="disabled")
            self._status.set("正在取消轉換並清理暫存檔…")

    def _poll_worker(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            while True:
                job_id, kind, value = self._events.get_nowait()
                if kind == "viewport":
                    self._viewport_pending = None
                    signature, views, differences, error = value
                    if (self._originals and signature[0] == self._generation
                            and signature[4] == self._mode_key()):
                        if error:
                            if signature[:5] == self._view_signature()[:5]:
                                self._failed_signature = self._view_signature()
                                self._status.set("清晰預覽繪製失敗，請重新產生比較。")
                                self._append_log(error)
                                self._view_dirty = False
                            self._schedule_render()
                        elif differences is not None and not self._frame_matches(signature):
                            self._views, self._viewport_diff = views, differences
                            self._display_signature = signature
                            self._view_dirty = False
                    if self._originals:
                        self._schedule_render()
                    continue
                if job_id != self._job_id:
                    continue
                if kind == "progress":
                    self._append_log(value)
                    if not self._cancel_event.is_set():
                        self._status.set(value[:240])
                    continue
                self._busy = False
                self._progress.stop()
                self._progress.set(0)
                if kind == "ready":
                    old_image, new_image, warnings, pdfs, regions = value
                    self._originals = (old_image, new_image)
                    self._pdfs = pdfs
                    self._regions = regions
                    self._generation += 1
                    self._populate_regions()
                    self._fraction = 0.5
                    self._slider.set(self._fraction)
                    self._fit()
                    self._status.set((f"找到 {len(regions)} 個視覺差異區域：紅色刪除、綠色新增。可用清單或上一處／下一處定位。"
                                      if regions else "全圖預覽未偵測到線條位置差異；仍可放大檢查細節。")
                                     + (" 另有轉換提醒，請查看紀錄。" if warnings else ""))
                    self._append_log("比對完成：紅色＝舊版才有（刪除）；綠色＝新版才有（新增）；灰色＝相同。")
                    self._append_log("差異清單依全圖預覽偵測，不是 CAD 圖元清單；不忽略相鄰像素位移。放大時會重新繪製向量預覽。")
                    for warning in warnings:
                        self._append_log("提醒：" + str(warning))
                elif kind == "cancelled":
                    self._status.set("已取消轉換，可重新選擇圖檔。")
                    self._append_log("使用者已取消此次轉換。")
                else:
                    self._status.set("無法產生比較；請查看下方原因後重試。")
                    self._append_log(value)
                    messagebox.showerror("DWG 比較無法完成", value, parent=self)
                self._set_controls()
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_id = self.after(16 if self._originals else 80, self._poll_worker)

    def _fit(self):
        if not self._originals:
            return
        self._stop_zoom_animation()
        self._viewport_cancel.set()
        self._drag = None
        self._last_interaction = 0.0
        self._selected_region = None
        self._update_focus_region()
        if self._region_tree.selection():
            self._region_tree.selection_remove(*self._region_tree.selection())
        width, height = self._canvas_size
        image_width, image_height = self._originals[0].size
        self._scale = min(width / image_width, height / image_height) * 0.96
        self._offset = ((width - image_width * self._scale) / 2,
                        (height - image_height * self._scale) / 2)
        self._fit_mode = True
        self._view_dirty = True
        self._schedule_render()

    def _stop_zoom_animation(self):
        self._zoom_target = None
        if self._zoom_animation_id is not None:
            self.after_cancel(self._zoom_animation_id)
            self._zoom_animation_id = None

    def _zoom(self, factor, anchor=None, smooth=False):
        if not self._originals:
            return
        if not math.isfinite(factor) or factor <= 0:
            return
        width, height = self._canvas_size
        anchor = anchor or (width / 2, height / 2)
        image_width, image_height = self._originals[0].size
        fit_scale = min(width / image_width, height / image_height) * 0.96
        base_scale, base_offset = self._zoom_target or (self._scale, self._offset)
        limit = max_view_scale(self._originals[0].size)
        requested = base_scale * factor
        new_scale = max(fit_scale * 0.2, min(limit, requested))
        if requested > limit:
            self._status.set(f"已達此圖面的安全放大範圍（約 {limit*100:,.0f}%）；繼續放大可能造成線條精度失真。")
        ratio = new_scale / base_scale
        new_offset = (anchor[0] - (anchor[0] - base_offset[0]) * ratio,
                      anchor[1] - (anchor[1] - base_offset[1]) * ratio)
        self._last_interaction = time.monotonic()
        self._cancel_idle_render()
        if smooth:
            self._zoom_target = (new_scale, new_offset)
            if self._zoom_animation_id is None:
                self._zoom_tick = time.monotonic()
                self._zoom_animation_id = self.after(8, self._animate_zoom)
        else:
            self._stop_zoom_animation()
            self._offset, self._scale = new_offset, new_scale
        self._fit_mode = False
        self._view_dirty = True
        self._schedule_render()

    def _on_zoom_entry(self, _event=None):
        if not self._originals:
            return "break"
        try:
            percent = float(self._zoom_text.get().strip().replace("%", "").replace(",", ""))
            if not math.isfinite(percent) or percent <= 0:
                raise ValueError()
        except ValueError:
            self._status.set("請輸入大於 0 的縮放百分比，例如 3200%，再按 Enter。")
            self._update_zoom_text()
            return "break"
        base = self._zoom_target[0] if self._zoom_target else self._scale
        self._zoom(percent / 100 / base, smooth=True)
        self._canvas.focus_set()
        return "break"

    def _update_zoom_text(self):
        if self._originals:
            self._zoom_text.set(f"{self._scale*100:,.0f}%")

    def _animate_zoom(self):
        self._zoom_animation_id = None
        if self._closed or self._zoom_target is None:
            return
        target_scale, target_offset = self._zoom_target
        now = time.monotonic()
        amount = 1 - math.exp(-max(0.001, now - self._zoom_tick) / 0.025)
        self._zoom_tick = now
        self._scale += (target_scale - self._scale) * amount
        self._offset = tuple(value + (target - value) * amount
                             for value, target in zip(self._offset, target_offset))
        self._last_interaction = now
        if (abs(target_scale - self._scale) < target_scale * 0.001
                and max(abs(a - b) for a, b in zip(self._offset, target_offset)) < 0.35):
            self._scale, self._offset = target_scale, target_offset
            self._zoom_target = None
        else:
            self._zoom_animation_id = self.after(16, self._animate_zoom)
        self._schedule_render()

    def _on_wheel(self, event):
        steps = max(-3.0, min(3.0, event.delta / 120))
        if steps:
            self._zoom(1.18 ** steps, (event.x, event.y), smooth=True)
        return "break"

    def _on_resize(self, event):
        if event.width < 2 or event.height < 2:
            return
        previous = self._canvas_size
        self._canvas_size = (event.width, event.height)
        self._status_label.configure(wraplength=max(400, event.width - 8))
        if self._originals and self._fit_mode:
            self._fit()
        else:
            self._offset = (self._offset[0] + (event.width - previous[0]) / 2,
                            self._offset[1] + (event.height - previous[1]) / 2)
            self._view_dirty = True
            self._schedule_render()

    def _on_slider(self, value):
        self._fraction = float(value)
        self._schedule_render()

    def _on_press(self, event):
        if not self._originals:
            return
        self._stop_zoom_animation()
        split = self._canvas_size[0] * self._fraction
        self._drag = ("swipe" if self._mode.get() == "左右滑桿" and abs(event.x - split) <= 18 else "pan", event.x, event.y)
        self._canvas.configure(cursor="sb_h_double_arrow" if self._drag[0] == "swipe" else "fleur")

    def _on_drag(self, event):
        if not self._drag:
            return
        mode, x, y = self._drag
        if mode == "swipe":
            self._fraction = max(0.0, min(1.0, event.x / self._canvas_size[0]))
            self._slider.set(self._fraction)
        else:
            self._last_interaction = time.monotonic()
            self._cancel_idle_render()
            self._offset = (self._offset[0] + event.x - x,
                            self._offset[1] + event.y - y)
            self._fit_mode = False
            self._view_dirty = True
        self._drag = (mode, event.x, event.y)
        self._schedule_render()

    def _on_release(self, event):
        self._drag = None
        self._on_motion(event)
        self._schedule_render()

    def _on_motion(self, event):
        if self._originals and not self._drag:
            split = self._canvas_size[0] * self._fraction
            self._canvas.configure(cursor="sb_h_double_arrow" if self._mode.get() == "左右滑桿" and abs(event.x - split) <= 18 else "fleur")

    def _mode_key(self):
        return {"差異疊圖": "overlay", "左右滑桿": "swipe",
                "舊版原圖": "old", "新版原圖": "new"}[self._mode.get()]

    def _cancel_idle_render(self):
        if self._viewport_pending and self._viewport_pending[5] == 2:
            self._viewport_cancel.set()

    def _view_signature(self):
        interacting = (self._zoom_target is not None
                       or (self._drag is not None and self._drag[0] == "pan")
                       or time.monotonic() - self._last_interaction < 0.14)
        quality = 1 if interacting or math.prod(self._canvas_size)*4 > 8_000_000 else 2
        return (self._generation, self._canvas_size, self._scale,
                tuple(round(n) for n in self._offset), self._mode_key(), quality)

    def _frame_matches(self, signature):
        displayed = self._display_signature
        return (displayed is not None and displayed[:5] == signature[:5]
                and displayed[5] >= signature[5])

    def _render_signature(self):
        """Request readable native text first, and anticipate the zoom endpoint.

        Rendering does not wait for the animation/idle debounce to finish.
        Only its destination is rendered, never every intermediate tween scale.
        A complete native-size vector frame is then refined at 2x when idle.
        """
        signature = self._view_signature()
        if self._zoom_target is not None:
            scale, offset = self._zoom_target
            signature = (signature[0], signature[1], scale,
                         tuple(round(n) for n in offset), signature[4], 1)
        elif self._display_signature is None or self._display_signature[:5] != signature[:5]:
            signature = (*signature[:5], 1)
        return signature

    def _request_viewport(self, signature):
        """Keep vector rendering off Tk; coalesce drag events to the newest view."""
        if self._viewport_pending is not None or self._closed:
            return
        self._viewport_pending = signature
        self._viewport_cancel = threading.Event()
        cancel_event = self._viewport_cancel
        self._last_refine = time.monotonic()
        pdfs, originals = self._pdfs, self._originals
        _, size, scale, offset, mode, quality = signature
        if pdfs and self._tile_cache is None:
            self._tile_cache = ComparisonTileCache(pdfs, reference_width=originals[0].width)
        cache = self._tile_cache
        job_id = self._job_id

        def render():
            try:
                from drawing_diff import compare_visible_layers
                if pdfs:
                    views, differences = cache.render(size, scale, offset, mode, quality, cancel_event)
                else:
                    views = tuple(render_viewport(im, size, scale, offset)
                                  if mode not in ("old", "new") or index == (mode == "new") else None
                                  for index, im in enumerate(originals))
                    differences = (compare_visible_layers(*views, mode) if mode in ("overlay", "swipe")
                                   else {})
                if cancel_event.is_set():
                    raise TileRenderCancelled()
                return signature, views, differences, None
            except TileRenderCancelled:
                return signature, None, None, None
            except Exception as exc:
                return signature, None, None, str(exc) or type(exc).__name__

        def completed(future):
            if not self._closed and not future.cancelled():
                self._events.put((job_id, "viewport", future.result()))

        self._view_executor.submit(render).add_done_callback(completed)

    def _refine_when_ready(self):
        self._refine_id = None
        self._schedule_render()

    def _schedule_render(self):
        if not self._closed and self._render_id is None:
            self._render_id = self.after(16, self._draw)

    def _draw(self):
        self._render_id = None
        if self._closed:
            return
        width, height = self._canvas_size
        self._canvas.delete("overlay")
        if not self._originals:
            self._canvas.create_text(
                width / 2, height / 2 - 14, text="找出兩份 DWG 的線條變更",
                font=("Microsoft JhengHei UI", 18, "bold"), fill=RUST, tags="overlay")
            self._canvas.create_text(
                width / 2, height / 2 + 20, text="先選擇舊版與新版，再按「產生比較」",
                font=("Microsoft JhengHei UI", 12), fill=MUTED, tags="overlay")
            return
        signature = self._view_signature()
        if self._failed_signature is not None and signature[:5] == self._failed_signature[:5]:
            self._canvas.create_text(width / 2, height / 2, text="清晰預覽繪製失敗，請查看下方紀錄。",
                                     fill=RUST, tags="overlay")
            return
        transient = not self._frame_matches(signature)
        # Even a complete interaction-quality frame needs an idle refinement.
        if (signature[5] == 1 and math.prod(self._canvas_size)*4 <= 8_000_000
                and self._refine_id is None):
            self._refine_id = self.after(150, self._refine_when_ready)
        now = time.monotonic()
        compatible = (self._display_signature is not None
                      and self._display_signature[0] == signature[0]
                      and self._display_signature[4] == signature[4])
        if self._viewport_pending and (self._viewport_pending[0] != signature[0]
                                      or self._viewport_pending[4] != signature[4]):
            self._viewport_cancel.set()
        desired = self._render_signature()
        # The first animation tick may still show the previous exact frame;
        # nevertheless its destination can already be rendered in parallel.
        if not self._frame_matches(desired):
            if not compatible or now - self._last_refine >= 0.04:
                self._request_viewport(desired)
            elif self._refine_id is None:
                self._refine_id = self.after(80, self._refine_when_ready)
        if transient:
            if not compatible:
                if self._image_item is not None:
                    self._canvas.itemconfigure(self._image_item, image="")
                self._canvas.create_text(12, 12, text="正在繪製清晰預覽…", anchor="nw",
                                         fill=RUST, tags="overlay")
                return
        mode = self._mode.get()
        from comparison_view import reproject_image
        source_scale, source_offset = self._display_signature[2:4]
        reproject_needed = self._display_signature[1:4] != signature[1:4]

        def projected(im):
            return (reproject_image(im, (width, height), source_scale, source_offset,
                                     self._scale, self._offset) if reproject_needed else im)

        image_position = (0, 0)
        if mode == "差異疊圖":
            merged = self._viewport_diff["overlay"]
        elif mode == "左右滑桿":
            merged = compose_swipe(projected(self._viewport_diff["old_overlay"]),
                                   projected(self._viewport_diff["new_overlay"]), self._fraction)
        else:
            merged = self._views[0 if mode == "舊版原圖" else 1]
        if mode != "左右滑桿":
            cache_key = (self._display_signature, mode)
            if self._sharp_photo_key != cache_key:
                self._sharp_photo = ImageTk.PhotoImage(merged, master=self._canvas)
                self._sharp_photo_key = cache_key
            if abs(self._scale / source_scale - 1) < 1e-8:
                # Panning is just moving the existing Tk image;
                # no PDF, difference calculation or pixel upload on this path.
                self._photo = self._sharp_photo
                image_position = tuple(round(a-b) for a, b in zip(self._offset, source_offset))
            else:
                self._photo = ImageTk.PhotoImage(projected(merged), master=self._canvas)
        else:
            self._photo = ImageTk.PhotoImage(merged, master=self._canvas)
        if self._image_item is None:
            self._image_item = self._canvas.create_image(*image_position, anchor="nw", image=self._photo)
        else:
            self._canvas.itemconfigure(self._image_item, image=self._photo)
            self._canvas.coords(self._image_item, *image_position)
        self._canvas.tag_lower(self._image_item)
        # Do not replace partially typed percentages while the field has focus.
        if self.focus_get() not in (self._zoom_entry._entry, self._focus_zoom_entry._entry):
            self._update_zoom_text()
        if mode in ("差異疊圖", "左右滑桿"):
            for index, region in enumerate(self._regions):
                if not self._show_regions.get() and index != self._selected_region:
                    continue
                x0, y0, x1, y1 = region["bbox"]
                left, top = x0 * self._scale + self._offset[0] - 5, y0 * self._scale + self._offset[1] - 5
                right, bottom = x1 * self._scale + self._offset[0] + 5, y1 * self._scale + self._offset[1] + 5
                if right < 0 or bottom < 0 or left > width or top > height:
                    continue
                color = ACCENT if index == self._selected_region else {
                    "added": "#00894F", "removed": "#C83232", "changed": "#AD701E"}[region["kind"]]
                self._canvas.create_rectangle(left, top, right, bottom, outline=color,
                                               width=1, dash=(5, 4), tags="overlay")
                self._canvas.create_text(max(14, min(width-14, left)), max(12, min(height-12, top-9)),
                                          text=f"{index+1:02d}", anchor="w", fill=color,
                                          font=("Microsoft JhengHei UI", 12, "bold"), tags="overlay")
        if mode != "左右滑桿":
            return
        split = round(width * self._fraction)
        self._canvas.create_line(split + 1, 0, split + 1, height,
                                 fill="white", width=5, tags="overlay")
        self._canvas.create_line(split, 0, split, height,
                                 fill=RUST, width=2, tags="overlay")
        center_y = height / 2
        self._canvas.create_oval(split - 17, center_y - 21, split + 17, center_y + 21,
                                 fill=APRICOT, outline=RUST, width=2, tags="overlay")
        self._canvas.create_line(split - 4, center_y - 6, split - 10, center_y,
                                 split - 4, center_y + 6, fill=RUST, width=2, tags="overlay")
        self._canvas.create_line(split + 4, center_y - 6, split + 10, center_y,
                                 split + 4, center_y + 6, fill=RUST, width=2, tags="overlay")
        labels = []
        if split >= 60:
            labels.append((14, "nw", "舊版"))
        if width - split >= 60:
            labels.append((width - 14, "ne", "新版"))
        for x, anchor, label in labels:
            text_item = self._canvas.create_text(
                x, 12, anchor=anchor, text=label, fill=RUST,
                font=("Microsoft JhengHei UI", 12, "bold"), tags="overlay")
            bbox = self._canvas.bbox(text_item)
            badge = self._canvas.create_rectangle(
                bbox[0] - 7, bbox[1] - 4, bbox[2] + 7, bbox[3] + 4,
                fill=APRICOT, outline="", tags="overlay")
            self._canvas.tag_lower(badge, text_item)

    def _on_destroy(self, event):
        if event.widget is self:
            self._dispose()

    def _dispose(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_event.set()
        self._viewport_cancel.set()
        self._tile_cache = None
        self._view_executor.shutdown(wait=False, cancel_futures=True)
        for callback in (self._poll_id, self._render_id, self._refine_id, self._zoom_animation_id):
            if callback is not None:
                try:
                    self.after_cancel(callback)
                except tk.TclError:
                    pass
        self._poll_id = self._render_id = None
        self._originals = self._views = self._photo = None
        self._sharp_photo = self._sharp_photo_key = None
        self._pdfs = self._viewport_diff = None
        try:
            while True:
                self._events.get_nowait()
        except queue.Empty:
            pass

    def destroy(self):
        self._dispose()
        super().destroy()
