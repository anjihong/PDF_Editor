"""PDF 주석 편집기: 펜, 형광펜(글자 줄 스냅), 텍스트, 메모(호버 표시), 저장, undo/redo.

주석은 PDF 표준 annotation으로 파일에 직접 저장된다. 상태는 PyMuPDF Document 하나가 전부.
"삭제"와 AddAnnot.undo는 실제 삭제 대신 HIDDEN 플래그를 세운다(xref가 안정되어 undo/redo가 단순).
숨긴 주석은 저장 시점에 물리적으로 제거된다.
"""
import re
import sys
import ctypes
import ctypes.wintypes
import hashlib
import os
from functools import cache
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt, QByteArray, QAbstractNativeEventFilter, QEvent, QObject, QRectF, QPointF, QSettings, QSize, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QFontDatabase, QIcon, QImage, QKeySequence,
                           QInputDevice, QPainter, QPalette, QPen, QPointingDevice, QPixmap, QTextCursor,
                           QUndoCommand, QUndoStack)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QColorDialog, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QScrollArea, QSpinBox,
                               QStyle, QStyleOptionSlider, QTextEdit, QToolBar, QToolButton, QToolTip,
                               QVBoxLayout, QWidget)

HIDDEN = pymupdf.PDF_ANNOT_IS_HIDDEN
ERASER_RADIUS_PX = 8
HIGHLIGHT_INK_OPACITY = 0.35


def page_settings_key(path):
    normalized = os.path.normcase(str(Path(path).resolve()))
    return "lastPages/" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def point_segment_distance_sq(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if not length_sq:
        return (point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2
    fraction = max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
    return (point[0] - start[0] - fraction * dx) ** 2 + (point[1] - start[1] - fraction * dy) ** 2


def segments_distance_sq(a, b, c, d):
    def cross(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])

    ab_c, ab_d = cross(a, b, c), cross(a, b, d)
    cd_a, cd_b = cross(c, d, a), cross(c, d, b)
    if (ab_c * ab_d <= 0 and cd_a * cd_b <= 0
            and max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
            and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))):
        return 0
    return min(point_segment_distance_sq(a, c, d), point_segment_distance_sq(b, c, d),
               point_segment_distance_sq(c, a, b), point_segment_distance_sq(d, a, b))


def ink_near_path(annot, start, end, radius):
    width = annot.border.get("width", 1) or 1
    reach = radius + width / 2
    if not annot.rect.intersects(pymupdf.Rect(min(start[0], end[0]) - reach,
                                               min(start[1], end[1]) - reach,
                                               max(start[0], end[0]) + reach,
                                               max(start[1], end[1]) + reach)):
        return False
    for stroke in annot.vertices or []:
        for first, second in zip(stroke, stroke[1:]):
            if segments_distance_sq(start, end, first, second) <= reach * reach:
                return True
        if len(stroke) == 1 and point_segment_distance_sq(stroke[0], start, end) <= reach * reach:
            return True
    return False


class PointerInfo(ctypes.Structure):
    _fields_ = [("pointerType", ctypes.c_uint32), ("pointerId", ctypes.c_uint32),
                ("frameId", ctypes.c_uint32), ("pointerFlags", ctypes.c_uint32),
                ("sourceDevice", ctypes.c_void_p), ("hwndTarget", ctypes.c_void_p),
                ("ptPixelLocation", ctypes.wintypes.POINT),
                ("ptHimetricLocation", ctypes.wintypes.POINT),
                ("ptPixelLocationRaw", ctypes.wintypes.POINT),
                ("ptHimetricLocationRaw", ctypes.wintypes.POINT),
                ("dwTime", ctypes.c_uint32), ("historyCount", ctypes.c_uint32),
                ("InputData", ctypes.c_int32), ("dwKeyStates", ctypes.c_uint32),
                ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_uint32)]


class PointerPenInfo(ctypes.Structure):
    _fields_ = [("pointerInfo", PointerInfo), ("penFlags", ctypes.c_uint32),
                ("penMask", ctypes.c_uint32), ("pressure", ctypes.c_uint32),
                ("rotation", ctypes.c_uint32), ("tiltX", ctypes.c_int32),
                ("tiltY", ctypes.c_int32)]


class PenButtonState(QAbstractNativeEventFilter):
    """Keep the Windows pen flags that Qt omits from its tablet button state."""

    def __init__(self, on_change=None):
        super().__init__()
        self.pointer_id = None
        self.eraser = False
        self.on_change = on_change
        self.get_pen_info = ctypes.windll.user32.GetPointerPenInfo
        self.get_pen_info.argtypes = [ctypes.c_uint32, ctypes.POINTER(PointerPenInfo)]
        self.get_pen_info.restype = ctypes.wintypes.BOOL

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message not in (0x0245, 0x0246, 0x0247, 0x024A):
            return False
        pointer_id = msg.wParam & 0xFFFF
        pen = PointerPenInfo()
        old_eraser = self.eraser
        if self.get_pen_info(pointer_id, ctypes.byref(pen)):
            self.pointer_id = pointer_id
            # This Galaxy Book reports the held S Pen button as PEN_FLAG_ERASER
            # (0x4), while Qt reports only the normal left button.
            self.eraser = bool(pen.penFlags & (0x1 | 0x4))
        if msg.message in (0x0247, 0x024A) and pointer_id == self.pointer_id:
            self.pointer_id = None
            self.eraser = False
        if self.eraser != old_eraser and self.on_change:
            self.on_change()
        return False
FONT_SIZE = 20
DEFAULT_HIGHLIGHT_WIDTH = FONT_SIZE
DEFAULT_COLORS = {"pen": "#426eff", "hl": "#fdff95", "text": "#2864c6"}
PRESETS = {"pen": ["#426eff", "#ff4d4f", "#222222", "#12b886"],
           "hl": ["#fdff95", "#c0fffd", "#ceffc9", "#ffd8d9"],
           "text": ["#2864c6", "#222222", "#e03131", "#12b886"]}


def rgb(hex_color):
    c = QColor(hex_color)
    return (c.redF(), c.greenF(), c.blueF())
THUMB_ZOOM = 0.2

def theme_qss(c):
    return """
QMainWindow, QDialog { background: %(window)s; color: %(text)s; }
QWidget { color: %(text)s; font-family: "Malgun Gothic"; font-size: 10pt; }
QLabel { color: %(text)s; background: transparent; }
QMenuBar, QToolBar, QStatusBar { background: %(chrome)s; color: %(text)s; }
QMenuBar { border-bottom: 1px solid %(border)s; padding: 2px 6px; }
QMenuBar::item { padding: 4px 10px; border-radius: 4px; }
QMenuBar::item:selected { background: %(hover)s; }
QToolBar { border: none; border-bottom: 1px solid %(border)s; padding: 5px 10px; spacing: 3px; }
QToolBar QToolButton, QStatusBar QToolButton, QWidget#dockHeader QToolButton {
    background: transparent; border: 1px solid transparent; border-radius: 5px;
    color: %(text)s; padding: 5px 8px; }
QToolBar QToolButton:hover, QStatusBar QToolButton:hover, QWidget#dockHeader QToolButton:hover { background: %(hover)s; }
QToolBar QToolButton:checked, QToolBar QToolButton:pressed, QStatusBar QToolButton:pressed {
    background: %(selected_bg)s; color: %(accent_text)s; border-color: %(accent_border)s; }
QToolBar QToolButton:disabled, QStatusBar QToolButton:disabled { color: %(disabled)s; }
QToolBar::separator { width: 1px; background: %(separator)s; margin: 5px 8px; }
QStatusBar { border-top: 1px solid %(border)s; min-height: 30px; }
QStatusBar QLabel { color: %(muted)s; }
QDockWidget { background: %(dock)s; border: 0; }
QWidget#dockHeader { background: %(dock_header)s; border-bottom: 1px solid %(dock_border)s; }
QWidget#dockHeader QLabel { color: %(text)s; font-weight: 600; }
QWidget#propertyBody { background: %(dock)s; }
QLabel#propertyTitle { font-size: 13pt; font-weight: 600; color: %(title)s; }
QLabel#propertyHint { color: %(muted)s; line-height: 1.3; }
QLabel#propertyLabel { color: %(label)s; font-weight: 600; }
QListWidget { background: %(dock)s; border: none; outline: none; }
QListWidget::item { color: %(list_text)s; border: 2px solid transparent; border-radius: 5px; padding: 4px; }
QListWidget::item:selected { color: %(list_selected_text)s; background: %(list_selected_bg)s; border-color: %(accent)s; }
QScrollArea { border: none; background: %(window)s; }
QSpinBox { background: %(input)s; color: %(input_text)s; border: 1px solid %(input_border)s;
    border-radius: 5px; padding: 4px 7px; selection-background-color: %(accent_border)s; }
QSpinBox:focus { border-color: %(accent)s; }
QMenu { background: %(menu)s; color: %(menu_text)s; border: 1px solid %(input_border)s; padding: 4px; }
QMenu::item { padding: 6px 26px 6px 24px; }
QMenu::item:selected { background: %(menu_selected)s; }
QMenu::item:disabled { color: %(menu_disabled)s; }
QMenu::separator { height: 1px; background: %(menu_separator)s; margin: 4px 6px; }
QPushButton, QToolButton#customColor { background: %(button)s; color: %(menu_text)s;
    border: 1px solid %(button_border)s; border-radius: 5px; padding: 6px 10px; }
QPushButton:hover, QToolButton#customColor:hover { background: %(button_hover)s; border-color: %(accent)s; }
QPushButton:pressed { background: %(selected_bg)s; }
QScrollBar:vertical { background: %(scroll_bg)s; width: 11px; margin: 0; }
QScrollBar:horizontal { background: %(scroll_bg)s; height: 11px; margin: 0; }
QScrollBar::handle { background: %(scroll_handle)s; border-radius: 5px; min-height: 28px; min-width: 28px; }
QScrollBar::handle:hover { background: %(scroll_hover)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QLabel#scrollPageIndicator { background: %(tooltip)s; color: %(tooltip_text)s;
    border: 1px solid %(tooltip_border)s; border-radius: 6px; padding: 4px 8px; font-weight: 600; }
QToolTip { background: %(tooltip)s; color: %(tooltip_text)s; border: 1px solid %(tooltip_border)s; padding: 5px; }
""" % c

ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"   # exe(onefile)는 _MEIPASS에 풀림

