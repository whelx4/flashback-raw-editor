"""
Main application window.

LoFiLogicEditor — QMainWindow: file loading, presets, thumbnail strip,
                  export, keyboard shortcuts, drag & drop.

"""
import logging
import sys
import os
import shutil
import time
import traceback
import platform
import re
from collections import OrderedDict
from pathlib import Path

# core must be imported before colour to apply the NumPy 2.0 compatibility shim
import core  # noqa: F401

log = logging.getLogger(__name__)

import numpy as np
import cv2
import colour

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QPushButton, QFileDialog, QMessageBox, QProgressBar,
    QScrollArea, QFrame, QSizePolicy, QCheckBox,
)
from PySide6.QtCore import (
    Qt, QTimer, QSize, Signal, QPoint, QThread, QEvent,
    QPropertyAnimation, QEasingCurve, QUrl, QStandardPaths, QSettings,
)
from PySide6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QPen, QCursor, QFont,
    QFontDatabase, QLinearGradient, QMovie, QPainterPath, QColorSpace,
    QSurfaceFormat, QAction, QKeySequence, QIcon, QPalette,
)

from core import resource_path
from core.gpu import gpu
from core.processor import ImageProcessor, export_image, input_kind_for_path
from core.config import (
    _timing_print, VIBE_PRESETS, VIBE_EXPORT_SUFFIX,
    VibeConfig, ImageAdjustments, vibe_config_for,
)
from core.export_naming import export_basename
from core.input_formats import (
    SUPPORTED_EXTENSIONS as ALL_SUPPORTED_EXTENSIONS,
    is_raster_path, is_sony_p43_path,
)
from core.preset_catalog import normalize_preset_id
from core import vibe_state

from .widgets import (
    ThumbnailWorker, ThumbnailWidget, ThumbnailStrip,
    FadeOverlayWidget, LoaderOverlay, ZoomableImageWidget, VibePicker,
    RenderWorker, VibeRefreshWorker,
)
from .debug_panel import DebugPanel
from .scrub_slider import ScrubSlider
from . import theme
from .theme import (
    C, UI_FONT, MONO_FONT,
    icon_btn_qss, section_title_qss, section_reset_link_qss,
    process_btn_qss, format_pill_qss, svg_icon,
)


# =============================================================================
# MAIN EDITOR WINDOW
# =============================================================================

def _value_nbytes(value):
    """Bytes held by a cache value: a numpy array, or a tuple/list that
    contains arrays (preview_cache stores ``(key, img_array)``)."""
    nb = getattr(value, 'nbytes', None)
    if nb is not None:
        return nb
    if isinstance(value, (tuple, list)):
        return sum(_value_nbytes(v) for v in value)
    return 0


class _CacheBudget:
    """Shared, dynamically-sized memory budget for the array caches.

    All caches that register share one pool, so the cap is the total resident
    cache memory — not per-cache. The limit is recomputed live from system RAM
    (via psutil) as ``min(fraction * total, available - reserve)`` so the caches
    automatically back off when other software consumes memory, and grow again
    when it's freed. Falls back to a fixed limit if psutil is unavailable.
    """

    def __init__(self, fraction=0.25, reserve_bytes=2 * 1024 ** 3,
                 floor_bytes=512 * 1024 ** 2, fallback_bytes=2 * 1024 ** 3):
        self.fraction = fraction
        self.reserve = reserve_bytes
        self.floor = floor_bytes
        self.fallback = fallback_bytes
        self.used = 0
        self._caches = []

    def register(self, cache):
        self._caches.append(cache)

    def limit(self):
        try:
            import psutil
            vm = psutil.virtual_memory()
            return max(self.floor, min(int(self.fraction * vm.total),
                                       int(vm.available - self.reserve)))
        except Exception:
            return max(self.floor, self.fallback)

    def enforce(self, protect=()):
        """Evict least-recently-used entries across all registered caches until
        the shared total is within the current limit. ``protect`` names keys that
        must never be evicted (the active image)."""
        limit = self.limit()
        # Guard bounds the loop against pathological states; normal exit is the
        # budget condition or running out of evictable entries.
        for _ in range(1_000_000):
            if self.used <= limit:
                return
            victim = None
            for cache in self._caches:
                for key in cache:                # OrderedDict: oldest first
                    if key not in protect:
                        victim = (cache, key)
                        break
                if victim:
                    break
            if victim is None:
                return                            # everything left is protected
            cache, key = victim
            del cache[key]