DARK_COLORS = dict(window="#20272d", text="#e8edef", chrome="#20272d", border="#364149",
                   hover="#354249", selected_bg="#244b4e", accent_text="#75e1d5", accent_border="#287f79",
                   accent="#25a89a", disabled="#73818a", separator="#465158", muted="#aebbc1",
                   dock="#273139", dock_header="#29343c", dock_border="#3b4850", title="#f3f6f6",
                   label="#b8c4c9", list_text="#b9c5ca", list_selected_text="#f3f8f8",
                   list_selected_bg="#314449", input="#1e282e", input_text="#f0f4f5",
                   input_border="#4b5b63", menu="#2c373f", menu_text="#edf2f3",
                   menu_selected="#365057", menu_disabled="#839198", menu_separator="#48565e",
                   button="#34434b", button_border="#52636b", button_hover="#40535b",
                   scroll_bg="#263139", scroll_handle="#52616a", scroll_hover="#687a83",
                   tooltip="#34434b", tooltip_text="#f2f5f5", tooltip_border="#64757c",
                   icon="#e8edef", close_icon="#aebbc1")
LIGHT_COLORS = dict(window="#e7ebeb", text="#243139", chrome="#f8faf9", border="#d5dddf",
                    hover="#e8f0ef", selected_bg="#ddf3f0", accent_text="#137b72", accent_border="#25a89a",
                    accent="#25a89a", disabled="#a7b1b5", separator="#d7dedf", muted="#66767e",
                    dock="#f1f4f3", dock_header="#edf1f0", dock_border="#d5dddf", title="#1f2b31",
                    label="#52636b", list_text="#4a5a62", list_selected_text="#193b3a",
                    list_selected_bg="#ddf3f0", input="#ffffff", input_text="#243139",
                    input_border="#c5cfd2", menu="#ffffff", menu_text="#243139",
                    menu_selected="#e5f2f0", menu_disabled="#9ba6aa", menu_separator="#dce2e3",
                    button="#f7f9f8", button_border="#c5cfd2", button_hover="#e7f1ef",
                    scroll_bg="#e3e9e9", scroll_handle="#aebabe", scroll_hover="#8e9da2",
                    tooltip="#ffffff", tooltip_text="#243139", tooltip_border="#b8c4c7",
                    icon="#34434b", close_icon="#75858c")

THEMES = {
    "다크": dict(qss=theme_qss(DARK_COLORS), colors=DARK_COLORS, canvas="#20272d",
               page_border="#b5bec1", shadow=5, shadow_color="#151b1f", dark_title=True,
               chrome=("#20272d", "#e8edef", "#364149"),
               marker=("#f7dc87", "#b08338", "#70521e"),
               palette=("#20272d", "#e8edef", "#1e282e", "#273139", "#34434b", "#287f79", "#ffffff"),
               note_style="QTextEdit{background:#fff8d7;color:#242c2f;border:1px solid #d7b963;border-radius:5px;padding:5px}"),
    "라이트": dict(qss=theme_qss(LIGHT_COLORS), colors=LIGHT_COLORS, canvas="#e7ebeb",
                page_border="#bcc5c8", shadow=5, shadow_color="#c4ccce", dark_title=False,
                chrome=("#f8faf9", "#243139", "#d5dddf"),
                marker=("#f7dc87", "#b08338", "#70521e"),
                palette=("#f8faf9", "#243139", "#ffffff", "#f1f4f3", "#f7f9f8", "#25a89a", "#ffffff"),
                note_style="QTextEdit{background:#fff8d7;color:#242c2f;border:1px solid #d7b963;border-radius:5px;padding:5px}"),
}

# 화면 전체에 같은 굵기의 선형 아이콘을 사용한다. 파일 아이콘과 겹치지 않도록 UI 전용 경로로 둔다.
UI_ICONS = {
    "folder": '<path d="M2 6h7l2 2h11v12H2z"/><path d="M2 6V4h7l2 2"/>',
    "save": '<path d="M4 3h14l3 3v15H3V3z"/><path d="M7 3v7h10V3M7 21v-8h10v8"/>',
    "cursor": '<path d="M4 3l15 9-7 1-3 7z"/>',
    "pencil": '<path d="M4 20l4.5-1 11-11-3.5-3.5L5 15.5zM14.5 6l3.5 3.5"/>',
    "brush": '<path d="M4 18h12M7 15l9-10 4 3-9 10H7zM14 7l4 3"/>',
    "text": '<path d="M4 5h16M12 5v15M7 20h10"/>',
    "note": '<path d="M4 3h16v14l-4 4H4zM16 21v-4h4M8 8h8M8 12h8"/>',
    "eraser": '<path d="M3 15l10-11 8 8-8 9H8zM8 21l-3-3M10 18h9"/>',
    "undo": '<path d="M9 5L4 10l5 5M4 10h10a6 6 0 0 1 0 12"/>',
    "redo": '<path d="M15 5l5 5-5 5M20 10H10a6 6 0 0 0 0 12"/>',
    "zoom-out": '<circle cx="10" cy="10" r="7"/><path d="M15 15l6 6M7 10h6"/>',
    "zoom-in": '<circle cx="10" cy="10" r="7"/><path d="M15 15l6 6M7 10h6M10 7v6"/>',
    "layout": '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M9 3v18"/>',
    "panel": '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M15 3v18"/>',
    "close": '<path d="M5 5l14 14M19 5L5 19"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/>',
    "moon": '<path d="M20 15.5A8 8 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5z"/>',
}


@cache
def ui_icon(name, color="#e8edef", dpr=1.0):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
           f'{UI_ICONS[name]}</svg>').encode()
    px = max(1, round(22 * dpr))
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    QSvgRenderer(QByteArray(svg)).render(p)
    p.end()
    img.setDevicePixelRatio(dpr)
    return QIcon(QPixmap.fromImage(img))


# ---------- 순수 PyMuPDF 헬퍼 (GUI 없이 테스트 가능) ----------

def line_quads(page, rect, words=None):
    """rect(비회전 좌표)와 겹치는 단어들을 (block, line)별로 합쳐 줄당 quad 하나씩 반환."""
    lines = {}
    for x0, y0, x1, y1, _w, b, l, _n in (words if words is not None else page.get_text("words")):
        r = pymupdf.Rect(x0, y0, x1, y1)
        if r.intersects(rect):
            lines[(b, l)] = lines[(b, l)] | r if (b, l) in lines else r
    return [r.quad for r in lines.values()]


def segment_word_hits(start, end, words, tolerance=0):
    """Return words touched by a stroke segment in unrotated PDF coordinates."""
    a, b = (start.x, start.y), (end.x, end.y)
    for x0, y0, x1, y1, _word, block, line, _number in words:
        rect = pymupdf.Rect(x0 - tolerance, y0 - tolerance, x1 + tolerance, y1 + tolerance)
        if rect.contains(start) or rect.contains(end):
            yield (block, line), pymupdf.Rect(x0, y0, x1, y1)
            continue
        corners = [(rect.x0, rect.y0), (rect.x1, rect.y0),
                   (rect.x1, rect.y1), (rect.x0, rect.y1)]
        if any(segments_distance_sq(a, b, corners[i], corners[(i + 1) % 4]) == 0
               for i in range(4)):
            yield (block, line), pymupdf.Rect(x0, y0, x1, y1)