class _ByteBudgetLRU(OrderedDict):
    """LRU cache of numpy arrays sharing a :class:`_CacheBudget`.

    get/set bump recency; inserts trigger a shared-budget enforcement that
    evicts the globally least-recently-used entries across all caches sharing
    the budget. Evicted intermediates are re-derived from disk on next visit,
    and the currently displayed image survives because the processor holds its
    own reference to that array (and it is passed as ``protect`` on insert).
    """

    def __init__(self, budget: _CacheBudget):
        super().__init__()
        self._budget = budget
        budget.register(self)

    def __getitem__(self, key):
        self.move_to_end(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self[key] if key in self else default

    def __setitem__(self, key, value):
        if key in self:
            del self[key]
        super().__setitem__(key, value)
        self._budget.used += _value_nbytes(value)
        self._budget.enforce(protect=(key,))

    def __delitem__(self, key):
        self._budget.used -= _value_nbytes(super().__getitem__(key))
        super().__delitem__(key)

    def pop(self, key, *default):
        # Delete through our own __delitem__ (which adjusts the budget) using
        # the raw OrderedDict accessor — NOT super().pop / self[key], both of
        # which re-enter the overridden __getitem__ and move_to_end, turning an
        # absent-key lookup into a KeyError that defeats the `default` arg.
        if key in self:
            value = OrderedDict.__getitem__(self, key)
            del self[key]
            return value
        if default:
            return default[0]
        raise KeyError(key)

    def clear(self):
        for v in self.values():
            self._budget.used -= _value_nbytes(v)
        super().clear()

    def prune_to(self, valid_keys):
        """Drop entries whose key is not in ``valid_keys`` (e.g. images no
        longer in the open project)."""
        for key in [k for k in self if k not in valid_keys]:
            del self[key]


class LoFiLogicEditor(QMainWindow):
    """Main application window for LoFi Logic image editing."""

    SUPPORTED_EXTENSIONS = ALL_SUPPORTED_EXTENSIONS

    def __init__(self):
        super().__init__()

        if sys.platform == 'darwin':
            self.setUnifiedTitleAndToolBarOnMac(True)

        # Application state
        self.processor = None
        self.image_files = []
        self.current_index = 0
        self.image_settings = {}
        # All array caches share one dynamic, RAM-relative memory budget so the
        # combined resident cache never exceeds it; entries are LRU-evicted
        # across caches and re-derived from disk on next visit.
        self.cache_budget = _CacheBudget()
        self.image_cache = _ByteBudgetLRU(self.cache_budget)
        self.preview_cache = _ByteBudgetLRU(self.cache_budget)
        self.export_mode = 'jpeg'  # 'jpeg' | 'tiff' | 'dng'
        self.thumbnail_cache = _ByteBudgetLRU(self.cache_budget)
        self._file_is_flashback: dict = {}  # path_str -> bool
        # Cumulative rotation in degrees (0/90/180/270) per image path. The
        # processor *consumes* its rotation field by burning it into the
        # intermediate, so we keep our own running tally that survives reloads
        # and gets persisted in project files.
        self.image_rotations: dict = {}
        # Path of the currently-open project file (.lofi), or None if the
        # current image set didn't come from a project. Save reuses this;
        # Save As always prompts.
        self.current_project_path = None  # type: ignore[assignment]

        self.app_settings = QSettings("LoFi Logic", "Editor")

        # The export destination and last-opened camera folder are persisted
        # independently. P43 originals stay where the user put them; LoFi Logic
        # never moves or deletes source JPEGs.
        pictures_loc = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        base_dir = pictures_loc if pictures_loc else str(Path.home())
        fallback = os.path.join(base_dir, "LoFi_Logic")

        def _resolve(key: str) -> str:
            v = self.app_settings.value(key, fallback)
            return v if isinstance(v, str) and v else fallback

        self.output_dir = _resolve("default_export_dir")
        os.makedirs(self.output_dir, exist_ok=True)

        # The active vibe — replaces the old global DebugConfig. Initialized
        # to factory disposable here; the real vibe is loaded in
        # _on_vibe_selected() once the picker exists.
        self.current_vibe = VibeConfig()
        self.current_vibe.dng_profile_name = self.app_settings.value(
            "dng_profile_name", "Flashback Standard"
        )

        # Theme: load persisted choice (default: light) and register listener
        # so every setStyleSheet()/icon registered below can be re-applied.
        saved_theme = self.app_settings.value("theme", "light")
        if saved_theme in ("light", "dark"):
            theme.set_theme(saved_theme)
        self._themed_styles: list = []   # (widget, style_builder) pairs
        self._themed_icons: list = []    # (button, rel_path, color_token, size) tuples
        self._themed_repaint: list = []  # widgets whose paintEvent uses palette
        theme.register_theme_listener(self._apply_theme)

        self.thumbnails_loading = False
        self.thumbnail_worker = None
        self.add_thumbnail_worker = None
        self._vibe_refresh_worker = None
        self._lut_cache: dict = {}
        # The LUT ref currently uploaded to the GPU / set on the processor.
        # Track it separately to avoid redundant uploads between frames.
        self._active_lut_ref = None

        # Run pre-1.5 → 1.5.0 vibe-state migration once. The report (if
        # any) is stashed for the post-window-shown notice; vibes loaded
        # here are not directly used (the editor reads via _vibe_for) but
        # calling migrate_and_load is what triggers the on-disk rewrite.
        _, self._migration_report = vibe_state.migrate_and_load()

        # LUT is loaded by ImageProcessor from current_vibe.lut_ref
        # (factory:<id> or user:<path>). No path argument anymore — the
        # processor never reads filesystem paths from the editor.
        self.processor = ImageProcessor(
            vibe=self.current_vibe,
            adjustments=ImageAdjustments(),
        )

        self._render_worker = RenderWorker(self.processor)
        self._render_worker.render_done.connect(self._on_render_done)
        self._render_worker.start()
        self._render_needs_commit = False  # True after slider release

        self.init_ui()

        self.debug_panel = DebugPanel(self.processor, self)
        self._on_vibe_selected(self.vibe_picker._current)
        self.debug_panel.hide()

        screen = QApplication.primaryScreen().geometry()
        main_geo = self.geometry()
        debug_x = main_geo.right() + 20
        if debug_x + 400 > screen.width():
            debug_x = main_geo.left() - 420
        self.debug_panel.move(max(0, debug_x), main_geo.y())

        QApplication.instance().installEventFilter(self)

        # Defer the post-migration notice until the main window has had a
        # chance to render; singleShot(0) puts it at the back of the next
        # event-loop tick, after show().
        if self._migration_report is not None:
            QTimer.singleShot(0, self._show_migration_notice)

        # Probe how the GPU resolved once the window is up. Init is lazy and a
        # little slow, so defer it off the constructor; if we landed on a
        # software adapter or the CPU fallback, surface it instead of letting
        # the user wonder why renders crawl on capable hardware.
        QTimer.singleShot(0, self._check_gpu_health)

    def _check_gpu_health(self):
        try:
            status = gpu.status()
        except Exception:
            log.exception("[gpu] status probe failed")
            return
        if status['mode'] == 'gpu':
            return
        if status.get('forced'):
            msg = ("CPU-only mode (LOFILOGIC_FORCE_CPU) — GPU acceleration is "
                   "disabled for debugging. Renders will be slow.")
        elif status['mode'] == 'software':
            msg = (f"GPU not in use — running on a software renderer "
                   f"({status['summary']}). Renders will be slow; update your "
                   f"graphics drivers.")
        elif not status['available']:
            msg = ("GPU acceleration off — the 'wgpu' library is not installed. "
                   "Renders will be slow. Install dependencies: "
                   "pip install -r requirements.txt")
        else:
            msg = ("GPU not in use — running on the CPU fallback. Renders will "
                   "be slow; check that GPU drivers are installed and current.")
        log.warning("[gpu] %s", msg)
        if hasattr(self, 'mode_label'):
            self.mode_label.setText("⚠ " + msg)
            self.mode_label.setStyleSheet(f"color: {C['accent']};")

    def _show_migration_notice(self):
        """Show the one-shot post-migration summary dialog (step 6).

        Dismissal persists in the v2 envelope via
        vibe_state.mark_migration_acknowledged so the dialog never fires
        twice for the same migration."""
        from .migration_notice import MigrationNoticeDialog
        dlg = MigrationNoticeDialog(self._migration_report, parent=self)
        dlg.show()  # non-modal — user can keep working with the editor
        self._migration_notice_dialog = dlg  # keep a ref so it isn't GC'd

    # ===================================================================
    # ROTATION
    # ===================================================================

    def rotate_clockwise(self):
        if not self.image_files:
            return
        if hasattr(self, '_render_worker'):
            self._render_worker.invalidate()
        img_array = self.processor.rotate_clockwise()
        self.display_image(img_array)
        self.update_current_thumbnail(img_array)
        file_path = str(self.image_files[self.current_index])
        self.image_cache[file_path] = self.processor.intermediate_acescg.copy()
        self.image_rotations[file_path] = (self.image_rotations.get(file_path, 0) + 90) % 360

    def rotate_counterclockwise(self):
        if not self.image_files:
            return
        if hasattr(self, '_render_worker'):
            self._render_worker.invalidate()
        img_array = self.processor.rotate_counterclockwise()
        self.display_image(img_array)
        self.update_current_thumbnail(img_array)
        file_path = str(self.image_files[self.current_index])
        self.image_cache[file_path] = self.processor.intermediate_acescg.copy()
        self.image_rotations[file_path] = (self.image_rotations.get(file_path, 0) - 90) % 360

    # ===================================================================
    # LUT LOADING
    # ===================================================================

    def _load_custom_lut(self):
        """Prompt for a .cube file. Stored on the current vibe as a
        `user:<absolute path>` ref so it becomes part of the active vibe's
        session state and can be saved with it."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select LUT", "", "LUT Files (*.cube)"
        )
        if not file_path:
            return
        try:
            custom_lut = colour.io.read_LUT(file_path)
            self._lut_cache[file_path] = custom_lut
            self.processor.lut = custom_lut
            gpu.upload_lut(custom_lut.table)
            self.current_vibe.lut_ref = f"user:{file_path}"
            self._active_lut_ref = self.current_vibe.lut_ref
            # User just imported a fresh LUT — any preserved pre-1.5 path
            # is no longer the active choice, so drop the breadcrumb.
            self.current_vibe.legacy_user_lut = ''
            self.debug_panel.refresh_lut_label()
            self.debug_panel.update_modified_indicator()
            self.refresh_from_debug()
        except Exception as e:
            QMessageBox.warning(self, "LUT Load Error", f"Failed to parse LUT file:\n{e}")

    # ===================================================================
    # EVENT FILTER (arrow key navigation + double-click sliders to reset)
    # ===================================================================

    def eventFilter(self, source, event):
        if event.type() == QEvent.Type.KeyPress:
            # Always route arrow keys to image navigation/rotation, regardless
            # of which widget has focus.  Guard against modal dialogs (file
            # picker, message boxes) and unrelated windows.
            active = QApplication.activeWindow()
            if active is self:
                key = event.key()
                if key == Qt.Key_Left:
                    if self.image_files and self.current_index > 0:
                        self.current_index -= 1
                        self.load_current_image()
                    return True
                elif key == Qt.Key_Right:
                    if self.image_files and self.current_index < len(self.image_files) - 1:
                        self.current_index += 1
                        self.load_current_image()
                    return True
                elif key == Qt.Key_Up:
                    if self.image_files:
                        self.rotate_clockwise()
                    return True
                elif key == Qt.Key_Down:
                    if self.image_files:
                        self.rotate_counterclockwise()
                    return True

        if event.type() == QEvent.Type.MouseButtonDblClick:
            if source == getattr(self, 'slider_intensity', None):
                self.reset_all_sliders()
                return True
        return super().eventFilter(source, event)

    # ===================================================================
    # THEME HELPERS
    # ===================================================================

    def _themed(self, widget, style_builder):
        """Apply `style_builder()` now and remember it for theme swaps."""
        widget.setStyleSheet(style_builder())
        self._themed_styles.append((widget, style_builder))
        return widget

    def _themed_icon(self, button, rel_path, color_token="text_label", size=14):
        """Set an SVG icon on a button and remember it for re-tinting."""
        button.setIcon(svg_icon(rel_path, color_token, size))
        button.setIconSize(QSize(size, size))
        self._themed_icons.append((button, rel_path, color_token, size))
        return button

    def _apply_theme(self):
        """Re-run every registered stylesheet and icon with the current palette.

        Each refresh step is guarded so one bad widget can't abort the whole
        palette swap; failures are logged at debug (off by default) so a
        systematic breakage is still diagnosable instead of fully silent.
        """
        for widget, builder in self._themed_styles:
            try:
                widget.setStyleSheet(builder())
            except Exception:
                log.debug("theme: stylesheet refresh failed for %r", widget, exc_info=True)
        for button, rel_path, color_token, size in self._themed_icons:
            try:
                button.setIcon(svg_icon(rel_path, color_token, size))
            except Exception:
                log.debug("theme: icon refresh failed for %s", rel_path, exc_info=True)
        # Regenerate drag-overlay strings (they hold cached accent/text colours)
        if hasattr(self, "_rebuild_drag_styles"):
            self._rebuild_drag_styles()
        # Dynamic styles (format pills, mode label, process-button-done, etc.)
        # aren't registered — they're reapplied by their owners on the next
        # state change. Trigger that here so the palette swap is immediate.
        if hasattr(self, "btn_export_jpeg") and hasattr(self, "export_mode"):
            try:
                self.set_export_mode(self.export_mode)
            except Exception:
                log.debug("theme: export-mode restyle failed", exc_info=True)
        if hasattr(self, "mode_label"):
            try:
                self.update_mode_label()
            except Exception:
                log.debug("theme: mode-label restyle failed", exc_info=True)
        # Force a repaint on widgets that read the palette inside paintEvent
        for w in self._themed_repaint:
            try:
                w.update()
            except Exception:
                log.debug("theme: repaint failed for %r", w, exc_info=True)
        # Apple/Windows native chrome needs to follow the theme too
        if getattr(self, "_native_chrome_applied", False):
            try:
                from ui import native_chrome
                native_chrome.apply(self, theme.current_theme())
            except Exception:
                log.debug("theme: native chrome refresh failed", exc_info=True)

    def set_dng_profile_name(self, name: str):
        self.current_vibe.dng_profile_name = name
        self.app_settings.setValue("dng_profile_name", name)

    def toggle_theme(self):
        new_name = theme.toggle_theme()   # listeners fire → _apply_theme()
        self.app_settings.setValue("theme", new_name)
        self._refresh_theme_toggle_icon()

    def _refresh_theme_toggle_icon(self):
        btn = getattr(self, "btn_theme_toggle", None)
        if btn is None:
            return
        # Unicode glyphs sidestep the need for bundled sun/moon SVGs.
        btn.setText("☀" if theme.current_theme() == "dark" else "☾")

        btn.setText("LIGHT" if theme.current_theme() == "dark" else "DARK")

    def _rebuild_drag_styles(self):
        """Rebuild the cached drag-overlay stylesheets with current palette values."""
        accent = C['accent']
        text_dim = C['text_dim']
        self._drag_style_active = (
            f"QFrame {{ background: rgba(0,0,0,0.55); border: 2px dashed {accent}; border-radius: 8px; }}"
            f"QLabel {{ color: {accent}; font-size: 16px; font-weight: 600; background: transparent; border: none; }}"
        )
        self._drag_style_dim = (
            f"QFrame {{ background: rgba(0,0,0,0.35); border: 2px dashed {text_dim}; border-radius: 8px; }}"
            f"QLabel {{ color: {text_dim}; font-size: 14px; font-weight: 500; background: transparent; border: none; }}"
        )
        # If they're currently visible, refresh whichever one is shown.
        if hasattr(self, "drag_overlay"):
            self.drag_overlay.setStyleSheet(self._drag_style_active)
        if hasattr(self, "drag_overlay_add"):
            self.drag_overlay_add.setStyleSheet(self._drag_style_dim)

    # ===================================================================
    # UI CONSTRUCTION
    # ===================================================================

    def init_ui(self):
        self.setWindowTitle("LoFi Logic")
        self.resize(1200, 760)
        QTimer.singleShot(0, self.center_window)

        theme.load_app_fonts()
        app_font = theme.ui_font(10, QFont.Normal)
        self.setFont(app_font)

        main_widget = QWidget()
        main_widget.setObjectName("MainWidget")
        main_widget.setAttribute(Qt.WA_StyledBackground, True)
        self._themed(
            main_widget,
            lambda: f"QWidget#MainWidget {{ background-color: {C['bg_window']}; }}",
        )
        self.setCentralWidget(main_widget)
        self.setAcceptDrops(True)

        # Drag overlays — strings are rebuilt on each theme change so their
        # accent/text colours stay in sync.
        self.drag_overlay = QFrame(main_widget)
        drag_layout = QVBoxLayout(self.drag_overlay)
        self._drag_label = QLabel("Drop Sony P43 JPEGs or other photos here")
        self._drag_label.setAlignment(Qt.AlignCenter)
        drag_layout.addWidget(self._drag_label)
        self.drag_overlay.hide()

        self.drag_overlay_add = QFrame(main_widget)
        add_drag_layout = QVBoxLayout(self.drag_overlay_add)
        self._add_drag_label = QLabel("Add images")
        self._add_drag_label.setAlignment(Qt.AlignCenter)
        add_drag_layout.addWidget(self._add_drag_label)
        self.drag_overlay_add.hide()
        self._rebuild_drag_styles()

        # === Root vertical layout: [toolbar | body | filmstrip | statusbar]
        root = QVBoxLayout(main_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ─────────────── SUB-TOOLBAR ───────────────
        root.addWidget(self._build_sub_toolbar())

        # ─────────────── BODY: image + right rail ───────────────
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Image column
        image_col = QWidget()
        image_col.setObjectName("ImageCol")
        image_col.setAttribute(Qt.WA_StyledBackground, True)
        self._themed(
            image_col,
            lambda: f"QWidget#ImageCol {{ background: {C['bg_canvas']}; }}",
        )
        image_col_layout = QVBoxLayout(image_col)
        image_col_layout.setContentsMargins(24, 20, 24, 12)
        image_col_layout.setSpacing(12)

        self.image_label = ZoomableImageWidget()
        self.image_label.setMinimumSize(640, 480)
        self.image_label.compare_pressed.connect(self.show_before_compare)
        self.image_label.compare_released.connect(self.end_before_compare)
        image_col_layout.addWidget(self.image_label, 1)

        image_col_layout.addWidget(self._build_image_meta_row())
        body_layout.addWidget(image_col, 1)

        # Right rail
        body_layout.addWidget(self._build_right_rail())
        root.addWidget(body, 1)

        # ─────────────── FILMSTRIP ───────────────
        filmstrip = QWidget()
        filmstrip.setObjectName("Filmstrip")
        filmstrip.setAttribute(Qt.WA_StyledBackground, True)
        self._themed(
            filmstrip,
            lambda: (
                f"QWidget#Filmstrip {{"
                f"  background: {C['bg_strip']};"
                f"  border-top: 1px solid {C['border_soft']};"
                f"}}"
            ),
        )
        filmstrip.setFixedHeight(88)
        fl = QHBoxLayout(filmstrip)
        fl.setContentsMargins(18, 8, 18, 8)
        fl.setSpacing(14)

        roll_meta = QWidget()
        roll_meta.setFixedWidth(78)
        roll_v = QVBoxLayout(roll_meta)
        roll_v.setContentsMargins(0, 6, 0, 5)
        roll_v.setSpacing(3)
        roll_title = QLabel("BATCH")
        roll_title.setFont(theme.ui_font(9, QFont.DemiBold))
        self._themed(roll_title, lambda: f"color: {C['text_dim']}; letter-spacing: 1.2px;")
        self.label_roll_count = QLabel("0 PHOTOS")
        self.label_roll_count.setFont(theme.mono_font(9, QFont.Medium))
        self._themed(self.label_roll_count, lambda: f"color: {C['text_secondary']};")
        roll_v.addWidget(roll_title)
        roll_v.addWidget(self.label_roll_count)
        roll_v.addStretch(1)
        fl.addWidget(roll_meta)

        self.thumbnail_strip = ThumbnailStrip()
        self.thumbnail_strip.thumbnail_clicked.connect(self.on_thumbnail_click)
        self.thumbnail_strip.thumbnail_right_clicked.connect(self.on_thumbnail_right_click)
        self.thumbnail_strip.thumbnail_paste_selected.connect(self.on_thumbnail_paste_selected)
        self.thumbnail_strip.thumbnail_remove_requested.connect(self.remove_from_project)
        fl.addWidget(self.thumbnail_strip)

        self.fade_overlay = FadeOverlayWidget(self.thumbnail_strip)
        root.addWidget(filmstrip)

        # ─────────────── STATUS BAR ───────────────
        root.addWidget(self._build_status_bar())

        self.loader_overlay = LoaderOverlay(self.centralWidget())
        self.settings_clipboard = None

        # ⌘R resets all sliders
        reset_sc = QAction(self)
        reset_sc.setShortcut(QKeySequence("Ctrl+R"))
        reset_sc.triggered.connect(self.reset_all_sliders)
        self.addAction(reset_sc)

        # Number keys select the first nine catalog presets.
        for key, row in zip('123456789', self.vibe_picker.VIBES):
            vibe_id = row[0]
            sc = QAction(self)
            sc.setShortcut(QKeySequence(key))
            sc.triggered.connect(lambda _=False, v=vibe_id: self.vibe_picker.set_vibe(v))
            self.addAction(sc)

        self._build_menu_bar()
        self._on_vibe_selected('funsaver_800')

    # ── sub-toolbar ─────────────────────────────────────────────────────
    def _build_sub_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("SubToolbar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(56)
        self._themed(
            bar,
            lambda: (
                f"QWidget#SubToolbar {{"
                f"  background: {C['bg_window']};"
                f"  border-bottom: 1px solid {C['border_soft']};"
                f"}}"
            ),
        )
        l = QHBoxLayout(bar)
        l.setContentsMargins(18, 0, 16, 0)
        l.setSpacing(8)

        brand = QWidget()
        brand_l = QHBoxLayout(brand)
        brand_l.setContentsMargins(0, 0, 12, 0)
        brand_l.setSpacing(8)
        brand_mark = QLabel("●")
        brand_mark.setFont(theme.ui_font(13, QFont.DemiBold))
        self._themed(brand_mark, lambda: f"color: {C['accent']};")
        brand_name = QLabel("LOFI LOGIC")
        brand_name.setFont(theme.ui_font(10, QFont.DemiBold))
        self._themed(brand_name, lambda: f"color: {C['text_primary']}; letter-spacing: 1.2px;")
        brand_l.addWidget(brand_mark)
        brand_l.addWidget(brand_name)
        l.addWidget(brand)

        def icon_btn(tooltip, svg_name=None, text=None, size=28):
            b = QPushButton()
            b.setFixedSize(size, size)
            b.setToolTip(tooltip)
            b.setCursor(Qt.PointingHandCursor)
            self._themed(b, lambda s=size: icon_btn_qss(s, 4))
            if svg_name:
                self._themed_icon(b, svg_name, "text_label", 14)
            elif text:
                b.setText(text)
                f = theme.ui_font(13, QFont.Medium)
                b.setFont(f)
            return b

        def action_btn(label, tooltip, svg_name):
            b = QPushButton(label)
            b.setToolTip(tooltip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(34)
            b.setFont(theme.ui_font(10, QFont.Medium))
            self._themed_icon(b, svg_name, "text_label", 14)
            self._themed(b, lambda: (
                f"QPushButton {{ color: {C['text_secondary']}; background: {C['bg_input']};"
                f" border: 1px solid {C['border_input']}; border-radius: 6px; padding: 0 12px; }}"
                f"QPushButton:hover {{ color: {C['text_primary']}; background: {C['bg_input_hover']};"
                f" border-color: {C['border_active']}; }}"
                f"QPushButton:pressed {{ background: {C['bg_input_active']}; }}"
            ))
            return b

        self.btn_open = action_btn("Open photos", "Open a folder or image files (Ctrl+O)", "assets/icons/folder.svg")
        self.btn_open.clicked.connect(self.open_files)
        l.addWidget(self.btn_open)

        self.btn_open_folder = action_btn(
            "Open P43 folder", "Open every supported photo in a camera folder",
            "assets/icons/camera.svg")
        self.btn_open_folder.clicked.connect(self.open_photo_folder)
        l.addWidget(self.btn_open_folder)

        l.addStretch(1)

        interaction_hint = QLabel("HOLD PREVIEW  BEFORE  ·  SCROLL  ZOOM  ·  DOUBLE-CLICK  FIT")
        interaction_hint.setFont(theme.mono_font(8, QFont.Medium))
        self._themed(interaction_hint, lambda: f"color: {C['text_dim']}; letter-spacing: 0.4px;")
        l.addWidget(interaction_hint)
        l.addSpacing(8)

        # Destination label avoids font-dependent sun/moon glyphs on Windows.
        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setFixedSize(52, 30)
        self.btn_theme_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_theme_toggle.setToolTip("Toggle light / dark theme")
        self.btn_theme_toggle.setFont(theme.ui_font(8, QFont.DemiBold))
        self._themed(self.btn_theme_toggle, lambda: (
            f"QPushButton {{ color: {C['text_dim']}; background: transparent; border: none;"
            " border-radius: 5px; letter-spacing: 0.8px; }}"
            f"QPushButton:hover {{ color: {C['text_primary']}; background: {C['bg_input_hover']}; }}"
        ))
        self.btn_theme_toggle.clicked.connect(self.toggle_theme)
        l.addWidget(self.btn_theme_toggle)
        self._refresh_theme_toggle_icon()

        return bar

    # ── image meta row (under the image) ────────────────────────────────
    def _build_image_meta_row(self) -> QWidget:
        row = QWidget()
        row.setObjectName("ImageMetaRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setFixedHeight(34)
        self._themed(
            row,
            lambda: (
                "QWidget#ImageMetaRow { background: rgba(255, 255, 255, 0.045);"
                " border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 7px; }"
            ),
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 0, 7, 0)
        rl.setSpacing(8)

        self.label_filename = QLabel("")
        self.label_filename.setFont(theme.ui_font(11, QFont.Medium))
        self._themed(self.label_filename, lambda: f"color: {C['text_canvas']};")
        rl.addWidget(self.label_filename)

        self.label_input_kind = QLabel("")
        self.label_input_kind.setFont(theme.mono_font(8, QFont.DemiBold))
        self.label_input_kind.setContentsMargins(6, 2, 6, 2)
        rl.addWidget(self.label_input_kind)

        rl.addStretch(1)

        def small_btn(arrow, tooltip, slot):
            b = QPushButton(arrow)
            b.setFixedSize(24, 24)
            b.setFont(theme.ui_font(13, QFont.Medium))
            b.setToolTip(tooltip)
            b.setCursor(Qt.PointingHandCursor)
            self._themed(b, lambda: (
                f"QPushButton {{ background: transparent; border: none; border-radius: 4px;"
                f" color: {C['text_canvas_dim']}; min-width: 24px; max-width: 24px;"
                " min-height: 24px; max-height: 24px; padding: 0; }"
                "QPushButton:hover { background: rgba(255,255,255,0.08); color: #ffffff; }"
                "QPushButton:pressed { background: rgba(255,255,255,0.13); }"
            ))
            b.clicked.connect(slot)
            return b

        # rotate buttons — pulled down from the toolbar to shorten travel
        self.btn_rotate_ccw = small_btn("↺", "Rotate left (↓)", self.rotate_counterclockwise)
        self.btn_rotate_cw = small_btn("↻", "Rotate right (↑)", self.rotate_clockwise)
        rl.addWidget(self.btn_rotate_ccw)
        rl.addWidget(self.btn_rotate_cw)

        rl.addSpacing(12)

        # counter
        self.label_counter = QLabel("0 / 0")
        self.label_counter.setFont(theme.mono_font(10, QFont.Medium))
        self._themed(self.label_counter, lambda: f"color: {C['text_canvas_dim']};")
        rl.addWidget(self.label_counter)

        rl.addSpacing(8)

        self.btn_prev_image = small_btn("‹", "Previous (←)", self.prev_image)
        self.btn_next_image = small_btn("›", "Next (→)", self.next_image)
        rl.addWidget(self.btn_prev_image)
        rl.addWidget(self.btn_next_image)
        return row

    # ── right rail ──────────────────────────────────────────────────────
    def _build_right_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("RightRail")
        rail.setAttribute(Qt.WA_StyledBackground, True)
        rail.setFixedWidth(340)
        self._themed(
            rail,
            lambda: (
                f"QWidget#RightRail {{"
                f"  background: {C['bg_rail']};"
                f"  border-left: 1px solid {C['border_soft']};"
                f"}}"
            ),
        )
        v = QVBoxLayout(rail)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_vibe_section())
        v.addWidget(self._divider())
        v.addWidget(self._build_intensity_section())
        v.addStretch(1)
        v.addWidget(self._divider())
        v.addWidget(self._build_export_footer())
        return rail

    def _build_intensity_section(self) -> QWidget:
        """The only creative adjustment in the preset-first product."""
        sec = QWidget()
        v = QVBoxLayout(sec)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        reset_link = QPushButton("Reset")
        self._themed(reset_link, lambda: section_reset_link_qss())
        reset_link.setCursor(Qt.PointingHandCursor)
        reset_link.clicked.connect(self.reset_all_sliders)
        v.addWidget(self._section_header("FILTER INTENSITY", reset_link))

        self.label_intensity = QLabel("100%")
        self.label_intensity.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider_intensity = ScrubSlider(dual=False)
        self.slider_intensity.setRange(0, 100)
        self.slider_intensity.setValue(100)
        self.slider_intensity.valueChanged.connect(self.on_filter_intensity_moved)
        self.slider_intensity.sliderReleased.connect(self.on_filter_intensity_released)
        self.slider_intensity.installEventFilter(self)
        v.addWidget(self._slider_row("AMOUNT", self.label_intensity, self.slider_intensity))

        hint = QLabel("0% is a clean calibrated render · 100% is the full preset")
        hint.setWordWrap(True)
        hint.setFont(theme.ui_font(9, QFont.Normal))
        self._themed(hint, lambda: f"color: {C['text_dim']};")
        v.addWidget(hint)
        return sec

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFixedHeight(1)
        self._themed(d, lambda: f"background: {C['border_soft']};")
        return d

    def _section_header(self, title: str, aside: QWidget = None) -> QWidget:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        lbl = QLabel(title)
        self._themed(lbl, lambda: section_title_qss())
        hl.addWidget(lbl)
        hl.addStretch(1)
        if aside is not None:
            hl.addWidget(aside)
        return w

    def _slider_row(self, label_text: str, value_label: QLabel, slider: ScrubSlider) -> QWidget:
        """Label row + slider as a single column."""
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        l = QLabel(label_text)
        l.setFont(theme.ui_font(11, QFont.Medium))
        self._themed(
            l,
            lambda: f"color: {C['text_label']}; letter-spacing: 0.4px;",
        )
        header.addWidget(l)
        header.addStretch(1)
        value_label.setFont(theme.mono_font(12, QFont.Medium))
        self._themed(
            value_label,
            lambda: f"color: {C['text_secondary']}; padding: 2px 4px;",
        )
        header.addWidget(value_label)

        header_w = QWidget()
        header_w.setLayout(header)
        bl.addWidget(header_w)
        bl.addWidget(slider)
        return box

    def _build_vibe_section(self) -> QWidget:
        sec = QWidget()
        v = QVBoxLayout(sec)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        v.addWidget(self._section_header("CAMERA LOOK"))

        self.vibe_picker = VibePicker()
        self.vibe_picker.vibe_changed.connect(self._on_vibe_selected)
        v.addWidget(self.vibe_picker)

        hint = QLabel("Click to browse by camera family · keys 1–9 select favourites")
        hint.setWordWrap(True)
        hint.setFont(theme.ui_font(9, QFont.Normal))
        self._themed(hint, lambda: f"color: {C['text_dim']};")
        v.addWidget(hint)
        return sec

    def _on_vibe_selected(self, vibe_id: str):
        """Apply a preset to the selected frame, not to the entire roll."""
        vibe = self._vibe_for(vibe_id)
        self._apply_vibe(vibe_id, vibe, refresh_thumbnails=False)
        self.save_current_settings()
        self.update_current_thumbnail()

    def _vibe_for(self, vibe_id: str) -> VibeConfig:
        """Saved VibeConfig if present for `vibe_id`, otherwise the factory recipe."""
        vibe_id = normalize_preset_id(vibe_id)
        if vibe_id not in VIBE_PRESETS:
            vibe_id = 'funsaver_800'
        saved = vibe_state.load_all().get(vibe_id)
        if saved is not None:
            return saved
        return vibe_config_for(vibe_id)

    def _apply_vibe(self, vibe_id: str, vibe: VibeConfig,
                    refresh_thumbnails: bool = False, render: bool = True):
        """Install `vibe` as the active vibe: bind it to the processor, load
        its LUT, sync the debug panel, and refresh the preview."""
        original_id = vibe_id
        vibe_id = normalize_preset_id(vibe_id)
        if original_id != vibe_id:
            vibe = self._vibe_for(vibe_id)
        if vibe_id not in VIBE_PRESETS:
            vibe_id = 'funsaver_800'
        if hasattr(self, '_render_worker'):
            self._render_worker.invalidate()
        # Preserve the persistent profile name across vibe swaps — it's an
        # app-wide preference, not a per-vibe field.
        profile_name = self.current_vibe.dng_profile_name
        self.current_vibe = vibe
        self.current_vibe.dng_profile_name = profile_name
        self.processor.vibe = self.current_vibe
        # Tag the per-image record with the new vibe id (for future Save Project).
        self.processor.adjustments.active_vibe_id = vibe_id
        self._apply_effective_lut()
        if hasattr(self, 'debug_panel'):
            self.debug_panel.sync_from_config()
            self.debug_panel.update_modified_indicator()
        if render:
            self.refresh_from_debug()
        if refresh_thumbnails:
            self._refresh_all_thumbnails()

    def _apply_effective_lut(self, file_path: str = None):
        """Load the selected ONE35 V2 preset LUT unless already active."""
        if file_path is None and self.image_files:
            file_path = str(self.image_files[self.current_index])
        base = self.current_vibe.lut_ref
        eff = base
        if eff == self._active_lut_ref:
            return
        self._load_lut_from_ref(eff, persist=(eff == base))

    def _lut_obj(self, ref: str):
        """Resolve a tagged LUT ref to a cached `colour` LUT object, or None."""
        from core.config import resolve_lut_ref
        if not ref:
            return None
        resolved, _ = resolve_lut_ref(ref)
        if not resolved:
            return None
        if resolved not in self._lut_cache:
            try:
                self._lut_cache[resolved] = colour.io.read_LUT(resolved)
            except Exception as e:
                log.warning("⚠ Could not load LUT '%s': %s", resolved, e)
                return None
        return self._lut_cache[resolved]

    def _v1_variant_lut(self):
        """Compatibility hook retained for workers; V1 inputs are unsupported."""
        return None

    def _load_lut_from_ref(self, lut_ref: str, persist: bool = True):
        """Resolve a tagged LUT ref (`factory:<id>` or `user:<path>`) to an
        absolute path via core.config.resolve_lut_ref, load + cache, push
        into processor + GPU. Empty ref clears the LUT so the tone-curve
        fallback renders.

        A `user:` ref whose file no longer exists falls back to the LUT
        the vibe normally ships with (whichever factory id matches the
        active vibe), so a missing custom LUT doesn't degrade further than
        the factory look. The post-migration / startup summary handles
        surfacing this to the user.
        """
        from core.config import resolve_lut_ref, vibe_config_for, LUT_REF_FACTORY
        if not lut_ref:
            self.processor.lut = None
            self._active_lut_ref = None
            return
        resolved, origin = resolve_lut_ref(lut_ref)
        if resolved is None and origin == 'user':
            log.warning("⚠ Custom LUT missing: %s. Falling back to factory LUT.", lut_ref)
            try:
                vibe_id = self.current_vibe_id()
                fallback_ref = vibe_config_for(vibe_id).lut_ref
            except (KeyError, AttributeError):
                fallback_ref = ''
            if fallback_ref and fallback_ref != lut_ref:
                self._load_lut_from_ref(fallback_ref, persist=persist)
            else:
                self.processor.lut = None
                self._active_lut_ref = None
            return
        if resolved is None:
            log.warning("⚠ Could not resolve LUT ref %r", lut_ref)
            self.processor.lut = None
            self._active_lut_ref = None
            return
        try:
            if resolved not in self._lut_cache:
                self._lut_cache[resolved] = colour.io.read_LUT(resolved)
            lut = self._lut_cache[resolved]
            self.processor.lut = lut
            gpu.upload_lut(lut.table)
            self._active_lut_ref = lut_ref
            if persist:
                self.current_vibe.lut_ref = lut_ref
        except Exception as e:
            log.warning("⚠ Could not load LUT '%s': %s", resolved, e)

    # -------------------------------------------------------------------
    # Per-vibe save / reset
    # -------------------------------------------------------------------

    def current_vibe_id(self) -> str:
        return self.vibe_picker.current_vibe()

    def save_current_vibe_defaults(self):
        """Promote the live VibeConfig to saved defaults for the active vibe."""
        vibe_id = self.current_vibe_id()
        vibe_state.save_one(vibe_id, self.current_vibe)
        if hasattr(self, 'debug_panel'):
            self.debug_panel.update_modified_indicator()
            self.debug_panel.status_label.setText(f"Saved defaults for {vibe_id}.")

    def reset_current_vibe_to_saved(self):
        """Discard session edits, reload saved defaults (or factory if no saved)."""
        vibe_id = self.current_vibe_id()
        self._apply_vibe(vibe_id, self._vibe_for(vibe_id), refresh_thumbnails=True)
        if hasattr(self, 'debug_panel'):
            label = "saved" if vibe_state.has_saved(vibe_id) else "factory (no saved defaults)"
            self.debug_panel.status_label.setText(f"Reset {vibe_id} to {label}.")

    def reset_current_vibe_to_factory(self):
        """Wipe saved defaults for the active vibe and apply factory state."""
        vibe_id = self.current_vibe_id()
        vibe_state.clear_one(vibe_id)
        self._apply_vibe(vibe_id, vibe_config_for(vibe_id), refresh_thumbnails=True)
        if hasattr(self, 'debug_panel'):
            self.debug_panel.status_label.setText(f"Reset {vibe_id} to factory defaults.")

    _DEFAULT_USER_SETTINGS = {'exposure_ev': 0.0, 'wb_temp': 0, 'tint': 0.0, 'push_pull_ev': 0.0}

    def _refresh_all_thumbnails(self):
        """Re-render every cached thumbnail in the background after a vibe change."""
        if not self.image_files:
            return

        # Stop any in-flight refresh before starting a new one.
        self._stop_vibe_refresh_worker()

        # Snapshot cache keys on the main thread so the worker never iterates the
        # live dict. Share the array references (shallow) rather than deep-copying
        # every intermediate — that copy duplicated the entire cache (gigabytes)
        # into the worker. Safe because cache values are never mutated in place
        # (always replaced) and the worker copies each array before rendering.
        cache_snapshot = dict(self.image_cache.items())

        self._vibe_refresh_worker = VibeRefreshWorker(
            image_files=self.image_files,
            cache_snapshot=cache_snapshot,
            image_settings=self.image_settings.copy(),
            current_index=self.current_index,
            lut=self._lut_obj(self.current_vibe.lut_ref),
            grain_tiles=self.processor.grain_tiles,
            default_settings=self._DEFAULT_USER_SETTINGS.copy(),
            vibe=self.current_vibe.copy(),
            lut_v1=self._v1_variant_lut(),
        )
        self._vibe_refresh_worker.thumbnail_ready.connect(self._on_vibe_refresh_thumbnail)
        self._vibe_refresh_worker.start()

    def _on_vibe_refresh_thumbnail(self, index, thumb_array):
        file_path = str(self.image_files[index])
        self.thumbnail_cache[file_path] = thumb_array
        self.thumbnail_strip.update_thumbnail(index, thumb_array)

    def _build_export_footer(self) -> QWidget:
        sec = QWidget()
        sec.setObjectName("ExportFooter")
        sec.setAttribute(Qt.WA_StyledBackground, True)
        self._themed(
            sec,
            lambda: (
                f"QWidget#ExportFooter {{ background: {C['bg_toolbar']};"
                f" border-top: 1px solid {C['border_soft']}; }}"
            ),
        )
        v = QVBoxLayout(sec)
        v.setContentsMargins(16, 15, 16, 16)
        v.setSpacing(9)

        v.addWidget(self._section_header("EXPORT"))

        self.btn_export_jpeg = QPushButton("JPEG · preserve camera metadata")
        self.btn_export_jpeg.setCheckable(True)
        self.btn_export_jpeg.setChecked(True)
        self.btn_export_jpeg.setCursor(Qt.PointingHandCursor)
        self.btn_export_jpeg.setFixedHeight(32)
        self.btn_export_jpeg.setToolTip(
            "Export a finished JPEG while preserving the source camera and capture metadata")
        self.btn_export_jpeg.clicked.connect(lambda: self.set_export_mode('jpeg'))
        v.addWidget(self.btn_export_jpeg)

        self.label_export_mode_help = QLabel("Finished image with the selected camera look")
        self.label_export_mode_help.setWordWrap(True)
        self.label_export_mode_help.setFont(theme.ui_font(9, QFont.Normal))
        self._themed(self.label_export_mode_help, lambda: f"color: {C['text_dim']};")
        v.addWidget(self.label_export_mode_help)

        # Output path row — single flat shape (no nested button outline)
        out_row = QWidget()
        self._themed(out_row, lambda: (
            f"QWidget {{"
            f"  background: {C['bg_input']};"
            f"  border: 1px solid {C['border_input']};"
            f"  border-radius: 6px;"
            f"}}"
            f"QLabel, QPushButton {{ background: transparent; border: none; }}"
        ))
        out_row.setFixedHeight(34)
        out_row.setCursor(Qt.PointingHandCursor)
        ol = QHBoxLayout(out_row)
        ol.setContentsMargins(8, 0, 8, 0)
        ol.setSpacing(6)

        folder_ico = QLabel()
        folder_ico.setPixmap(svg_icon("assets/icons/folder.svg", "text_label", 12).pixmap(12, 12))
        folder_ico.setCursor(Qt.PointingHandCursor)
        folder_ico.mousePressEvent = lambda _: self.select_output_dir()
        ol.addWidget(folder_ico)

        self.label_output = QLabel(self._short_output_path(self.output_dir))
        self.label_output.setFont(theme.mono_font(10, QFont.Medium))
        self._themed(
            self.label_output,
            lambda: f"color: {C['text_label']}; background: transparent;",
        )
        self.label_output.setWordWrap(False)
        self.label_output.setTextFormat(Qt.PlainText)
        self.label_output.setCursor(Qt.PointingHandCursor)
        self.label_output.setToolTip(self.output_dir)
        self.label_output.mousePressEvent = lambda _: self.select_output_dir()
        ol.addWidget(self.label_output, 1)
        v.addWidget(out_row)

        # Process button + thin progress bar above it
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setVisible(False)
        self._themed(self.progress_bar, lambda: (
            f"QProgressBar {{"
            f"  background: {C['border_input']};"
            f"  border: none; border-radius: 1px;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"  background: {C['accent']}; border-radius: 1px;"
            f"}}"
        ))
        v.addWidget(self.progress_bar)

        self.btn_process_all = QPushButton("Export 0 photos")
        self.btn_process_all.setEnabled(False)
        self.btn_process_all.setFixedHeight(44)
        self.btn_process_all.setCursor(Qt.PointingHandCursor)
        self._themed(self.btn_process_all, lambda: process_btn_qss())
        self.btn_process_all.clicked.connect(self.process_all_images)
        v.addWidget(self.btn_process_all)

        # Initialize pill state to default (JPEG)
        self.set_export_mode('jpeg')
        return sec

    @staticmethod
    def _short_output_path(directory: str) -> str:
        """Keep the destination recognizable inside the narrow export rail."""
        path = Path(directory)
        if path.parent.name and path.name:
            return f"{path.parent.name}  /  {path.name}"
        return str(path)

    # ── status bar ──────────────────────────────────────────────────────
    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(26)
        self._themed(bar, lambda: (
            f"QWidget#StatusBar {{"
            f"  background: {C['bg_strip']};"
            f"  border-top: 1px solid {C['border_soft']};"
            f"}}"
        ))
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)

        self.status_dot = QLabel("●")
        self._themed(self.status_dot, lambda: f"color: {C['processed']};")
        hl.addWidget(self.status_dot)

        self.mode_label = QLabel("Ready")
        self.mode_label.setFont(theme.mono_font(10, QFont.Medium))
        self._themed(self.mode_label, lambda: f"color: {C['text_dim']};")
        hl.addWidget(self.mode_label)

        hl.addStretch(1)

        hints = [
            ("← →", "PHOTOS"),
            ("RIGHT-CLICK", "SELECT EXPORT"),
            ("SHIFT-CLICK", "SELECT RANGE"),
        ]
        for keys, desc in hints:
            chip = QLabel(f"{keys}  {desc}")
            chip.setFont(theme.mono_font(8, QFont.Medium))
            self._themed(chip, lambda: f"color: {C['text_dim']};")
            hl.addWidget(chip)

        return bar

    def _build_menu_bar(self):
        """Build the native menu bar."""
        from _version import __version__
        mb = self.menuBar()
        self._themed(mb, lambda: (
            f"QMenuBar {{ background: {C['bg_window']}; color: {C['text_secondary']};"
            " border-bottom: 1px solid " + C['border_soft'] + "; padding: 2px 8px; }}"
            f"QMenuBar::item {{ background: transparent; padding: 5px 9px; border-radius: 4px; }}"
            f"QMenuBar::item:selected {{ background: {C['bg_input_hover']}; color: {C['text_primary']}; }}"
            f"QMenu {{ background: {C['bg_rail']}; color: {C['text_primary']};"
            f" border: 1px solid {C['border_input']}; padding: 6px; }}"
            f"QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background: {C['accent_soft']}; color: {C['text_primary']}; }}"
            f"QMenu::separator {{ height: 1px; background: {C['border_soft']}; margin: 5px 8px; }}"
        ))

        # ── File ──────────────────────────────────────────────────────
        file_menu = mb.addMenu("File")

        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)  # Cmd+O / Ctrl+O
        act_open.triggered.connect(self.open_files)
        file_menu.addAction(act_open)

        file_menu.addSeparator()

        act_open_project = QAction("Open Project…", self)
        act_open_project.setShortcut(QKeySequence("Ctrl+Shift+O"))
        act_open_project.triggered.connect(lambda: self.open_project())
        file_menu.addAction(act_open_project)

        self.recent_projects_menu = file_menu.addMenu("Open Recent Project")
        self.recent_projects_menu.aboutToShow.connect(self._rebuild_recent_projects_menu)
        # Initial population so the menu isn't empty before first show.
        self._rebuild_recent_projects_menu()

        act_save_project = QAction("Save Project", self)
        act_save_project.setShortcut(QKeySequence.StandardKey.Save)  # Cmd+S / Ctrl+S
        act_save_project.triggered.connect(self.save_project)
        file_menu.addAction(act_save_project)

        act_save_project_as = QAction("Save Project As…", self)
        act_save_project_as.setShortcut(QKeySequence.StandardKey.SaveAs)  # Cmd+Shift+S
        act_save_project_as.triggered.connect(self.save_project_as)
        file_menu.addAction(act_save_project_as)

        file_menu.addSeparator()

        act_export_jpg = QAction("Export JPGs", self)
        act_export_jpg.triggered.connect(self.export_as_jpeg)
        file_menu.addAction(act_export_jpg)

        act_output_dir = QAction("Set Output Directory…", self)
        act_output_dir.triggered.connect(self.select_output_dir)
        file_menu.addAction(act_output_dir)

        # ── Edit ──────────────────────────────────────────────────────
        # Note: macOS automatically appends "Start Dictation" and "Emoji & Symbols"
        # to any menu titled exactly "Edit". Naming it differently avoids that.
        edit_menu = mb.addMenu("Adjustments")

        act_copy = QAction("Copy Settings", self)
        act_copy.setShortcut(QKeySequence.StandardKey.Copy)   # Cmd+C / Ctrl+C
        act_copy.triggered.connect(self.copy_settings)
        edit_menu.addAction(act_copy)

        act_paste = QAction("Paste Settings", self)
        act_paste.setShortcut(QKeySequence.StandardKey.Paste)  # Cmd+V / Ctrl+V
        act_paste.triggered.connect(self.paste_settings)
        edit_menu.addAction(act_paste)

        act_select_all = QAction("Select All for Paste", self)
        act_select_all.setShortcut(QKeySequence.StandardKey.SelectAll)  # Cmd+A / Ctrl+A
        act_select_all.triggered.connect(self._menu_select_all_paste)
        edit_menu.addAction(act_select_all)

        act_deselect = QAction("Deselect All for Paste", self)
        act_deselect.setShortcut(QKeySequence("Ctrl+D"))  # Cmd+D on macOS
        act_deselect.triggered.connect(self._menu_deselect_all_paste)
        edit_menu.addAction(act_deselect)

        edit_menu.addSeparator()

        act_reset = QAction("Reset Settings", self)
        act_reset.triggered.connect(self.reset_all_sliders)
        edit_menu.addAction(act_reset)

        edit_menu.addSeparator()

        act_remove = QAction("Remove from Project", self)
        act_remove.setShortcut(QKeySequence(Qt.Key_Delete))
        act_remove.triggered.connect(self.remove_current_from_project)
        edit_menu.addAction(act_remove)

        # The preset-first product intentionally has no hidden editing mode.
        # Keeping navigation in one place mirrors the roll/gallery workflow.

        # AboutRole moves to the app menu automatically on macOS. On Windows
        # this final menu keeps the conventional File → Adjustments → Help order.
        help_menu = mb.addMenu("Help")
        act_about = QAction("About LoFi Logic", self)
        act_about.setMenuRole(QAction.MenuRole.AboutRole)
        act_about.triggered.connect(self.show_about)
        help_menu.addAction(act_about)

    # ───────────────────────────────────────────────────────────────────
    # MENU ACTIONS
    # ───────────────────────────────────────────────────────────────────

    def _char_icon(self, char, size=16):
        """Render a Unicode character as a QIcon for use in menus."""
        px = QPixmap(size, size)
        px.fill(Qt.transparent)
        painter = QPainter(px)
        color = QApplication.palette().color(QPalette.ColorRole.WindowText)
        painter.setPen(color)
        font = painter.font()
        font.setPixelSize(size)
        painter.setFont(font)
        painter.drawText(px.rect(), Qt.AlignCenter, char)
        painter.end()
        return QIcon(px)

    def show_about(self):
        from _version import __version__
        QMessageBox.about(
            self,
            "About LoFi Logic",
            f"<b>LoFi Logic</b><br>"
            f"Version {__version__}<br><br>"
            "A preset-first photo lab for the Sony Cyber-shot P43 "
            "and other compact cameras.<br><br>"
            "© 2026 LoFi Logic"
        )

    def _toggle_advanced_settings(self):
        if self.debug_panel.isVisible():
            self.debug_panel.hide()
        else:
            self.debug_panel.show()
            self.debug_panel.raise_()

    def _menu_select_all_paste(self):
        if not self.image_files:
            return
        self.thumbnail_strip.select_all_for_paste()
        count = len(self.thumbnail_strip.get_paste_selected_indices())
        self.mode_label.setText(f"{count} selected for paste")
        self.mode_label.setStyleSheet(f"color: {C['accent']};")
        QTimer.singleShot(2000, self.update_mode_label)

    def _menu_deselect_all_paste(self):
        self.thumbnail_strip.clear_paste_selection()
        self.mode_label.setText("Paste selection cleared")
        self.mode_label.setStyleSheet(f"color: {C['accent']};")
        QTimer.singleShot(1500, self.update_mode_label)

    def export_as_jpeg(self):
        self.set_export_mode('jpeg')
        self.process_all_images()

    def export_as_dng(self):
        # Kept as a compatibility hook for old shortcuts. The P43 product
        # exports finished JPEGs only.
        self.set_export_mode('jpeg')
        self.process_all_images()

    def export_lut_tiffs(self, output_dir, reverse_ae=False):
        """Export ACEScct TIFFs for all selected (or all) images.

        reverse_ae=False (default) exports at the app's standard exposure — the
        right input for previewing a hand-built LUT. reverse_ae=True normalises
        each frame by its EXIF shutter speed for real film-stock profiling.
        """
        if not self.image_files:
            return 0, 0

        selected_indices = self.thumbnail_strip.get_process_selected_indices()
        indices_to_process = sorted(selected_indices) if selected_indices else list(range(len(self.image_files)))
        total = len(indices_to_process)

        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.btn_process_all.setEnabled(False)

        success_count = 0
        for i, idx in enumerate(indices_to_process):
            file_path = str(self.image_files[idx])
            try:
                self.progress_bar.setValue(i)
                self.mode_label.setText(f"Exporting TIFF {i+1}/{total}…")
                QApplication.processEvents()

                # Always re-load from DNG: needed so _rev_gain_unconditional is
                # computed from this file's EXIF, not a previously cached image.
                self.processor.load_image(file_path)

                if file_path in self.image_settings:
                    self.processor.set_settings(self.image_settings[file_path])

                base_name = export_basename(file_path)
                suffix = "_lut_profile" if reverse_ae else "_standard"
                output_path = os.path.join(output_dir, f"{base_name}{suffix}.tif")
                if export_image(self.processor, output_path, as_tiff=True,
                                lut_profiling=True, reverse_ae=reverse_ae):
                    success_count += 1
            except Exception as e:
                log.error("Error exporting TIFF for %s: %s", file_path, e)
                traceback.print_exc()
            self.progress_bar.setValue(i + 1)
            QApplication.processEvents()

        self.progress_bar.setVisible(False)
        self.btn_process_all.setEnabled(True)
        self.load_current_image()
        self.update_mode_label()
        return success_count, total

    def center_window(self):
        frame_geo = self.frameGeometry()
        screen_geo = QApplication.primaryScreen().availableGeometry()
        frame_geo.moveCenter(screen_geo.center())
        self.move(frame_geo.topLeft())

    # ===================================================================
    # FILE MANAGEMENT
    # ===================================================================

    def open_photo_folder(self):
        """Open a copied camera folder without moving or modifying originals."""
        default_dir = (QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
                       or str(Path.home()))
        start_dir = self.app_settings.value("last_open_dir", default_dir)
        if not isinstance(start_dir, str) or not os.path.isdir(start_dir):
            start_dir = default_dir
        folder = QFileDialog.getExistingDirectory(
            self, "Select Sony P43 Photo Folder", start_dir)
        if not folder:
            return
        photos = self._images_from_folder(folder)
        if not photos:
            QMessageBox.information(
                self, "No photos found",
                "That folder contains no supported JPEG, raster, or RAW images.")
            return
        self._remember_open_directory(folder)
        self.load_image_files(photos)

    RECENT_PROJECTS_MAX = 8

    def _recent_projects(self):
        raw = self.app_settings.value("recent_projects", []) or []
        if isinstance(raw, str):  # QSettings sometimes returns a single string
            raw = [raw]
        return [str(p) for p in raw]

    def _remember_recent_project(self, path):
        path = str(path)
        items = [p for p in self._recent_projects() if p != path]
        items.insert(0, path)
        items = items[: self.RECENT_PROJECTS_MAX]
        self.app_settings.setValue("recent_projects", items)
        if hasattr(self, 'recent_projects_menu'):
            self._rebuild_recent_projects_menu()

    def _remove_recent_project(self, path):
        path = str(path)
        items = [p for p in self._recent_projects() if p != path]
        self.app_settings.setValue("recent_projects", items)
        if hasattr(self, 'recent_projects_menu'):
            self._rebuild_recent_projects_menu()

    def _rebuild_recent_projects_menu(self):
        menu = self.recent_projects_menu
        menu.clear()
        items = [p for p in self._recent_projects() if Path(p).exists()]
        if not items:
            act = QAction("(No recent projects)", self)
            act.setEnabled(False)
            menu.addAction(act)
            return
        for p in items:
            label = Path(p).name
            act = QAction(label, self)
            act.setToolTip(p)
            act.triggered.connect(lambda _checked=False, pth=p: self.open_project(pth))
            menu.addAction(act)
        menu.addSeparator()
        act_clear = QAction("Clear Menu", self)
        act_clear.triggered.connect(lambda: (
            self.app_settings.setValue("recent_projects", []),
            self._rebuild_recent_projects_menu(),
        ))
        menu.addAction(act_clear)

    def save_project(self):
        """Save to the currently-open project file, or prompt if none."""
        if self.current_project_path is None:
            return self.save_project_as()
        self._write_project_to(self.current_project_path)

    def save_project_as(self):
        from core.project import PROJECT_EXT
        if not self.image_files:
            QMessageBox.information(self, "Save Project", "No images are open.")
            return
        default_dir = self.app_settings.value("last_project_dir", self.output_dir)
        suggested = (str(self.current_project_path)
                     if self.current_project_path
                     else str(Path(default_dir) / f"Untitled{PROJECT_EXT}"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", suggested,
            f"LoFi Logic Project (*{PROJECT_EXT})"
        )
        if not path:
            return
        self._write_project_to(Path(path))

    def _write_project_to(self, path):
        from core.project import save_project
        if not self.image_files:
            return
        # Commit any in-flight edits so the saved project reflects the UI.
        cur = str(self.image_files[self.current_index])
        self.image_settings[cur] = self.processor.get_settings()
        try:
            written = save_project(
                path, self.image_files, self.image_settings,
                image_rotations=self.image_rotations,
                current_index=self.current_index,
            )
            self.current_project_path = written
            self.app_settings.setValue("last_project_dir", str(written.parent))
            self._remember_recent_project(written)
        except Exception as e:
            log.error("Save project failed: %s", e)
            QMessageBox.critical(self, "Save Project", f"Failed to save project:\n{e}")

    def open_project(self, path=None):
        from core.project import load_project, PROJECT_EXT, LEGACY_PROJECT_EXT
        if not path:
            default_dir = self.app_settings.value("last_project_dir", self.output_dir)
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Project", default_dir,
                f"LoFi Logic Project (*{PROJECT_EXT} *{LEGACY_PROJECT_EXT})"
            )
            if not path:
                return
        try:
            image_files, image_settings, image_rotations, current_index = load_project(path)
        except Exception as e:
            log.error("Open project failed: %s", e)
            QMessageBox.critical(self, "Open Project", f"Failed to open project:\n{e}")
            self._remove_recent_project(path)
            return
        if not image_files:
            QMessageBox.warning(self, "Open Project",
                                "None of the project's images could be found on disk.")
            return
        self.app_settings.setValue("last_project_dir", str(Path(path).parent))
        self._remember_recent_project(path)
        self.image_settings = dict(image_settings)
        # Pick the original active-file path so the alphabetical re-sort
        # inside load_image_files doesn't drift the selection.
        active_path = (str(image_files[current_index])
                       if 0 <= current_index < len(image_files) else None)
        self.load_image_files(
            image_files,
            image_rotations=image_rotations,
            current_path=active_path,
        )
        # load_image_files cleared current_project_path; re-attach so Save
        # writes back to this file.
        self.current_project_path = Path(path)

    def open_os_path(self, path):
        """Open a path the OS handed us (file association double-click, "Open
        With", or a command-line argument). Routes project files to
        open_project and DNG/folder inputs through the normal load path,
        so double-clicking a .lofi behaves like File → Open Project."""
        from core.project import PROJECT_EXT, LEGACY_PROJECT_EXT
        if not path or not os.path.exists(path):
            return
        if path.lower().endswith((PROJECT_EXT, LEGACY_PROJECT_EXT)):
            self.open_project(path)
            return
        resolved = self._resolve_input_paths([path])
        if resolved:
            self._remember_open_directory(path)
            self.load_image_files(resolved)

    def open_files(self):
        default_dir = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation) or str(Path.home())
        start_dir = self.app_settings.value("last_open_dir", default_dir)
        if not isinstance(start_dir, str) or not os.path.isdir(start_dir):
            start_dir = default_dir

        filter_string = (
            "Sony P43 JPEGs (*.jpg *.jpeg);;"
            "Supported images (*.dng *.arw *.nef *.nrw *.cr2 *.cr3 *.raf *.orf "
            "*.rw2 *.pef *.srw *.x3f *.3fr *.fff *.iiq *.rwl *.raw "
            "*.jpg *.jpeg *.png *.tif *.tiff *.webp);;"
            "Camera RAWs (*.dng *.arw *.nef *.nrw *.cr2 *.cr3 *.raf *.orf *.rw2 "
            "*.pef *.srw *.x3f *.3fr *.fff *.iiq *.rwl *.raw);;"
            "Finished images (*.jpg *.jpeg *.png *.tif *.tiff *.webp)"
        )

        files, _ = QFileDialog.getOpenFileNames(self, "Select Image Files", start_dir, filter_string)

        if files:
            self._remember_open_directory(files[0])
            self.load_image_files(self._resolve_input_paths(files))

    def _remember_open_directory(self, path: str | Path) -> None:
        """Persist the last successfully used image folder immediately."""
        candidate = Path(path)
        directory = candidate if candidate.is_dir() else candidate.parent
        if directory.is_dir():
            self.app_settings.setValue("last_open_dir", str(directory))
            self.app_settings.sync()

    def _stop_vibe_refresh_worker(self):
        """Stop, join, and release the vibe-refresh worker.

        Releasing the reference (not just stopping) is what frees memory: the
        worker holds a snapshot of the whole image cache, so a lingering
        ``self._vibe_refresh_worker`` keeps that entire snapshot alive — the
        cause of RAM not dropping when a project is replaced.
        """
        w = self._vibe_refresh_worker
        if w is None:
            return
        w.blockSignals(True)
        w.stop()
        if w.isRunning():
            w.wait()
        self._vibe_refresh_worker = None

    def _stop_thumbnail_workers(self):
        """Stop and join any running thumbnail workers so their QThreads never
        outlive the Python owner — that aborts with "QThread: Destroyed while
        thread is still running". signals are blocked first so a stale
        ``finished`` emission can't fire its slot against a replacement worker.
        A worker may still be mid-flight during close/reload."""
        for attr in ('thumbnail_worker', 'add_thumbnail_worker'):
            w = getattr(self, attr, None)
            if w is None:
                continue
            w.blockSignals(True)
            w._is_running = False
            if w.isRunning():
                w.wait()
            setattr(self, attr, None)

    def load_image_files(self, image_files, export_sources=None,
                          image_rotations=None, current_path=None):
        if not image_files:
            return

        valid, rejected = [], []
        sources = export_sources or {}
        for candidate in image_files:
            candidate_str = str(candidate)
            probe = str(sources.get(candidate_str, candidate_str))
            if Path(probe).suffix.lower() in self.SUPPORTED_EXTENSIONS:
                valid.append(candidate)
            else:
                rejected.append(candidate)
        image_files = valid
        if rejected:
            QMessageBox.warning(
                self, "Unsupported files",
                f"Skipped {len(rejected)} file(s) whose image format is unsupported."
            )
        if not image_files:
            return

        self._stop_vibe_refresh_worker()
        # Retire any in-flight thumbnail pass before we replace the worker
        # reference below, or the old QThread is orphaned while still running.
        self._stop_thumbnail_workers()

        # Always present in alphabetical order (by filename, case-insensitive)
        # regardless of source ordering or OS settings.
        image_files = sorted(image_files, key=lambda p: Path(str(p)).name.lower())

        # Any fresh image load detaches us from the previously-open project,
        # so subsequent Save uses Save-As semantics. open_project re-assigns
        # current_project_path after calling load_image_files.
        self.current_project_path = None

        self.image_files = image_files
        self.image_cache.clear()
        self.preview_cache.clear()
        self.thumbnail_cache.clear()
        self._file_is_flashback.clear()
        self.image_rotations = dict(image_rotations) if image_rotations else {}
        self.thumbnail_strip.clear()

        # Pick starting index: caller-provided path wins, else first image.
        self.current_index = 0
        if current_path:
            target = str(current_path)
            for i, p in enumerate(self.image_files):
                if str(p) == target:
                    self.current_index = i
                    break

        self.btn_process_all.setEnabled(True)
        self.update_process_button_text()

        expected_thumb_width = 105
        layout_spacing = 5
        final_width = len(self.image_files) * (expected_thumb_width + layout_spacing)
        self.thumbnail_strip.container.setMinimumWidth(final_width)

        # When importing from a camera, the first target file does not yet
        # exist on disk — export it synchronously so load_current_image() has
        # something to read. Pre-fill the image cache with what the export
        # helper already loaded so load_current_image avoids a second read.
        # The background worker skips this file (target now exists) and moves
        # on to the next.
        if export_sources:
            first = str(image_files[0])
            src = export_sources.get(first)
            if src and not os.path.exists(first):
                try:
                    from core.camera_import import export_camera_dng
                    preloaded = export_camera_dng(src, first, self.processor)
                    if preloaded is not None:
                        self.image_cache[first] = self.processor.intermediate_acescg.copy()
                        self._file_is_flashback[first] = bool(self.processor.is_flashback_file)
                except Exception as e:
                    log.error("First-image camera export failed: %s", e)

        self.load_current_image()

        if hasattr(self, 'loader_overlay'):
            self.loader_overlay.fade_in()
            self.loader_overlay.update_progress(0, len(self.image_files))

        render_profiles = {}
        for path in self.image_files:
            path_str = str(path)
            settings = self.image_settings.get(path_str)
            if settings:
                vibe_id = settings.get('active_vibe_id', self.current_vibe_id())
                vibe = self._vibe_for(vibe_id)
                render_profiles[path_str] = (
                    vibe, self._lut_obj(vibe.lut_ref), settings.copy(),
                )

        self.thumbnail_worker = ThumbnailWorker(
            self.image_files,
            self._lut_obj(self.current_vibe.lut_ref),
            export_sources=export_sources,
            rotations=self.image_rotations,
            lut_v1=self._v1_variant_lut(),
            render_profiles=render_profiles,
        )

        self.thumbnail_worker.progress.connect(self.loader_overlay.update_progress)
        self.thumbnail_worker.thumbnail_ready.connect(self._add_thumbnail_to_ui)

        if hasattr(self, '_on_thumbnail_error'):
            self.thumbnail_worker.error.connect(self._on_thumbnail_error)
        if hasattr(self, '_on_thumbnails_finished'):
            self.thumbnail_worker.finished.connect(self._on_thumbnails_finished)

        self.thumbnail_worker.start()

    def _on_thumbnail_error(self, index, error_message):
        log.error("  ✗ Failed thumbnail %d: %s", index, error_message)
        try:
            if hasattr(self, 'loader_overlay') and self.loader_overlay.isVisible():
                self.loader_overlay.progress_label.setText(f"Error at {index}: {error_message}")
                QTimer.singleShot(1500, lambda: self.loader_overlay.update_progress(index + 1, len(self.image_files)))
        except Exception:
            log.debug("loader overlay error display failed", exc_info=True)

    def _on_thumbnails_finished(self):
        self.thumbnails_loading = False
        log.info("✓ Thumbnail generation complete!")
        self.thumbnail_strip.container.setUpdatesEnabled(True)
        if self.thumbnail_worker:
            # ThumbnailWorker emits its own `finished` as the LAST line of run(),
            # i.e. while the QThread is still technically running. wait() blocks
            # until run() has actually returned (instant here) so the queued
            # deleteLater can't destroy a still-running QThread -> qFatal/abort.
            self.thumbnail_worker.wait()
            self.thumbnail_worker.deleteLater()
            self.thumbnail_worker = None
        try:
            if hasattr(self, 'loader_overlay'):
                self.loader_overlay.clear_and_hide()
        except Exception:
            log.debug("loader overlay hide failed", exc_info=True)

    def add_image_files(self, new_files):
        """Append new images to the current session without resetting existing ones."""
        if not new_files:
            return

        existing_paths = {str(f) for f in self.image_files}
        files_to_add = [f for f in new_files if str(f) not in existing_paths]
        if not files_to_add:
            return

        offset = len(self.image_files)
        self.image_files.extend(files_to_add)

        self.btn_process_all.setEnabled(True)
        self.update_process_button_text()

        expected_thumb_width = 105
        layout_spacing = 5
        final_width = len(self.image_files) * (expected_thumb_width + layout_spacing)
        self.thumbnail_strip.container.setMinimumWidth(final_width)

        if hasattr(self, 'add_thumbnail_worker') and self.add_thumbnail_worker and self.add_thumbnail_worker.isRunning():
            self.add_thumbnail_worker._is_running = False
            self.add_thumbnail_worker.wait()

        if hasattr(self, 'loader_overlay'):
            self.loader_overlay.fade_in()
            self.loader_overlay.update_progress(0, len(files_to_add))

        self.add_thumbnail_worker = ThumbnailWorker(
            files_to_add,
            self._lut_obj(self.current_vibe.lut_ref),
            lut_v1=self._v1_variant_lut(),
        )
        self.add_thumbnail_worker.progress.connect(self.loader_overlay.update_progress)
        self.add_thumbnail_worker.thumbnail_ready.connect(
            lambda i, t, mid, isfb, off=offset: self._add_thumbnail_to_ui(i + off, t, mid, isfb)
        )
        self.add_thumbnail_worker.finished.connect(self._on_add_thumbnails_finished)
        self.add_thumbnail_worker.start()

    def _on_add_thumbnails_finished(self):
        log.info("✓ Add-images thumbnail generation complete!")
        if hasattr(self, 'add_thumbnail_worker') and self.add_thumbnail_worker:
            # See _on_thumbnails_finished: join the thread before deleteLater so
            # the DeferredDelete can't hit a QThread that's still running.
            self.add_thumbnail_worker.wait()
            self.add_thumbnail_worker.deleteLater()
            self.add_thumbnail_worker = None
        try:
            if hasattr(self, 'loader_overlay'):
                self.loader_overlay.clear_and_hide()
        except Exception:
            log.debug("loader overlay hide failed", exc_info=True)
        self.update_mode_label()

    # ===================================================================
    # THUMBNAIL MANAGEMENT
    # ===================================================================

    def update_thumbnail_for_settings(self, index, settings, _processor=None):
        if not self.image_files or index >= len(self.image_files):
            return

        file_path = str(self.image_files[index])

        if index == self.current_index:
            try:
                img_display = self.processor.render_preview()
                if img_display is not None:
                    h, w = img_display.shape[:2]
                    scale = 70 / h
                    new_w = int(w * scale)
                    thumb_array = cv2.resize(img_display, (new_w, 70), interpolation=cv2.INTER_LINEAR)
                    self.thumbnail_cache[file_path] = thumb_array
                    self.thumbnail_strip.update_thumbnail(index, thumb_array)
                    return
            except Exception as e:
                log.error("  ✗ Failed to update current thumbnail: %s", e)
        else:
            try:
                if file_path in self.image_cache:
                    vibe_id = settings.get('active_vibe_id', self.current_vibe_id())
                    vibe = self._vibe_for(vibe_id)
                    temp_processor = _processor or ImageProcessor(vibe=vibe)
                    restore_lut = None
                    if _processor is None:
                        # Each frame owns its preset, so resolve the LUT from that
                        # frame's saved settings rather than the currently open one.
                        chosen = self._lut_obj(vibe.lut_ref)
                        temp_processor.lut = chosen
                        if chosen is not self.processor.lut:
                            if chosen is not None:
                                gpu.upload_lut(chosen.table)
                            restore_lut = self.processor.lut
                    temp_processor.intermediate_acescg = self.image_cache[file_path].copy()
                    temp_processor.current_file = file_path
                    temp_processor.is_flashback_file = self._file_is_flashback.get(file_path, False)
                    temp_processor.input_kind = input_kind_for_path(
                        file_path, temp_processor.is_flashback_file)
                    temp_processor.set_settings(settings)
                    img_display = temp_processor._render_fast(downscale=True)
                    if restore_lut is not None:
                        gpu.upload_lut(restore_lut.table)
                    if img_display is not None:
                        h, w = img_display.shape[:2]
                        scale = 70 / h
                        new_w = int(w * scale)
                        thumb_array = cv2.resize(img_display, (new_w, 70), interpolation=cv2.INTER_LINEAR)
                        self.thumbnail_cache[file_path] = thumb_array
                        self.thumbnail_strip.update_thumbnail(index, thumb_array)
                        return
            except Exception as e:
                log.error("  ✗ Failed to update thumbnail %d: %s", index, e)

    def update_current_thumbnail(self, img_array=None):
        if not self.image_files:
            return
        file_path = str(self.image_files[self.current_index])
        try:
            if img_array is None:
                img_array = self.processor.render_preview()
            if img_array is not None:
                h, w = img_array.shape[:2]
                scale = 70 / h
                new_w = int(w * scale)
                thumb_array = cv2.resize(img_array, (new_w, 70), interpolation=cv2.INTER_LINEAR)
                self.thumbnail_cache[file_path] = thumb_array
                self.thumbnail_strip.update_thumbnail(self.current_index, thumb_array)
        except Exception as e:
            _timing_print(f"  [Thumbnail] Update failed silently: {e}")

    def _add_thumbnail_to_ui(self, index, thumb_array, intermediate=None,
                             is_flashback=None):
        self.thumbnail_strip.container.setUpdatesEnabled(False)
        filename = self.image_files[index].name if self.image_files and index < len(self.image_files) else None
        self.thumbnail_strip.add_thumbnail(thumb_array, index, filename=filename)
        self.thumbnail_strip.container.setUpdatesEnabled(True)

        if self.image_files and index < len(self.image_files):
            file_path = str(self.image_files[index])
            if self._is_processed(file_path):
                self.thumbnail_strip.set_processed(index, True)
            if index == self.current_index:
                self.thumbnail_strip.set_current_index(index)

        if intermediate is not None and self.image_files and index < len(self.image_files):
            file_path = str(self.image_files[index])
            self.image_cache[file_path] = intermediate
            if is_flashback is not None:
                # Persist the Flashback flag alongside the cached intermediate,
                # otherwise navigating to a worker-cached image leaves the DNG
                # export button greyed out (it only sees False from .get()).
                self._file_is_flashback[file_path] = bool(is_flashback)
                if index == self.current_index:
                    self._update_dng_button_state()

    def on_thumbnail_click(self, index):
        if 0 <= index < len(self.image_files):
            self.current_index = index
            self.load_current_image()

    def remove_current_from_project(self):
        self.remove_from_project(self.current_index)

    def remove_from_project(self, index):
        """Drop the image at `index` from the open set (no file deletion).
        Used to curate before saving a project."""
        if not self.image_files:
            return
        if not (0 <= index < len(self.image_files)):
            return
        file_path = str(self.image_files[index])

        self.image_files.pop(index)
        self.image_settings.pop(file_path, None)
        self.image_rotations.pop(file_path, None)
        self.image_cache.pop(file_path, None)
        self.preview_cache.pop(file_path, None)
        self.thumbnail_cache.pop(file_path, None)
        self._file_is_flashback.pop(file_path, None)
        self.thumbnail_strip.remove_at(index)

        if not self.image_files:
            self.current_index = 0
            # Drop the cached intermediate so the removed image cannot be
            # rendered back into the view.
            self.processor.intermediate_acescg = None
            self.processor.current_file = None
            # And cancel any in-flight background render — otherwise its
            # render_done would fire after the clear and re-set
            # image_label._original_pixmap, making the image reappear on the
            # next zoom/scroll.
            if hasattr(self, '_render_worker'):
                self._render_worker.invalidate()
            self._render_needs_commit = False
            self.image_label.clear()
            self.label_filename.setText("")
            self.label_input_kind.setText("")
            self.label_counter.setText("0 / 0")
            self.btn_process_all.setEnabled(False)
            self.update_process_button_text()
            self.update_mode_label()
            return

        # Stay on the same slot when possible; if we deleted the tail, fall
        # back to what is now the last image.
        self.current_index = min(index, len(self.image_files) - 1)
        self.update_process_button_text()
        self.load_current_image()

    def on_thumbnail_right_click(self, index):
        if 0 <= index < len(self.image_files):
            self.thumbnail_strip.toggle_process_selection(index)
            self.update_process_button_text()
            count = len(self.thumbnail_strip.get_process_selected_indices())
            if count > 0:
                self.mode_label.setText(f"{count} selected for processing")
            else:
                self.mode_label.setText("All images will be processed")
            self.mode_label.setStyleSheet(f"color: {C['accent']};")
            QTimer.singleShot(2000, self.update_mode_label)

    def on_thumbnail_paste_selected(self, index, is_selected):
        count = len(self.thumbnail_strip.get_paste_selected_indices())
        if count > 0:
            paste_key = "Cmd+V" if sys.platform == 'darwin' else "Ctrl+V"
            self.mode_label.setText(f"{count} selected for paste ({paste_key})")
            self.mode_label.setStyleSheet(f"color: {C['accent']};")
        else:
            self.update_mode_label()

    def update_process_button_text(self):
        self.btn_process_all.setStyleSheet(process_btn_qss())
        selected = self.thumbnail_strip.get_process_selected_indices()
        total = len(self.image_files)
        if hasattr(self, 'label_roll_count'):
            self.label_roll_count.setText(f"{total} PHOTO{'S' if total != 1 else ''}")
        if selected:
            self.btn_process_all.setText(f"Export {len(selected)} selected")
        else:
            self.btn_process_all.setText(f"Export {total} photo{'s' if total != 1 else ''}")

    def _set_process_button_done(self, count: int):
        """Post-export state: checkmark + 'N frames processed'."""
        self.btn_process_all.setText(f"✓  {count} photo{'s' if count != 1 else ''} exported")
        self.btn_process_all.setStyleSheet(f"""
            QPushButton {{
                background: {C['bg_input']};
                color: {C['text_label']};
                border: 1px solid {C['border_input']};
                border-radius: 3px;
                font-family: "{UI_FONT}";
                font-size: 12px;
                font-weight: 600;
                padding: 10px 12px;
            }}
        """)

    # ===================================================================
    # IMAGE LOADING & DISPLAY
    # ===================================================================

    def _preview_key(self):
        """Identity of the downscaled preview for the current processor state.

        A cached preview is reusable only if every input that the downscale
        render depends on is unchanged: the source intermediate (id changes on
        rotate/reload), the per-image intensity, the active preset, and the LUT.
        Keying on these makes a stale preview
        structurally impossible — any change yields a new key and a cache miss,
        so no manual invalidation is needed when settings or vibe change."""
        a = self.processor.adjustments
        return (
            id(self.processor.intermediate_acescg),
            round(a.exposure_ev, 4), round(a.wb_temp, 4), round(a.tint, 4),
            round(getattr(a, 'push_pull_ev', 0.0), 4),
            round(getattr(a, 'filter_intensity', 1.0), 4),
            getattr(a, 'rotation', 0),
            self.current_vibe_id(),
            id(self.processor.lut),
        )

    def load_current_image(self):
        if not self.image_files:
            self.label_filename.setText("")
            if hasattr(self, 'label_input_kind'):
                self.label_input_kind.setText("")
            self.label_counter.setText("0 / 0")
            return

        # Cancel any in-flight scrub render and clear the commit flag — the
        # processor state is about to change out from under the worker.
        if hasattr(self, '_render_worker'):
            self._render_worker.invalidate()
        self._render_needs_commit = False

        file_path = str(self.image_files[self.current_index])
        self.label_filename.setText(Path(file_path).name)
        self._update_input_kind_badge(self._file_is_flashback.get(file_path))
        self.label_counter.setText(f"{self.current_index + 1} / {len(self.image_files)}")
        if hasattr(self, 'thumbnail_strip'):
            self.thumbnail_strip.set_current_index(self.current_index)

        if file_path in self.image_settings:
            settings = self.image_settings[file_path]
            self.processor.set_settings(settings)
        else:
            settings = ImageAdjustments(active_vibe_id=self.current_vibe_id()).to_dict()
            self.processor.set_settings(settings)

        # A roll can mix looks frame-by-frame. Switch the picker and processing
        # recipe silently while navigating so this does not overwrite settings.
        vibe_id = settings.get('active_vibe_id', self.current_vibe_id())
        vibe_id = normalize_preset_id(vibe_id)
        if vibe_id not in VIBE_PRESETS:
            vibe_id = 'funsaver_800'
        vibe = self._vibe_for(vibe_id)
        self.vibe_picker.set_vibe(vibe_id, emit=False)
        self._apply_vibe(vibe_id, vibe, refresh_thumbnails=False, render=False)
        self.update_sliders_from_processor()


        if file_path in self.image_cache:
            self.processor.intermediate_acescg = self.image_cache[file_path]
            self.processor.current_file = file_path
            # Restore Flashback status so DNG button reflects the correct state
            self.processor.is_flashback_file = self._file_is_flashback.get(file_path, False)
            self.processor.input_kind = input_kind_for_path(
                file_path, self.processor.is_flashback_file)
            self._update_dng_button_state()
            self._update_input_kind_badge(self.processor.is_flashback_file)
            # Revisiting an image must be instant and must not block the UI
            # thread on a GPU readback (that readback serialises behind any
            # in-flight full-res render on the shared device — the freeze that
            # made switching show the previous image for seconds). Reuse the
            # cached downscaled preview when it still matches the current state;
            # only render synchronously on a genuine miss (first visit / changed
            # settings or vibe).
            key = self._preview_key()
            cached = self.preview_cache.get(file_path)
            if cached is not None and cached[0] == key:
                img_array = cached[1]
            else:
                img_array = self.processor.render_preview(downscale=True)
                self.preview_cache[file_path] = (key, img_array)
            self.display_image(img_array, is_scrub=True)
            self.update_current_thumbnail(img_array)
            self.update_mode_label()
            self._render_worker.request(downscale=False)
        else:
            log.info("[Load] Image not in cache, loading from disk...")
            img_array = self.processor.load_image(file_path)
            if img_array is not None:
                self._file_is_flashback[file_path] = self.processor.is_flashback_file
                self._update_dng_button_state()
                self._update_input_kind_badge(self.processor.is_flashback_file)
                # Re-apply any rotation persisted from a previous session.
                stored_rot = self.image_rotations.get(file_path, 0)
                if stored_rot:
                    self.processor.adjustments.rotation = stored_rot
                    img_array = self.processor._apply_rotation_and_render()
                self.image_cache[file_path] = self.processor.intermediate_acescg.copy()
                self.preview_cache[file_path] = (self._preview_key(), img_array)
                self.update_current_thumbnail(img_array)
                self.display_image(img_array, is_scrub=True)
                self.save_current_settings()
                self.update_mode_label()
                self._render_worker.request(downscale=False)
            else:
                QMessageBox.critical(self, "Error", f"Failed to load image:\n{file_path}")

    def _update_input_kind_badge(self, _legacy_flashback=False):
        if not hasattr(self, 'label_input_kind'):
            return
        file_path = (str(self.image_files[self.current_index])
                     if self.image_files else "")
        if file_path and is_sony_p43_path(file_path):
            self.label_input_kind.setText("SONY P43")
            self.label_input_kind.setStyleSheet(
                f"color: {C['accent']}; background: {C['accent_soft']}; border-radius: 3px;")
            self.label_input_kind.setToolTip(
                "Native Sony Cyber-shot DSC-P43 JPEG · metadata preserved on export")
        elif file_path and is_raster_path(file_path):
            self.label_input_kind.setText("JPEG")
            self.label_input_kind.setStyleSheet(
                f"color: {C['text_canvas_dim']}; background: rgba(255,255,255,0.07); border-radius: 3px;")
            self.label_input_kind.setToolTip("Color-managed finished image")
        else:
            self.label_input_kind.setText("RAW")
            self.label_input_kind.setStyleSheet(
                f"color: {C['text_canvas_dim']}; background: rgba(255,255,255,0.07); border-radius: 3px;")
            self.label_input_kind.setToolTip("Generic camera RAW compatibility input")

    def display_image(self, img_array, is_scrub=False):
        if img_array is None:
            return
        if is_scrub:
            self.image_label.set_scrub_image(img_array)
        else:
            self.image_label.set_image(img_array)

    def show_before_compare(self):
        """Show the calibrated 0%-preset rendering while the mouse is held."""
        if not self.image_files or self.processor.intermediate_acescg is None:
            return
        neutral = self.processor.render_neutral_preview(downscale=True)
        self.image_label.show_compare_image(neutral)

    def end_before_compare(self):
        self.image_label.end_compare()

    def update_sliders_from_processor(self):
        a = self.processor.adjustments
        value = int(round(float(getattr(a, 'filter_intensity', 1.0)) * 100.0))
        self.slider_intensity.blockSignals(True)
        self.slider_intensity.setValue(value)
        self.slider_intensity.blockSignals(False)
        self.label_intensity.setText(f"{value}%")

    # ===================================================================
    # RENDER WORKER CALLBACK
    # ===================================================================

    def _on_render_done(self, img_array, was_downscaled):
        """Receive a completed render from the background RenderWorker."""
        self.display_image(img_array, is_scrub=was_downscaled)
        # Keep the downscaled-preview cache warm with the latest look so a later
        # revisit is instant. Safe even mid-scrub: the worker drops post-switch
        # renders (epoch), so this only ever fires for the current image, and
        # the key captures the live settings used to produce this frame.
        if was_downscaled and self.image_files:
            file_path = str(self.image_files[self.current_index])
            self.preview_cache[file_path] = (self._preview_key(), img_array)
            # Slider release deliberately leaves the quick render queued first.
            # Once it is visible, refine the same state at full resolution.
            if self._render_needs_commit:
                self._render_worker.request(downscale=False)
        if not was_downscaled and self._render_needs_commit:
            self._render_needs_commit = False
            self.update_current_thumbnail(img_array)
            self.update_mode_label()
            # save_current_settings is called synchronously at slider release —
            # see on_*_released / reset_*_slider — so persistence survives an
            # image switch that invalidates this render.

    # ===================================================================
    # SLIDER HANDLERS
    # ===================================================================

    def on_filter_intensity_moved(self, value):
        self.label_intensity.setText(f"{value}%")
        self.processor.adjustments.filter_intensity = value / 100.0
        self._render_worker.request(downscale=True)

    def on_filter_intensity_released(self):
        self.save_current_settings()
        self._render_needs_commit = True
        self._render_worker.request(downscale=True)

    def reset_all_sliders(self):
        self.slider_intensity.blockSignals(True)
        self.slider_intensity.setValue(100)
        self.slider_intensity.blockSignals(False)
        self.label_intensity.setText("100%")
        self.processor.adjustments = ImageAdjustments(active_vibe_id=self.current_vibe_id())
        img_array = self.processor.render_preview()
        self.display_image(img_array)
        self.update_current_thumbnail(img_array)
        self.save_current_settings()
        self.update_mode_label()

    def update_mode_label(self):
        """Default status line when the processor is idle."""
        if self.image_files:
            total = len(self.image_files)
            processed = sum(
                1 for p in self.image_files if self._is_processed(str(p))
            )
            pending = total - processed
            self.mode_label.setText(f"Ready   {processed} processed · {pending} pending")
        else:
            self.mode_label.setText("Ready")
        self.mode_label.setStyleSheet(f"color: {C['text_dim']};")
        self.status_dot.setStyleSheet(f"color: {C['processed']};")

    def _is_processed(self, file_path: str) -> bool:
        """True if an export file exists in output_dir for this source image."""
        try:
            base = export_basename(file_path)
            candidates = ["_clean.dng", "_edit.jpg"]
            candidates += [f"_{s}.jpg" for s in VIBE_EXPORT_SUFFIX.values()]
            for suffix in candidates:
                if os.path.exists(os.path.join(self.output_dir, base + suffix)):
                    return True
        except Exception:
            log.debug("export-exists check failed for %s", file_path, exc_info=True)
        return False

    def save_current_settings(self):
        if self.image_files:
            file_path = str(self.image_files[self.current_index])
            self.image_settings[file_path] = self.processor.get_settings()

    # ===================================================================
    # COPY / PASTE SETTINGS
    # ===================================================================

    def prev_image(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_current_image()

    def next_image(self):
        if self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.load_current_image()

    def copy_settings(self):
        self.settings_clipboard = self.processor.get_settings()
        self.mode_label.setText("Settings copied")
        self.mode_label.setStyleSheet(f"color: {C['accent']};")
        QTimer.singleShot(2000, self.update_mode_label)

    def paste_settings(self):
        if not self.settings_clipboard:
            return

        if hasattr(self, '_render_worker'):
            self._render_worker.invalidate()
        self._render_needs_commit = False

        paste_selected = self.thumbnail_strip.get_paste_selected_indices()

        if paste_selected:
            indices_to_apply = sorted(paste_selected)
            total = len(indices_to_apply)

            success_count = 0
            self.mode_label.setText(f"Applying to {total} images...")
            self.mode_label.setStyleSheet(f"color: {C['accent']};")
            QApplication.processEvents()

            for idx in indices_to_apply:
                file_path = str(self.image_files[idx])
                self.image_settings[file_path] = self.settings_clipboard.copy()
                success_count += 1
                if success_count % 5 == 0:
                    self.mode_label.setText(f"Applied {success_count}/{total}...")
                    QApplication.processEvents()

            if self.current_index in paste_selected:
                self.processor.set_settings(self.settings_clipboard)
                vibe_id = self.settings_clipboard.get('active_vibe_id', self.current_vibe_id())
                vibe_id = normalize_preset_id(vibe_id)
                if vibe_id not in VIBE_PRESETS:
                    vibe_id = 'funsaver_800'
                self.vibe_picker.set_vibe(vibe_id, emit=False)
                self._apply_vibe(vibe_id, self._vibe_for(vibe_id), render=False)
                img_array = self.processor.render_preview()
                self.update_sliders_from_processor()
                self.display_image(img_array)
                self.update_thumbnail_for_settings(self.current_index, self.settings_clipboard)

            self.mode_label.setText(f"Updating thumbnails...")
            for idx in indices_to_apply:
                if idx != self.current_index:
                    self.update_thumbnail_for_settings(idx, self.settings_clipboard)

            self.thumbnail_strip.clear_paste_selection()
            self.mode_label.setText(f"Settings applied to {success_count} images")
            QTimer.singleShot(2000, self.update_mode_label)

        else:
            self.processor.set_settings(self.settings_clipboard)
            vibe_id = self.settings_clipboard.get('active_vibe_id', self.current_vibe_id())
            vibe_id = normalize_preset_id(vibe_id)
            if vibe_id not in VIBE_PRESETS:
                vibe_id = 'funsaver_800'
            self.vibe_picker.set_vibe(vibe_id, emit=False)
            self._apply_vibe(vibe_id, self._vibe_for(vibe_id), render=False)
            img_array = self.processor.render_preview()
            self.update_sliders_from_processor()
            self.display_image(img_array)
            self.save_current_settings()
            self.update_mode_label()
            self.update_thumbnail_for_settings(self.current_index, self.settings_clipboard)
            self.mode_label.setText("Settings pasted")
            self.mode_label.setStyleSheet(f"color: {C['accent']};")
            QTimer.singleShot(2000, self.update_mode_label)

    # ===================================================================
    # EXPORT
    # ===================================================================

    def select_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.output_dir)
        if directory:
            self.output_dir = directory
            self.app_settings.setValue("default_export_dir", directory)
            self.app_settings.sync()
            self.label_output.setText(self._short_output_path(directory))
            self.label_output.setToolTip(directory)

    def _update_dng_button_state(self):
        """Compatibility hook: the P43 product always exports JPEG."""
        if self.export_mode != 'jpeg':
            self.set_export_mode('jpeg')

    def set_export_mode(self, mode):
        """Select the finished-JPEG export used by the P43 workflow."""
        self.export_mode = 'jpeg'
        self.btn_export_jpeg.setChecked(True)
        self.btn_export_jpeg.setStyleSheet(format_pill_qss(True))
        if hasattr(self, 'label_export_mode_help'):
            self.label_export_mode_help.setText(
                "Finished image with the selected camera look"
            )
        if hasattr(self, "btn_process_all") and hasattr(self, "thumbnail_strip"):
            self.update_process_button_text()

    def process_all_images(self):
        if not self.image_files:
            return

        if hasattr(self, '_render_worker'):
            self._render_worker.invalidate()
        self._render_needs_commit = False

        selected_indices = self.thumbnail_strip.get_process_selected_indices()
        if selected_indices:
            indices_to_process = sorted(selected_indices)
        else:
            indices_to_process = list(range(len(self.image_files)))

        # Low disk space: inline warning in the status bar, no modal.
        mb_per_image = {'jpeg': 5, 'dng': 17}.get(self.export_mode, 5)
        required_mb = len(indices_to_process) * mb_per_image
        try:
            free_mb = shutil.disk_usage(self.output_dir).free // (1024 * 1024)
            if free_mb < required_mb:
                self.mode_label.setText(
                    f"Low disk space: ~{required_mb} MB needed, {free_mb} MB free"
                )
                self.mode_label.setStyleSheet(f"color: {C['accent']};")
        except OSError:
            pass

        self.progress_bar.setMaximum(len(indices_to_process))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.btn_process_all.setEnabled(False)

        success_count = 0
        skip_count = 0
        total = len(indices_to_process)

        for i, idx in enumerate(indices_to_process):
            file_path = str(self.image_files[idx])

            try:
                self.progress_bar.setValue(i)
                self.btn_process_all.setText(f"Processing {i + 1} / {total}")
                self.mode_label.setText(f"Processing {i+1}/{total}...")
                QApplication.processEvents()

                if self.export_mode == 'dng':
                    is_flashback = self._file_is_flashback.get(file_path)
                    if is_flashback is None:
                        from core.processor import _read_dng_exif
                        is_flashback, _ = _read_dng_exif(file_path)
                        self._file_is_flashback[file_path] = is_flashback
                    if not is_flashback:
                        skip_count += 1
                        continue

                if self.export_mode != 'dng':
                    if file_path in self.image_cache:
                        self.processor.intermediate_acescg = self.image_cache[file_path].copy()
                        self.processor.current_file = file_path
                        self.processor.is_flashback_file = self._file_is_flashback.get(file_path, False)
                        self.processor.input_kind = input_kind_for_path(
                            file_path, self.processor.is_flashback_file)
                    else:
                        self.processor.load_image(file_path)
                        self.image_cache[file_path] = self.processor.intermediate_acescg.copy()

                    settings = self.image_settings.get(
                        file_path,
                        ImageAdjustments(active_vibe_id=self.current_vibe_id()).to_dict(),
                    )
                    self.processor.set_settings(settings)
                    vibe_id = settings.get('active_vibe_id', self.current_vibe_id())
                    self._apply_vibe(vibe_id, self._vibe_for(vibe_id), render=False)

                base_name = export_basename(file_path)
                if self.export_mode == 'dng':
                    output_path = os.path.join(self.output_dir, f"{base_name}_clean.dng")
                else:
                    self._apply_effective_lut(file_path)
                    vibe_id = self.processor.adjustments.active_vibe_id
                    suffix = VIBE_EXPORT_SUFFIX.get(vibe_id, 'edit')
                    output_path = os.path.join(self.output_dir, f"{base_name}_{suffix}.jpg")

                if self.export_mode == 'dng':
                    from core.dng_export import export_dng
                    thumb = None
                    strip_pixmap = None
                    if 0 <= idx < len(self.thumbnail_strip.thumbnails):
                        strip_pixmap = self.thumbnail_strip.thumbnails[idx].pixmap
                    if strip_pixmap and not strip_pixmap.isNull():
                        img = strip_pixmap.toImage().convertToFormat(QImage.Format_RGB888)
                        w, h = img.width(), img.height()
                        stride = img.bytesPerLine()
                        arr = np.frombuffer(img.bits(), dtype=np.uint8).reshape((h, stride))[:, :w * 3].reshape((h, w, 3)).copy()
                        tw, th = 512, max(1, int(h * 512 / w))
                        thumb = cv2.resize(arr, (tw, th), interpolation=cv2.INTER_LINEAR)
                    if thumb is None:
                        import rawpy
                        with rawpy.imread(file_path) as raw:
                            thumb = raw.postprocess(
                                half_size=True, use_camera_wb=True,
                                no_auto_bright=True, output_bps=8,
                            )
                        tw = 512
                        th = max(1, int(thumb.shape[0] * tw / thumb.shape[1]))
                        thumb = cv2.resize(thumb, (tw, th), interpolation=cv2.INTER_LINEAR)
                    ok = export_dng(file_path, output_path, thumb, self.current_vibe.dng_profile_name)
                elif export_image(self.processor, output_path):
                    ok = True
                else:
                    ok = False

                if ok:
                    success_count += 1
                    self.thumbnail_strip.set_processed(idx, True)

            except Exception as e:
                log.error("Error processing %s: %s", file_path, e)
                traceback.print_exc()

            self.progress_bar.setValue(i + 1)
            QApplication.processEvents()

        self.progress_bar.setVisible(False)
        self.btn_process_all.setEnabled(True)
        self.load_current_image()
        self.update_mode_label()

        processed_total = total - skip_count
        if skip_count > 0:
            self.mode_label.setText(
                f"✓ {success_count} processed · {skip_count} skipped (non-Flashback, DNG only)"
            )
            self.mode_label.setStyleSheet(f"color: {C['text_dim']};")
            QTimer.singleShot(4000, self.update_mode_label)

        if success_count == processed_total and processed_total > 0:
            self._set_process_button_done(success_count)
        else:
            self.update_process_button_text()

    # ===================================================================
    # DEBUG / REFRESH
    # ===================================================================

    def refresh_from_debug(self):
        log.info("Refreshing...")
        if self.processor and self.processor.intermediate_acescg is not None:
            img_array = self.processor.render_preview()
            self.display_image(img_array)
            self.update_current_thumbnail(img_array)

    def reload_current_image(self):
        if not self.image_files:
            return
        file_path = str(self.image_files[self.current_index])
        if file_path in self.image_cache:
            del self.image_cache[file_path]
        self.preview_cache.pop(file_path, None)
        self.load_current_image()

    # ===================================================================
    # KEYBOARD & DRAG/DROP
    # ===================================================================

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_C and (event.modifiers() & Qt.ControlModifier):
            if self.image_files:
                self.copy_settings()
            event.accept()
        elif event.key() == Qt.Key_V and (event.modifiers() & Qt.ControlModifier):
            if self.image_files and self.settings_clipboard:
                self.paste_settings()
            event.accept()
        elif event.key() == Qt.Key_A and (event.modifiers() & Qt.ControlModifier):
            if self.image_files:
                self.thumbnail_strip.select_all_for_paste()
                count = len(self.thumbnail_strip.get_paste_selected_indices())
                self.mode_label.setText(f"{count} selected for paste")
                self.mode_label.setStyleSheet(f"color: {C['accent']};")
                QTimer.singleShot(2000, self.update_mode_label)
            event.accept()
        elif event.key() == Qt.Key_Escape:
            self.thumbnail_strip.clear_paste_selection()
            self.mode_label.setText("Paste selection cleared")
            self.mode_label.setStyleSheet(f"color: {C['accent']};")
            QTimer.singleShot(1500, self.update_mode_label)
            event.accept()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.image_files:
                self.remove_current_from_project()
            event.accept()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # Needs a real native window; defer one event loop pass so the
        # NSWindow is fully constructed before we drive AppKit against it.
        if not getattr(self, "_native_chrome_applied", False):
            self._native_chrome_applied = True

            def _do_apply():
                try:
                    from ui import native_chrome
                    native_chrome.apply(self, theme.current_theme())
                except Exception as e:
                    log.warning("[native_chrome] apply failed: %s", e)

            QTimer.singleShot(0, _do_apply)

    def closeEvent(self, event):
        # Join every background QThread before teardown — any still running when
        # its Python owner is destroyed aborts the process ("QThread: Destroyed
        # while thread is still running"). Slow V1 thumbnail passes make this
        # easy to hit on close.
        self._stop_vibe_refresh_worker()
        self._stop_thumbnail_workers()
        self._render_worker.stop()
        self._render_worker.wait()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loader_overlay') and self.loader_overlay.isVisible():
            self.loader_overlay.setGeometry(self.rect())
        if hasattr(self, 'drag_overlay') and self.drag_overlay.isVisible():
            self._update_drag_overlay_geometry()

    def _update_drag_overlay_geometry(self):
        """Position the two drag overlays: upper = replace, lower = add (thumbnail strip)."""
        cw = self.centralWidget()
        if cw is None:
            return
        cw_w = cw.width()
        cw_h = cw.height()
        m = 8  # margin

        if self.image_files and hasattr(self, 'drag_overlay_add'):
            # Split at the thumbnail strip top edge; add-overlay fills the full strip.
            strip = self.thumbnail_strip.parentWidget() or self.thumbnail_strip
            strip_top = strip.mapTo(cw, QPoint(0, 0)).y()
            strip_h = strip.height()
            self.drag_overlay.setGeometry(m, m, cw_w - 2 * m, max(40, strip_top - 2 * m))
            self.drag_overlay_add.setGeometry(0, strip_top, cw_w, strip_h)
        else:
            # No images loaded — full-area replace overlay only
            self.drag_overlay.setGeometry(m, m, cw_w - 2 * m, cw_h - 2 * m)

    def _set_drag_hover(self, over_strip):
        """Highlight the active drag zone and dim the inactive one."""
        if not self.image_files:
            return
        if over_strip:
            self.drag_overlay.setStyleSheet(self._drag_style_dim)
            self.drag_overlay_add.setStyleSheet(self._drag_style_active)
        else:
            self.drag_overlay.setStyleSheet(self._drag_style_active)
            self.drag_overlay_add.setStyleSheet(self._drag_style_dim)

    def _images_from_folder(self, folder):
        """Collect supported image candidates from a folder, non-recursively."""
        found = []
        try:
            entries = sorted(Path(folder).iterdir(), key=lambda x: x.name.lower())
        except OSError:
            return found
        for entry in entries:
            if not entry.is_file():
                continue
            if entry.name.lower().endswith(self.SUPPORTED_EXTENSIONS):
                found.append(entry)
        if not found:
            log.warning("[editor] no loadable images in folder: %s", folder)
        return found

    def _resolve_input_paths(self, paths):
        """Expand folders into supported images; pass individual files through."""
        resolved = []
        for p in paths:
            sp = str(p)
            if os.path.isdir(sp):
                resolved.extend(self._images_from_folder(sp))
            else:
                resolved.append(Path(p))
        return resolved

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and (
                        url.toLocalFile().lower().endswith(self.SUPPORTED_EXTENSIONS)
                        or os.path.isdir(url.toLocalFile())):
                    self._update_drag_overlay_geometry()
                    self.drag_overlay.raise_()
                    self.drag_overlay.show()
                    if self.image_files:
                        self.drag_overlay_add.raise_()
                        self.drag_overlay_add.show()
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        if not (self.drag_overlay.isVisible() and self.image_files):
            return
        cw = self.centralWidget()
        strip_top = self.thumbnail_strip.mapTo(cw, QPoint(0, 0)).y()
        pos_in_cw = cw.mapFrom(self, event.position().toPoint())
        self._set_drag_hover(pos_in_cw.y() >= strip_top)
        event.acceptProposedAction()

    def dropEvent(self, event):
        self.drag_overlay.hide()
        self.drag_overlay_add.hide()
        # Restore default styles
        self.drag_overlay.setStyleSheet(self._drag_style_active)
        self.drag_overlay_add.setStyleSheet(self._drag_style_dim)

        urls = event.mimeData().urls()
        dropped = []
        for url in urls:
            if url.isLocalFile():
                file_path = url.toLocalFile()
                if file_path.lower().endswith(self.SUPPORTED_EXTENSIONS) \
                        or os.path.isdir(file_path):
                    dropped.append(file_path)
        image_files = self._resolve_input_paths(dropped)

        if not image_files:
            event.ignore()
            return

        # Determine drop zone: below thumbnail strip top → add; above → replace
        cw = self.centralWidget()
        strip_top = self.thumbnail_strip.mapTo(cw, QPoint(0, 0)).y()
        pos_in_cw = cw.mapFrom(self, event.position().toPoint())
        drop_on_strip = self.image_files and (pos_in_cw.y() >= strip_top)

        self._remember_open_directory(image_files[0])
        if drop_on_strip:
            self.add_image_files(image_files)
        else:
            self.load_image_files(image_files)

        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drag_overlay.hide()
        self.drag_overlay_add.hide()
        self.drag_overlay.setStyleSheet(self._drag_style_active)
        self.drag_overlay_add.setStyleSheet(self._drag_style_dim)
        super().dragLeaveEvent(event)


# Compatibility name for project files and third-party imports created before
# the P43 product refactor.
FlashbackEditor = LoFiLogicEditor