def text_rect(pt, text, fs=FONT_SIZE):
    # ponytail: 글자 수 × fs 로 폭 추정(CJK 기준, 라틴은 여유 생김). 정확한 폭 필요하면 pymupdf.get_text_length.
    lines = text.split("\n")
    w = max(len(l) for l in lines) * fs + 8
    h = len(lines) * fs * 1.45 + 6
    return pymupdf.Rect(pt.x, pt.y, pt.x + w, pt.y + h)


def make_ink(page, pts, color, width):
    a = page.add_ink_annot([[(p.x, p.y) for p in pts]])
    a.set_colors(stroke=color)
    a.set_border(width=width)
    a.update()
    return a


def make_highlight(page, quads, color):
    a = page.add_highlight_annot(quads)
    a.set_colors(stroke=color)
    a.update()
    return a


def make_highlight_ink(page, pts, color, width):
    a = make_ink(page, pts, color, width)
    a.update(opacity=HIGHLIGHT_INK_OPACITY)
    return a


def make_text(page, pt, text, color, fs=FONT_SIZE):
    return page.add_freetext_annot(text_rect(pt, text, fs), text, fontsize=fs, text_color=color)


def annot_fontsize(doc, xref):
    m = re.search(r"([\d.]+)\s+Tf", doc.xref_get_key(xref, "DA")[1] or "")
    return float(m.group(1)) if m else FONT_SIZE


def make_note(page, pt, text):
    a = page.add_text_annot(pt, text)
    a.set_info(content=text)
    a.update()
    return a


def set_hidden(page, xref, hidden):
    a = page.load_annot(xref)
    a.set_flags(a.flags | HIDDEN if hidden else a.flags & ~HIDDEN)


def set_content(page, xref, text, rect=None, fontsize=None):
    a = page.load_annot(xref)
    a.set_info(content=text)
    if rect is not None:
        a.set_rect(rect)
    a.update(fontsize=fontsize) if fontsize else a.update()


def purge_hidden(doc):
    """숨긴 주석 물리 삭제. 삭제한 개수 반환."""
    n = 0
    for page in doc:
        for xref in [a.xref for a in page.annots() if a.flags & HIDDEN]:
            page.delete_annot(page.load_annot(xref))
            n += 1
    return n


def to_qimage(page, zoom):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    return QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()


def thumbnail_icon(page):
    pixmap = QPixmap.fromImage(to_qimage(page, THUMB_ZOOM))
    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Normal)
    icon.addPixmap(pixmap, QIcon.Selected)
    return icon


# ---------- Undo 커맨드 ----------

class Cmd(QUndoCommand):
    def __init__(self, win, pno, text):
        super().__init__(text)
        self.win, self.pno = win, pno

    @property
    def page(self):
        return self.win.doc[self.pno]

    def refresh(self):
        self.win.pages[self.pno].invalidate()


class AddAnnot(Cmd):
    def __init__(self, win, pno, make, text):
        super().__init__(win, pno, text)
        self.make, self.xref = make, None

    def redo(self):
        if self.xref is None:
            self.xref = self.make(self.page).xref
        else:
            set_hidden(self.page, self.xref, False)
        self.refresh()

    def undo(self):
        set_hidden(self.page, self.xref, True)
        self.refresh()


class SetHidden(Cmd):
    def __init__(self, win, pno, xref, text="삭제"):
        super().__init__(win, pno, text)
        self.xref = xref

    def redo(self):
        set_hidden(self.page, self.xref, True)
        self.refresh()

    def undo(self):
        set_hidden(self.page, self.xref, False)
        self.refresh()


class SetContent(Cmd):
    def __init__(self, win, pno, xref, new, new_rect=None, text="메모", fontsize=None):
        super().__init__(win, pno, text)
        page = self.page   # Annot이 Page를 약참조하므로 a.rect 읽는 동안 살려둠
        a = page.load_annot(xref)
        self.xref, self.old, self.new = xref, a.info["content"], new
        self.old_rect, self.new_rect = (a.rect, new_rect) if new_rect is not None else (None, None)
        self.old_fs, self.new_fs = (annot_fontsize(win.doc, xref), fontsize) if fontsize else (None, None)

    def redo(self):
        set_content(self.page, self.xref, self.new, self.new_rect, self.new_fs)
        self.refresh()

    def undo(self):
        set_content(self.page, self.xref, self.old, self.old_rect, self.old_fs)
        self.refresh()


class MoveAnnot(Cmd):
    def __init__(self, win, pno, xref, old_rect, new_rect, text="이동"):
        super().__init__(win, pno, text)
        self.xref, self.old_rect, self.new_rect = xref, old_rect, new_rect

    def apply(self, rect):
        a = self.page.load_annot(self.xref)
        a.set_rect(rect)
        a.update()
        self.refresh()

    def redo(self):
        self.apply(self.new_rect)

    def undo(self):
        self.apply(self.old_rect)


# ---------- 인라인 에디터 ----------

class InlineEditor(QTextEdit):
    """페이지 위에 바로 뜨는 입력창. 포커스 잃거나 Ctrl+Enter면 commit, Esc면 취소. Enter는 줄바꿈."""

    def __init__(self, parent, pos, text, font_px, style, on_done, width=None):
        super().__init__(parent)
        self.on_done, self.done, self.fixed_w = on_done, False, width
        self.setAcceptRichText(False)
        self.setStyleSheet(style)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth if width else QTextEdit.NoWrap)
        f = self.font()
        f.setPixelSize(font_px)
        self.setFont(f)
        self.setPlainText(text)
        self.moveCursor(QTextCursor.End)
        self.textChanged.connect(self.fit)
        self.move(pos)
        self.fit()
        self.show()
        self.setFocus()

    def set_font_px(self, px):
        if self.done:
            return
        f = self.font()
        f.setPixelSize(px)
        self.setFont(f)
        self.fit()

    def fit(self):
        doc = self.document()
        w = self.fixed_w or max(40, int(doc.idealWidth()) + 12)
        self.resize(w, max(24, int(doc.size().height()) + 12))

    def finish(self, ok):
        if self.done:
            return
        self.done = True
        text = self.toPlainText().strip() if ok else None
        self.hide()
        self.deleteLater()
        self.on_done(text or None)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.finish(True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.finish(False)
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ControlModifier:
            self.finish(True)
        else:
            super().keyPressEvent(e)


# ---------- 배경 터치 이동 ----------

class BackgroundPan(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.canvas = None
        self.target = None
        self.touch_id = None
        self.last_pos = None

    def set_canvas(self, canvas):
        self.target = self.touch_id = self.last_pos = None
        self.canvas = canvas
        canvas.setAttribute(Qt.WA_AcceptTouchEvents)
        canvas.installEventFilter(self)

    def on_page(self, global_pos):
        if self.canvas is None:
            return False
        child = self.canvas.childAt(self.canvas.mapFromGlobal(global_pos.toPoint()))
        while child is not None and child is not self.canvas:
            if isinstance(child, PageWidget):
                return True
            child = child.parentWidget()
        return False

    def eventFilter(self, obj, e):
        kind = e.type()
        if kind not in (QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TouchCancel):
            return False
        if kind == QEvent.TouchBegin:
            device = e.device()
            if self.touch_id is not None or device is None or device.type() != QInputDevice.DeviceType.TouchScreen:
                return False
            points = e.points()
            if not points or self.on_page(points[0].globalPosition()):
                e.ignore()
                return False
            self.target = obj
            self.touch_id = points[0].id()
            self.last_pos = points[0].globalPosition()
            e.accept()
            return True
        if self.touch_id is None or obj is not self.target:
            e.ignore()
            return False
        if kind == QEvent.TouchCancel:
            self.target = self.touch_id = self.last_pos = None
            e.accept()
            return True
        point = next((p for p in e.points() if p.id() == self.touch_id), None)
        if kind == QEvent.TouchUpdate and point is not None:
            pos = point.globalPosition()
            delta = pos - self.last_pos
            hbar, vbar = self.win.scroll.horizontalScrollBar(), self.win.scroll.verticalScrollBar()
            hbar.setValue(hbar.value() - round(delta.x()))
            vbar.setValue(vbar.value() - round(delta.y()))
            self.last_pos = pos
        if kind == QEvent.TouchEnd:
            self.target = self.touch_id = self.last_pos = None
        elif point is None and e.points():
            self.touch_id = e.points()[0].id()
            self.last_pos = e.points()[0].globalPosition()
        e.accept()
        return True


# ---------- 페이지 위젯 ----------

class PageWidget(QWidget):
    def __init__(self, win, pno):
        super().__init__()
        self.win, self.pno = win, pno
        self.img = None
        self.words = None    # get_text("words") 캐시
        self.pts = []        # 펜 드래그 중 점(위젯 좌표)
        self.hl_points = []  # 형광펜 드래그 중 점(위젯 좌표)
        self.hl_lines = {}   # 닿은 단어를 (block, line)별로 합친 영역
        self.preview = []    # 형광펜 미리보기 quads
        self.moving = None   # 텍스트 드래그 이동 [xref, 시작, 원래 rect, 현재]
        self.erased = None   # 지우개 드래그 중 숨긴 xref 목록
        self.erase_previous = None
        self.erase_excluded = set()
        self.pen_input = None  # 펜촉이 닿아 있는 동안의 실제 동작: pen / erase
        self.tool_cursor = Qt.ArrowCursor
        self.tip_xref = None
        self.setMouseTracking(True)
        self.invalidate(thumb=False)

    @property
    def page(self):
        return self.win.doc[self.pno]

    @property
    def zoom(self):
        return self.win.zoom

    def invalidate(self, thumb=True):
        self.img = None
        r, s = self.page.rect, self.win.theme["shadow"]
        self.setFixedSize(int(r.width * self.zoom) + s, int(r.height * self.zoom) + s)
        self.update()
        if thumb:
            self.win.update_thumb(self.pno)

    def get_words(self):
        if self.words is None:
            self.words = self.page.get_text("words")
        return self.words

    def to_pdf(self, qp):
        return pymupdf.Point(qp.x() / self.zoom, qp.y() / self.zoom) * self.page.derotation_matrix

    def to_widget(self, rect):
        r, z = rect * self.page.rotation_matrix, self.zoom
        return QRectF(r.x0 * z, r.y0 * z, r.width * z, r.height * z)

    def annot_at(self, pt):
        self._page = self.page   # Annot은 Page를 약참조하므로 반환 후에도 살려둠
        for a in self._page.annots():
            if not a.flags & HIDDEN and a.rect.contains(pt):
                return a
        return None

    def paintEvent(self, _e):
        dpr = self.devicePixelRatioF()
        if self.img is None:
            self.img = to_qimage(self.page, self.zoom * dpr)
            self.img.setDevicePixelRatio(dpr)
        theme, s = self.win.theme, self.win.theme["shadow"]
        paper = QRectF(0, 0, self.width() - s, self.height() - s)
        p = QPainter(self)
        if s:
            p.fillRect(paper.translated(s, s), QColor(theme["shadow_color"]))
        p.drawImage(0, 0, self.img)
        p.setPen(QPen(QColor(theme["page_border"]), 1))
        p.drawRect(paper.adjusted(0, 0, -1, -1))
        p.setRenderHint(QPainter.Antialiasing)
        # 메모 표식: 내용 있는 형광펜/펜 주석 우상단에 말풍선
        page = self.page
        for a in page.annots():
            if a.info["content"] and a.type[0] not in (pymupdf.PDF_ANNOT_FREE_TEXT, pymupdf.PDF_ANNOT_TEXT) \
                    and not a.flags & HIDDEN:
                r = self.to_widget(a.rect)
                m = QRectF(r.right() - 7, r.top() - 9, 16, 14)
                fill, edge, line = theme["marker"]
                p.setPen(QPen(QColor(edge), 1))
                p.setBrush(QColor(fill))
                p.drawRoundedRect(m, 3, 3)
                p.setPen(QPen(QColor(line), 1.5))
                for i in range(3):
                    p.drawLine(QPointF(m.left() + 4, m.top() + 4 + i * 3), QPointF(m.right() - 4, m.top() + 4 + i * 3))
        color = QColor(self.win.colors.get(self.win.tool, "#000000"))
        if len(self.pts) > 1:
            p.setPen(QPen(color, self.win.width * self.zoom, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline(self.pts)
        if self.moving and (self.moving[3] - self.moving[1]).manhattanLength() > 4:
            p.setPen(QPen(QColor("#25a89a"), 1, Qt.DashLine))
            p.setBrush(QColor(37, 168, 154, 30))
            p.drawRect(self.to_widget(self.moving[2]).translated(self.moving[3] - self.moving[1]))
        if self.preview:
            color.setAlpha(110)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            for q in self.preview:
                p.drawRect(self.to_widget(q.rect))
        elif len(self.hl_points) > 1:
            color.setAlpha(round(255 * HIGHLIGHT_INK_OPACITY))
            p.setPen(QPen(color, self.win.hl_width * self.zoom, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline(self.hl_points)

    # --- 펜 / 마우스 ---
    def update_pen_input(self, pos, mode):
        if mode != self.pen_input:
            previous_mode = self.pen_input
            if self.pen_input is not None:
                self.move_pen_input(pos)
                last_ink = self.finish_pen_input()
            else:
                last_ink = None
            self.pen_input = mode
            if mode == "pen":
                self.pts = [pos]
            elif mode == "erase":
                self.erased = []
                self.erase_previous = None
                self.erase_excluded = {last_ink} if previous_mode == "pen" and last_ink is not None else set()
                self.erase_at(self.to_pdf(pos))
            self.setCursor(Qt.PointingHandCursor if mode == "erase" else self.tool_cursor)
        elif mode is not None:
            self.move_pen_input(pos)

    def move_pen_input(self, pos):
        if self.pen_input == "pen" and self.pts and pos != self.pts[-1]:
            self.pts.append(pos)
            self.update()
        elif self.pen_input == "erase" and self.erased is not None:
            self.erase_at(self.to_pdf(pos))

    def finish_pen_input(self):
        last_ink = None
        if self.pen_input == "pen":
            last_ink = self.finish_ink()
        elif self.pen_input == "erase":
            self.finish_erase()
        self.pen_input = None
        return last_ink

    def tabletEvent(self, e):
        buttons = e.buttons()
        tip_down = bool(buttons & Qt.LeftButton) or e.pressure() > 0
        self.win.set_qt_pen_eraser(tip_down and e.pointerType() == QPointingDevice.PointerType.Eraser)
        if self.win.temporary_eraser or self.win.tool == "erase":
            mode = "erase" if tip_down else None
        elif self.win.tool == "pen":
            mode = ("erase" if buttons & Qt.RightButton else "pen") if tip_down else None
        elif self.win.tool == "hl":
            if self.pen_input is not None:
                self.update_pen_input(e.position(), None)
            if tip_down:
                if not self.hl_points:
                    self.start_highlight(e.position())
                else:
                    self.move_highlight(e.position())
            elif self.hl_points:
                self.finish_highlight(e.position())
            else:
                self.hover_at(e.position(), e.globalPosition())
            e.accept()
            return
        else:
            if self.pen_input is not None:
                self.update_pen_input(e.position(), None)
                e.accept()
            else:
                e.ignore()
            return
        self.update_pen_input(e.position(), mode)
        if mode is None:
            self.hover_at(e.position(), e.globalPosition())
        e.accept()  # 처리한 펜 이벤트가 다시 마우스 이벤트로 전달되지 않게 함

    def mouse_input_mode(self, e):
        buttons = e.buttons()
        if self.win.temporary_eraser and buttons & (Qt.LeftButton | Qt.RightButton):
            return "erase"
        if self.win.tool == "erase" and buttons & Qt.LeftButton:
            return "erase"
        if self.win.tool != "pen":
            return None
        if buttons & Qt.RightButton:
            return "erase"
        if buttons & Qt.LeftButton:
            return "pen"
        return None

    def mousePressEvent(self, e):
        mode = self.mouse_input_mode(e)
        if mode is not None and e.button() in (Qt.LeftButton, Qt.RightButton):
            self.update_pen_input(e.position(), mode)
            e.accept()
            return
        if e.button() != Qt.LeftButton:
            return
        t, pt = self.win.tool, self.to_pdf(e.position())
        a = self.annot_at(pt)
        if a and a.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT and t in (None, "text"):
            self.moving = [a.xref, e.position(), a.rect, e.position()]   # 릴리즈 때 클릭이면 편집, 드래그면 이동
            return
        if a and t is None and a.info["content"]:
            return self.start_note(pt, a.xref)
        if t == "hl":
            self.start_highlight(e.position())
        elif t == "text":
            self.start_text(pt)
        elif t == "note":
            self.start_note(pt)
        elif t == "erase":
            self.erased = []
            self.erase_previous = None
            self.erase_excluded = set()
            self.erase_at(pt)

    def erase_at(self, pt):
        start = self.erase_previous or pt
        self.erase_previous = pt
        self._page = self.page
        radius = ERASER_RADIUS_PX / self.zoom
        start_xy, end_xy = (start.x, start.y), (pt.x, pt.y)
        hits = [a.xref for a in self._page.annots()
                if not a.flags & HIDDEN and a.xref not in self.erase_excluded
                and a.type[0] == pymupdf.PDF_ANNOT_INK
                and ink_near_path(a, start_xy, end_xy, radius)]
        if not hits:
            a = self.annot_at(pt)  # 다른 주석 종류는 기존의 정확한 영역 판정을 유지
            if a is not None and a.xref not in self.erase_excluded:
                hits = [a.xref]
        for xref in hits:
            if xref not in self.erased:
                self.erased.append(xref)
                set_hidden(self._page, xref, True)   # 즉시 숨김, 커맨드는 릴리즈 때 한 번에
        if hits:
            self.invalidate(thumb=False)

    def mouseMoveEvent(self, e):
        mode = self.mouse_input_mode(e)
        if self.pen_input is not None or mode is not None:
            self.update_pen_input(e.position(), mode)
            return
        if self.erased is not None:
            self.erase_at(self.to_pdf(e.position()))
        elif self.hl_points:
            self.move_highlight(e.position())
        elif self.moving:
            self.moving[3] = e.position()
            self.update()
        else:
            self.hover_at(e.position(), e.globalPosition())

    def hover_at(self, pos, global_pos):
        a = self.annot_at(self.to_pdf(pos))
        is_text = (not self.win.temporary_eraser and a is not None
                   and a.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT and self.win.tool in (None, "text"))
        self.setCursor(Qt.SizeAllCursor if is_text else self.tool_cursor)
        content = a.info["content"] if a and a.type[0] != pymupdf.PDF_ANNOT_FREE_TEXT else ""
        if content:
            if a.xref != self.tip_xref:
                QToolTip.showText(global_pos.toPoint(), content, self)
            self.tip_xref = a.xref
        else:
            QToolTip.hideText()
            self.tip_xref = None

    def start_highlight(self, pos):
        self.hl_points = [pos]
        self.hl_lines = {}
        self._match_highlight_segment(pos, pos)
        self.update()

    def _match_highlight_segment(self, start, end):
        tolerance = 1 / self.zoom
        for key, rect in segment_word_hits(self.to_pdf(start), self.to_pdf(end), self.get_words(), tolerance):
            self.hl_lines[key] = self.hl_lines[key] | rect if key in self.hl_lines else rect
        self.preview = [rect.quad for rect in self.hl_lines.values()]

    def move_highlight(self, pos):
        if pos != self.hl_points[-1]:
            self._match_highlight_segment(self.hl_points[-1], pos)
            self.hl_points.append(pos)
            self.update()

    def cancel_highlight(self):
        self.hl_points = []
        self.hl_lines = {}
        self.preview = []
        self.update()

    def finish_highlight(self, pos):
        self.move_highlight(pos)
        quads = self.preview
        pts = [self.to_pdf(p) for p in self.hl_points]
        self.cancel_highlight()
        color = rgb(self.win.colors["hl"])
        if quads:
            self.win.undo.push(AddAnnot(self.win, self.pno,
                                        lambda page: make_highlight(page, quads, color), "형광펜"))
        elif len(pts) > 1:
            width = self.win.hl_width
            self.win.undo.push(AddAnnot(self.win, self.pno,
                                        lambda page: make_highlight_ink(page, pts, color, width), "형광펜"))

    def finish_erase(self):
        xrefs, self.erased = self.erased, None
        self.erase_previous = None
        self.erase_excluded = set()
        if xrefs:
            self.win.undo.beginMacro("지우기")
            for x in xrefs:
                self.win.undo.push(SetHidden(self.win, self.pno, x))
            self.win.undo.endMacro()

    def finish_ink(self):
        pts = [self.to_pdf(p) for p in self.pts]
        self.pts = []
        xref = None
        if len(pts) > 1:
            c, w = rgb(self.win.colors["pen"]), self.win.width
            command = AddAnnot(self.win, self.pno, lambda page: make_ink(page, pts, c, w), "펜")
            self.win.undo.push(command)
            xref = command.xref
        self.update()
        return xref

    def mouseReleaseEvent(self, e):
        if self.pen_input is not None or (self.win.temporary_eraser or self.win.tool == "pen") \
                and e.button() in (Qt.LeftButton, Qt.RightButton):
            self.update_pen_input(e.position(), self.mouse_input_mode(e))
            e.accept()
            return
        win, pno = self.win, self.pno
        if self.moving:
            xref, start, rect, cur = self.moving
            self.moving = None
            if (cur - start).manhattanLength() > 4:
                d = self.to_pdf(cur) - self.to_pdf(start)
                win.undo.push(MoveAnnot(win, pno, xref, rect, rect + (d.x, d.y, d.x, d.y)))
            else:
                self.start_text(self.to_pdf(start), xref)
            self.update()
        elif self.erased is not None:
            self.finish_erase()
        elif self.pts:
            self.finish_ink()
        elif self.hl_points and e.button() == Qt.LeftButton:
            self.finish_highlight(e.position())

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.win.set_zoom(self.win.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
        else:
            e.ignore()

    def contextMenuEvent(self, e):
        win, pno = self.win, self.pno
        if win.tool == "pen" or win.temporary_eraser:
            e.accept()
            return
        if win.tool:                 # 도구 켜진 상태에서 우클릭 = 도구 취소
            return win.set_tool(None)
        pt = self.to_pdf(QPointF(e.pos()))
        a = self.annot_at(pt)
        m = QMenu(self)
        if a is None:
            m.addAction("여기에 메모", lambda: self.start_note(pt))
            m.addAction("여기에 텍스트", lambda: self.start_text(pt))
        else:
            xref = a.xref
            if a.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT:
                m.addAction("텍스트 편집", lambda: self.start_text(pt, xref))
            else:
                m.addAction("메모 수정" if a.info["content"] else "메모 추가", lambda: self.start_note(pt, xref))
            m.addAction("삭제", lambda: win.undo.push(SetHidden(win, pno, xref)))
        m.exec(e.globalPos())

    # --- 인라인 편집 ---
    def start_text(self, pt, xref=None):
        win, pno, z = self.win, self.pno, self.zoom
        c = win.colors["text"]
        style = f"QTextEdit{{background:transparent;color:{c};border:1px dashed {c};padding:0}}"
        page = self.page   # Annot이 Page를 약참조하므로 살려둠
        if xref is not None:
            a = page.load_annot(xref)
            old, rect = a.info["content"], a.rect
            win.font_spin.setValue(round(annot_fontsize(win.doc, xref)))   # 편집 중 크기 바꾸면 그 텍스트에 적용
            set_hidden(page, xref, True)     # 편집 중엔 원본 숨김(undo 스택 안 거침)
            self.invalidate(thumb=False)
        else:
            old, rect = "", pymupdf.Rect(pt, pt)
        pos = self.to_widget(rect).topLeft().toPoint()
        fs0 = win.font_size

        def done(text):
            fs = win.font_size
            if xref is not None:
                set_hidden(self.page, xref, False)
                if text and (text != old or fs != fs0):
                    win.undo.push(SetContent(win, pno, xref, text, text_rect(rect.top_left, text, fs), "텍스트 편집", fs))
                else:
                    self.invalidate(thumb=False)
            elif text:
                col = rgb(win.colors["text"])
                win.undo.push(AddAnnot(win, pno, lambda page: make_text(page, pt, text, col, fs), "텍스트"))
        ed = InlineEditor(self, pos, old, round(win.font_size * z), style, done)
        ed.setFont(win.base_font)
        ed.set_font_px(round(win.font_size * z))
        win.font_spin.valueChanged.connect(lambda v: ed.set_font_px(round(v * z)))

    def start_note(self, pt, xref=None):
        win, pno = self.win, self.pno
        QToolTip.hideText()
        style = win.theme["note_style"]
        page = self.page
        if xref is not None:
            a = page.load_annot(xref)
            old, pos = a.info["content"], self.to_widget(a.rect).topRight().toPoint()
        else:
            old, pos = "", self.to_widget(pymupdf.Rect(pt, pt)).topLeft().toPoint()

        def done(text):
            if xref is not None:
                if text and text != old:
                    win.undo.push(SetContent(win, pno, xref, text))
            elif text:
                win.undo.push(AddAnnot(win, pno, lambda page: make_note(page, pt, text), "메모"))
        InlineEditor(self, pos, old, 13, style, done, width=220)


# ---------- 메인 창 ----------

TOOLS = [("펜", "pen", "pencil"), ("형광펜", "hl", "brush"), ("텍스트", "text", "text"),
         ("메모", "note", "note"), ("지우개", "erase", "eraser")]


class Win(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.pen_button = PenButtonState(self.on_native_pen_button_changed) if sys.platform == "win32" else None
        self.qt_pen_eraser = False
        self._temporary_displayed = False
        self.doc, self.path, self.pages = None, None, []
        self._document_generation = 0
        self._zoom_generation = 0
        self._restoring_page = False
        self._last_seen_page = None
        self.zoom, self.tool, self.width, self.font_size = 1.5, None, 2, FONT_SIZE
        self.fit_mode = False
        self.hl_width = DEFAULT_HIGHLIGHT_WIDTH
        self.colors = dict(DEFAULT_COLORS)
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(self.on_clean_changed)
        self.setAcceptDrops(True)
        self.resize(1200, 900)
        saved_theme = str(QSettings("pdf-editor", "PdfEditor").value("theme", "다크"))
        self.theme_name = saved_theme if saved_theme in THEMES else "다크"
        self.theme = THEMES[self.theme_name]
        self.icon_targets = []
        # offscreen 실행에서도 Windows의 한글 글꼴을 사용할 수 있도록 등록한다.
        malgun = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
        if sys.platform == "win32" and malgun.exists() and "Malgun Gothic" not in QFontDatabase.families():
            QFontDatabase.addApplicationFont(str(malgun))
        self.base_font = QFont("Malgun Gothic", 10)
        QApplication.setFont(self.base_font)
        self.apply_application_theme()

        tb = self.tb = QToolBar("도구")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        file_menu = self.menuBar().addMenu("파일")
        view_menu = self.menuBar().addMenu("보기")

        self.open_act = QAction("열기", self, shortcut=QKeySequence.Open)
        self.set_themed_icon(self.open_act, "folder")
        self.open_act.triggered.connect(self.open_dialog)
        self.save_act = QAction("저장", self, shortcut=QKeySequence.Save)
        self.set_themed_icon(self.save_act, "save")
        self.save_act.triggered.connect(self.save)
        self.save_as_act = QAction("다른 이름으로 저장", self, shortcut=QKeySequence.SaveAs)
        self.save_as_act.triggered.connect(self.save_as)
        for act in (self.open_act, self.save_act, self.save_as_act):
            file_menu.addAction(act)
        tb.addAction(self.open_act)
        tb.addAction(self.save_act)
        tb.addSeparator()

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)   # 다시 누르면 꺼짐 = 선택 모드
        select_act = QAction("선택", self, checkable=True)
        self.set_themed_icon(select_act, "cursor")
        select_act.setData(None)
        self.tool_group.addAction(select_act)
        tb.addAction(select_act)
        for i, (name, tool, icon) in enumerate(TOOLS):
            act = QAction(name, self, checkable=True, shortcut=str(i + 1))
            self.set_themed_icon(act, icon)
            act.setData(tool)
            act.setToolTip(f"{name} ({i + 1})")
            self.tool_group.addAction(act)
            tb.addAction(act)
        self.tool_group.triggered.connect(lambda act: self.set_tool(act.data() if act.isChecked() else None))
        select_act.setChecked(True)
        tb.addSeparator()

        for act, key, icon in [(self.undo.createUndoAction(self, "↶"), QKeySequence.Undo, "undo"),
                               (self.undo.createRedoAction(self, "↷"), QKeySequence.Redo, "redo")]:
            act.setShortcuts(key)
            self.set_themed_icon(act, icon)
            tb.addAction(act)
            button = tb.widgetForAction(act)
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setToolTip("실행 취소" if icon == "undo" else "다시 실행")

        self.scroll = QScrollArea(widgetResizable=True)
        self.background_pan = BackgroundPan(self)
        self.scroll.viewport().setAttribute(Qt.WA_AcceptTouchEvents)
        self.scroll.viewport().installEventFilter(self.background_pan)
        self.scroll.verticalScrollBar().valueChanged.connect(self.update_page_label)
        self.setCentralWidget(self.scroll)

        self.scroll_page_indicator = QLabel("", self.scroll.viewport())
        self.scroll_page_indicator.setObjectName("scrollPageIndicator")
        self.scroll_page_indicator.setAlignment(Qt.AlignCenter)
        self.scroll_page_indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.scroll_page_indicator.hide()
        self.scroll_page_timer = QTimer(self)
        self.scroll_page_timer.setSingleShot(True)
        self.scroll_page_timer.setInterval(700)
        self.scroll_page_timer.timeout.connect(self.scroll_page_indicator.hide)

        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setFlow(QListWidget.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setMovement(QListWidget.Static)
        self.thumbs.setResizeMode(QListWidget.Adjust)
        self.thumbs.setIconSize(QSize(140, 140))
        self.thumbs.setSpacing(6)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.currentRowChanged.connect(self.goto_page)
        self.pages_dock = self.make_dock("페이지", self.thumbs, Qt.LeftDockWidgetArea, 190)
        self.properties_dock = self.make_dock("속성", self.make_properties(), Qt.RightDockWidgetArea, 220)
        self.pages_dock.visibilityChanged.connect(self.schedule_refit)
        self.properties_dock.visibilityChanged.connect(self.schedule_refit)
        for dock, title, icon in ((self.pages_dock, "페이지 목록", "layout"),
                                  (self.properties_dock, "속성 패널", "panel")):
            toggle = dock.toggleViewAction()
            toggle.setText(title)
            self.set_themed_icon(toggle, icon)
            view_menu.addAction(toggle)
        self.resizeDocks([self.pages_dock, self.properties_dock], [205, 240], Qt.Horizontal)

        self.zoom_out_act = QAction("축소", self, shortcut=QKeySequence.ZoomOut)
        self.set_themed_icon(self.zoom_out_act, "zoom-out")
        self.zoom_out_act.triggered.connect(lambda: self.set_zoom(self.zoom / 1.2))
        self.zoom_act = QAction("100%", self)
        self.zoom_act.triggered.connect(self.fit_width)
        self.zoom_in_act = QAction("확대", self, shortcut=QKeySequence.ZoomIn)
        self.set_themed_icon(self.zoom_in_act, "zoom-in")
        self.zoom_in_act.triggered.connect(lambda: self.set_zoom(self.zoom * 1.2))
        view_menu.addSeparator()
        view_menu.addAction(self.zoom_out_act)
        view_menu.addAction("너비 맞춤", self.fit_width)
        view_menu.addAction(self.zoom_in_act)
        view_menu.addSeparator()
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for name, icon in (("다크", "moon"), ("라이트", "sun")):
            action = QAction(f"{name} 테마", self, checkable=True)
            action.setData(name)
            self.set_themed_icon(action, icon)
            self.theme_group.addAction(action)
            self.theme_actions[name] = action
            view_menu.addAction(action)
        self.theme_group.triggered.connect(lambda action: self.apply_theme(action.data()))

        self.page_spin = QSpinBox(minimum=1, maximum=1)
        self.page_spin.editingFinished.connect(lambda: self.goto_page(self.page_spin.value() - 1))
        self.page_label = QLabel("/ 0")
        self.page_spin.setFixedWidth(58)
        page_controls = QWidget()
        page_layout = QHBoxLayout(page_controls)
        page_layout.setContentsMargins(7, 0, 14, 0)
        page_layout.setSpacing(5)
        for w in (QLabel("페이지"), self.page_spin, self.page_label):
            page_layout.addWidget(w)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(page_controls)

        zoom_controls = QWidget()
        zoom_layout = QHBoxLayout(zoom_controls)
        zoom_layout.setContentsMargins(0, 0, 8, 0)
        zoom_layout.setSpacing(3)
        for act in (self.zoom_out_act, self.zoom_act, self.zoom_in_act):
            button = QToolButton()
            button.setDefaultAction(act)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly if act == self.zoom_act else Qt.ToolButtonIconOnly)
            zoom_layout.addWidget(button)
        self.statusBar().addPermanentWidget(zoom_controls)

        self.theme_toggle_act = QAction(self)
        self.theme_toggle_act.triggered.connect(self.toggle_theme)
        self.theme_btn = QToolButton()
        self.theme_btn.setDefaultAction(self.theme_toggle_act)
        self.theme_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.theme_btn.setIconSize(QSize(18, 18))
        self.statusBar().addPermanentWidget(self.theme_btn)

        self.apply_theme(self.theme_name)
        self.update_properties()
        self.setWindowTitle("PDF 편집기[*]")
        if path:
            self.open(path)
        if self.pen_button:
            QApplication.instance().installNativeEventFilter(self.pen_button)

    def set_themed_icon(self, target, icon, color_key="icon"):
        self.icon_targets.append((target, icon, color_key))
        target.setIcon(ui_icon(icon, self.theme["colors"][color_key], self.devicePixelRatioF()))

    def apply_application_theme(self):
        app = QApplication.instance()
        app.setStyleSheet(self.theme["qss"])
        window, text, base, alternate, button, highlight, highlighted = self.theme["palette"]
        palette = QPalette()
        for role, color in ((QPalette.Window, window), (QPalette.WindowText, text),
                            (QPalette.Base, base), (QPalette.AlternateBase, alternate),
                            (QPalette.Text, text), (QPalette.Button, button),
                            (QPalette.ButtonText, text), (QPalette.Highlight, highlight),
                            (QPalette.HighlightedText, highlighted)):
            palette.setColor(role, QColor(color))
        app.setPalette(palette)

    def make_dock(self, title, content, side, min_width):
        dock = QDockWidget(title, self)
        dock.setWidget(content)
        dock.setMinimumWidth(min_width)
        dock.setFeatures(QDockWidget.DockWidgetClosable)
        header = QWidget()
        header.setObjectName("dockHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(13, 5, 7, 5)
        layout.addWidget(QLabel(title))
        layout.addStretch()
        close = QToolButton()
        self.set_themed_icon(close, "close", "close_icon")
        close.setIconSize(QSize(16, 16))
        close.setToolTip(f"{title} 패널 닫기")
        close.clicked.connect(dock.hide)
        layout.addWidget(close)
        dock.setTitleBarWidget(header)
        self.addDockWidget(side, dock)
        return dock

    def make_properties(self):
        body = QWidget()
        body.setObjectName("propertyBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(15)
        self.property_title = QLabel("선택")
        self.property_title.setObjectName("propertyTitle")
        self.property_hint = QLabel()
        self.property_hint.setObjectName("propertyHint")
        self.property_hint.setWordWrap(True)
        layout.addWidget(self.property_title)
        layout.addWidget(self.property_hint)

        self.color_section = QWidget()
        color_layout = QVBoxLayout(self.color_section)
        color_layout.setContentsMargins(0, 10, 0, 0)
        color_layout.setSpacing(10)
        color_label = QLabel("색상")
        color_label.setObjectName("propertyLabel")
        color_layout.addWidget(color_label)
        swatches = QWidget()
        swatch_layout = QHBoxLayout(swatches)
        swatch_layout.setContentsMargins(0, 0, 0, 0)
        swatch_layout.setSpacing(8)
        self.swatch_buttons = []
        for index in range(4):
            button = QToolButton()
            button.setFixedSize(28, 28)
            button.clicked.connect(lambda checked=False, n=index: self.set_color(PRESETS[self.color_key][n]))
            swatch_layout.addWidget(button)
            self.swatch_buttons.append(button)
        swatch_layout.addStretch()
        color_layout.addWidget(swatches)
        self.color_btn = QToolButton()
        self.color_btn.setObjectName("customColor")
        self.color_btn.setText("사용자 지정 색상")
        self.color_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.color_btn.clicked.connect(self.pick_color)
        color_layout.addWidget(self.color_btn)
        layout.addWidget(self.color_section)

        self.width_section = QWidget()
        width_layout = QVBoxLayout(self.width_section)
        width_layout.setContentsMargins(0, 10, 0, 0)
        width_label = QLabel("선 굵기")
        width_label.setObjectName("propertyLabel")
        width_layout.addWidget(width_label)
        self.width_spin = QSpinBox(minimum=1, maximum=20, value=self.width, suffix=" pt")
        self.width_spin.setFixedWidth(110)
        self.width_spin.valueChanged.connect(self.set_stroke_width)
        width_layout.addWidget(self.width_spin)
        layout.addWidget(self.width_section)

        self.font_section = QWidget()
        font_layout = QVBoxLayout(self.font_section)
        font_layout.setContentsMargins(0, 10, 0, 0)
        font_label = QLabel("글자 크기")
        font_label.setObjectName("propertyLabel")
        font_layout.addWidget(font_label)
        self.font_spin = QSpinBox(minimum=6, maximum=72, value=self.font_size, suffix=" pt")
        self.font_spin.setFixedWidth(110)
        self.font_spin.valueChanged.connect(lambda v: setattr(self, "font_size", v))
        font_layout.addWidget(self.font_spin)
        layout.addWidget(self.font_section)
        layout.addStretch()
        return body

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_refit()
        if self.scroll_page_indicator.isVisible():
            self.position_scroll_page_indicator()

    def schedule_refit(self):
        if self.fit_mode and self.pages:
            QTimer.singleShot(0, lambda: self.fit_width() if self.fit_mode else None)

    # --- 문서 ---
    def store_page(self, pno):
        self._last_seen_page = pno
        settings = QSettings("pdf-editor", "PdfEditor")
        settings.setValue(page_settings_key(self.path), pno)
        settings.sync()

    def remember_current_page(self):
        if not self.doc or not self.pages:
            return
        pno = self._last_seen_page if self._restoring_page else self.current_page()
        self.store_page(pno)

    def open(self, path):
        # 파일을 메모리로 읽어 열기: 파일 잠금 없음, 같은 경로에 그대로 저장 가능.
        new_doc = pymupdf.open(stream=Path(path).read_bytes(), filetype="pdf")
        self.remember_current_page()
        self.doc = new_doc
        self.path = path
        self._document_generation += 1
        self._restoring_page = True
        try:
            stored = int(QSettings("pdf-editor", "PdfEditor").value(page_settings_key(path), 0))
        except (TypeError, ValueError):
            stored = 0
        self._last_seen_page = max(0, min(stored, len(self.doc) - 1))
        self.undo.clear()
        box = QWidget()
        self.paint_canvas(box)
        lay = QVBoxLayout(box)
        lay.setAlignment(Qt.AlignHCenter)
        lay.setSpacing(16)
        lay.setContentsMargins(16, 16, 16, 16)
        self.pages = [PageWidget(self, i) for i in range(len(self.doc))]
        for pw in self.pages:
            lay.addWidget(pw)
        self.scroll.setWidget(box)
        self.background_pan.set_canvas(box)
        self.thumbs.clear()
        # ponytail: 열 때 전부 렌더(0.2배). 수백 페이지면 스크롤 시 지연 렌더로.
        for i in range(len(self.doc)):
            self.thumbs.addItem(QListWidgetItem(thumbnail_icon(self.doc[i]), str(i + 1)))
        self.page_spin.setMaximum(len(self.doc))
        self.page_label.setText(f"/ {len(self.doc)}")
        self.setWindowTitle(f"{Path(path).name} - PDF 편집기[*]")
        generation = self._document_generation
        QTimer.singleShot(0, lambda: self.fit_width() if generation == self._document_generation else None)

    def open_dialog(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "PDF 열기", filter="PDF (*.pdf)")
        if path:
            self.open(path)

    def save(self):
        if not self.doc:
            return False
        purged = purge_hidden(self.doc)
        self.doc.save(self.path, garbage=3, deflate=True)
        if purged:
            self.undo.clear()   # 숨긴 주석이 사라져 이전 히스토리 무효
        else:
            self.undo.setClean()
        self.statusBar().showMessage(f"저장됨: {self.path}", 3000)
        return True

    def save_as(self):
        if not self.doc:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "다른 이름으로 저장", self.path, "PDF (*.pdf)")
        if not path:
            return False
        self.remember_current_page()
        self.path = path
        self.setWindowTitle(f"{Path(path).name} - PDF 편집기[*]")
        saved = self.save()
        if saved:
            self.remember_current_page()
        return saved

    def confirm_discard(self):
        if not self.doc or self.undo.isClean():
            return True
        r = QMessageBox.question(self, "저장", "변경 내용을 저장할까요?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        return self.save() if r == QMessageBox.Save else r == QMessageBox.Discard

    def on_clean_changed(self, clean):
        self.setWindowModified(not clean)

    def closeEvent(self, e):
        for pw in self.pages:
            for ed in pw.findChildren(InlineEditor):
                ed.finish(True)
        if self.doc and not self.undo.isClean():
            try:
                if not self.save():
                    e.ignore()
                    return
            except Exception as exc:
                QMessageBox.critical(self, "저장 실패", f"변경 내용을 저장하지 못했습니다:\n{exc}")
                e.ignore()
                return
        self.remember_current_page()
        if self.pen_button:
            QApplication.instance().removeNativeEventFilter(self.pen_button)
        e.accept()

    def dragEnterEvent(self, e):
        if any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        if self.confirm_discard():
            self.open(next(u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".pdf")))

    # --- 페이지 이동/목록 ---
    def current_page(self):
        y = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() / 2
        for pw in self.pages:
            if pw.y() + pw.height() >= y:
                return pw.pno
        return len(self.pages) - 1

    def update_page_label(self):
        if not self.pages:
            self.scroll_page_indicator.hide()
            return
        pno = self.current_page()
        for w in (self.page_spin, self.thumbs):
            w.blockSignals(True)
        self.page_spin.setValue(pno + 1)
        self.thumbs.setCurrentRow(pno)
        for w in (self.page_spin, self.thumbs):
            w.blockSignals(False)
        if not self._restoring_page and pno != self._last_seen_page:
            self.store_page(pno)
        self.show_scroll_page_indicator(pno)

    def position_scroll_page_indicator(self):
        bar = self.scroll.verticalScrollBar()
        viewport = self.scroll.viewport()
        option = QStyleOptionSlider()
        bar.initStyleOption(option)
        handle = bar.style().subControlRect(
            QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
        handle_top_left = bar.mapTo(viewport, handle.topLeft())
        handle_center = bar.mapTo(viewport, handle.center())
        label = self.scroll_page_indicator
        label.adjustSize()
        x = max(0, min(handle_top_left.x() - label.width() - 8,
                       viewport.width() - label.width()))
        y = max(0, min(handle_center.y() - label.height() // 2,
                       viewport.height() - label.height()))
        label.move(x, y)

    def show_scroll_page_indicator(self, pno):
        bar = self.scroll.verticalScrollBar()
        if len(self.pages) <= 1 or not bar.isVisible() or bar.maximum() <= bar.minimum():
            self.scroll_page_timer.stop()
            self.scroll_page_indicator.hide()
            return
        self.scroll_page_indicator.setText(str(pno + 1))
        self.position_scroll_page_indicator()
        self.scroll_page_indicator.raise_()
        self.scroll_page_indicator.show()
        self.scroll_page_timer.start()

    def goto_page(self, pno):
        if 0 <= pno < len(self.pages):
            self.scroll.verticalScrollBar().setValue(self.pages[pno].y() - 16)

    def update_thumb(self, pno):
        if pno < self.thumbs.count():
            self.thumbs.item(pno).setIcon(thumbnail_icon(self.doc[pno]))

    # --- 편집 ---
    def set_zoom(self, z, fit=False):
        pno = self._last_seen_page if self._restoring_page else self.current_page()
        generation = self._document_generation
        self._zoom_generation += 1
        zoom_generation = self._zoom_generation
        self.fit_mode = fit
        self.zoom = max(0.3, min(5.0, z))
        for pw in self.pages:
            pw.invalidate(thumb=False)
        self.zoom_act.setText(f"{self.zoom * 100:.0f}%")
        def finish_zoom():
            if generation != self._document_generation or zoom_generation != self._zoom_generation:
                return
            self.goto_page(pno)
            if self._restoring_page:
                self._restoring_page = False
                self.update_page_label()
                self.scroll_page_timer.stop()
                self.scroll_page_indicator.hide()
        # QScrollArea의 크기 조정과 페이지 레이아웃이 끝난 다음 위치를 복원한다.
        QTimer.singleShot(0, lambda: QTimer.singleShot(0, finish_zoom))

    def fit_width(self):
        if self.pages:
            self.set_zoom((self.scroll.viewport().width() - 48) / self.doc[0].rect.width, fit=True)

    def set_tool(self, tool):
        if tool != self.tool:
            for pw in self.pages:
                if pw.pen_input is not None:
                    pw.finish_pen_input()
                if pw.hl_points:
                    pw.cancel_highlight()
        self.tool = tool
        self.width_spin.blockSignals(True)
        self.width_spin.setRange(1, 40 if tool == "hl" else 20)
        self.width_spin.setValue(self.hl_width if tool == "hl" else self.width)
        self.width_spin.blockSignals(False)
        for pw in self.pages:                 # 도구 바뀌면 열려 있던 입력창은 취소
            for ed in pw.findChildren(InlineEditor):
                ed.finish(False)
        self.update_swatch()
        self.refresh_tool_display()

    def set_stroke_width(self, width):
        if self.tool == "hl":
            self.hl_width = width
        else:
            self.width = width
        for pw in self.pages:
            if pw.hl_points or pw.pts:
                pw.update()

    @property
    def temporary_eraser(self):
        return bool((self.pen_button and self.pen_button.eraser) or self.qt_pen_eraser)

    def set_qt_pen_eraser(self, active):
        if self.qt_pen_eraser != active:
            self.qt_pen_eraser = active
            self.refresh_tool_display()

    def on_native_pen_button_changed(self):
        if not self.pen_button.eraser:
            self.qt_pen_eraser = False
        self.refresh_tool_display()

    def refresh_tool_display(self):
        temporary = self.temporary_eraser
        if temporary and not self._temporary_displayed:
            for pw in self.pages:
                if pw.hl_points:
                    pw.cancel_highlight()
                if pw.moving:
                    pw.moving = None
                    pw.update()
        self._temporary_displayed = temporary
        shown_tool = "erase" if temporary else self.tool
        for act in self.tool_group.actions():
            act.setChecked(act.data() == shown_tool)
        cursor = {"pen": Qt.CrossCursor, "hl": Qt.IBeamCursor, "text": Qt.IBeamCursor,
                  "note": Qt.PointingHandCursor, "erase": Qt.PointingHandCursor}
        for pw in self.pages:
            pw.tool_cursor = cursor.get(shown_tool, Qt.ArrowCursor)
            pw.setCursor(pw.tool_cursor)
        self.update_properties()

    # --- 테마 ---
    def apply_theme(self, name):
        name = name if name in THEMES else "다크"
        self.theme_name = name
        self.theme = THEMES[name]
        self.apply_application_theme()
        dpr = self.devicePixelRatioF()
        colors = self.theme["colors"]
        for target, icon, color_key in self.icon_targets:
            target.setIcon(ui_icon(icon, colors[color_key], dpr))
        for theme_name, action in self.theme_actions.items():
            action.setChecked(theme_name == name)
        next_name, next_icon = (("라이트", "sun") if name == "다크" else ("다크", "moon"))
        self.theme_toggle_act.setText(f"{next_name} 테마로 전환")
        self.theme_toggle_act.setToolTip(f"{next_name} 테마로 전환")
        self.theme_toggle_act.setIcon(ui_icon(next_icon, colors["icon"], dpr))
        self.update_swatch()
        if self.scroll.widget():
            self.paint_canvas(self.scroll.widget())
        for pw in self.pages:
            pw.invalidate(thumb=False)
        settings = QSettings("pdf-editor", "PdfEditor")
        settings.setValue("theme", name)
        settings.sync()
        if self.isVisible():
            style_window_chrome(self)

    def toggle_theme(self):
        self.apply_theme("라이트" if self.theme_name == "다크" else "다크")

    def paint_canvas(self, box):
        pal = box.palette()
        pal.setColor(QPalette.Window, QColor(self.theme["canvas"]))
        box.setPalette(pal)
        box.setAutoFillBackground(True)

    def update_properties(self):
        shown_tool = "erase" if self.temporary_eraser else self.tool
        details = {
            None: ("선택", "문서의 주석을 클릭하거나 끌어 이동하세요. 위에서 편집 도구를 선택할 수 있습니다."),
            "pen": ("펜", "페이지 위에 자유롭게 그립니다."),
            "hl": ("형광펜", "글자 위에서는 텍스트 줄을 따라 표시합니다. 빈 공간에는 자유롭게 그릴 수 있습니다."),
            "text": ("텍스트", "페이지를 클릭해 글자를 입력하세요. Ctrl+Enter로 입력을 마칩니다."),
            "note": ("메모", "페이지를 클릭해 메모를 추가하세요."),
            "erase": ("지우개", "주석 위를 드래그해서 삭제합니다."),
        }
        title, hint = details[shown_tool]
        self.property_title.setText(title)
        self.property_hint.setText(hint)
        self.color_section.setVisible(shown_tool in ("pen", "hl", "text"))
        self.width_section.setVisible(shown_tool in ("pen", "hl"))
        self.font_section.setVisible(shown_tool == "text")

    @property
    def color_key(self):
        return self.tool if self.tool in self.colors else "pen"

    def update_swatch(self):
        key = self.color_key
        current = self.colors[key].lower()
        colors = self.theme["colors"]
        for button, color in zip(self.swatch_buttons, PRESETS[key]):
            border = colors["accent"] if color.lower() == current else colors["button_border"]
            button.setStyleSheet(f"QToolButton {{ background: {color}; border: 3px solid {border}; border-radius: 14px; }}"
                                 f"QToolButton:hover {{ border-color: {colors['accent_text']}; }}")
            button.setToolTip(color)
        pm = QPixmap(18, 18)
        pm.fill(QColor(current))
        self.color_btn.setIcon(QIcon(pm))

    def set_color(self, hex_color):
        self.colors[self.color_key] = hex_color
        self.update_swatch()

    def pick_color(self):
        dialog = QColorDialog(QColor(self.colors[self.color_key]), self)
        dialog.setWindowTitle("주석 색상")
        dialog.setOption(QColorDialog.DontUseNativeDialog)
        if dialog.exec() != QColorDialog.Accepted:
            return
        c = dialog.selectedColor()
        if c.isValid():
            self.set_color(c.name())


def style_window_chrome(win):
    if sys.platform != "win32":
        return
    hwnd = int(win.winId())
    dark = ctypes.c_int(1 if win.theme["dark_title"] else 0)
    try:
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        if dwm(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark)) != 0:
            dwm(hwnd, 19, ctypes.byref(dark), ctypes.sizeof(dark))
        for attribute, hex_color in zip((35, 36, 34), win.theme["chrome"]):
            color = QColor(hex_color)
            value = ctypes.c_int(color.red() | color.green() << 8 | color.blue() << 16)
            dwm(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass  # 오래된 Windows에서는 운영체제 제목 표시줄을 그대로 사용한다.


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pdf-editor.PdfEditor")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app_icon = QIcon(str(ASSETS / "app.ico"))
    app.setWindowIcon(app_icon)
    win = Win(sys.argv[1] if len(sys.argv) > 1 else None)
    win.setWindowIcon(app_icon)
    win.show()
    style_window_chrome(win)
    sys.exit(app.exec())
