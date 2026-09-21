"""PDF 주석 편집기: 펜, 형광펜(글자 줄 스냅), 텍스트, 메모(호버 표시), 저장, undo/redo.

주석은 PDF 표준 annotation으로 파일에 직접 저장된다. 상태는 PyMuPDF Document 하나가 전부.
"삭제"와 AddAnnot.undo는 실제 삭제 대신 HIDDEN 플래그를 세운다(xref가 안정되어 undo/redo가 단순).
숨긴 주석은 저장 시점에 물리적으로 제거된다.
"""
import re
import sys
import ctypes
from functools import cache
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt, QByteArray, QRectF, QPointF, QSettings, QSize, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QFontDatabase, QIcon, QImage, QKeySequence,
                           QPainter, QPalette, QPen, QPixmap, QTextCursor, QUndoCommand, QUndoStack)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QColorDialog, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QScrollArea, QSpinBox,
                               QTextEdit, QToolBar, QToolButton, QToolTip, QVBoxLayout, QWidget)

HIDDEN = pymupdf.PDF_ANNOT_IS_HIDDEN
FONT_SIZE = 20
DEFAULT_COLORS = {"pen": "#426eff", "hl": "#fdff95", "text": "#2864c6"}
PRESETS = {"pen": ["#426eff", "#ff4d4f", "#222222", "#12b886"],
           "hl": ["#fdff95", "#c0fffd", "#ceffc9", "#ffd8d9"],
           "text": ["#2864c6", "#222222", "#e03131", "#12b886"]}


def rgb(hex_color):
    c = QColor(hex_color)
    return (c.redF(), c.greenF(), c.blueF())
THUMB_ZOOM = 0.2

QSS = """
QMainWindow { background: #f4f4f6; }
QToolBar { background: #ffffff; border-bottom: 1px solid #d9d9de; padding: 4px 8px; spacing: 2px; }
QToolBar QToolButton, QStatusBar QToolButton { padding: 6px 10px; border-radius: 6px; color: #222; }
QToolBar QToolButton:hover, QStatusBar QToolButton:hover { background: #eef0f3; }
QToolBar QToolButton:checked { background: #dbe8ff; color: #1657d0; font-weight: 600; }
QToolBar QToolButton:disabled { color: #b0b0b5; }
QToolBar::separator { width: 1px; background: #e0e0e4; margin: 6px 6px; }
QToolBar QSpinBox, QStatusBar QSpinBox { padding: 3px 6px; border: 1px solid #d9d9de; border-radius: 6px; background: #fff; }
QScrollArea { border: none; }
QStatusBar { background: #ffffff; border-top: 1px solid #d9d9de; color: #444; }
QDockWidget { font-weight: 600; color: #444; }
QDockWidget::title { background: #f4f4f6; padding: 6px; border-bottom: 1px solid #d9d9de; }
QListWidget { background: #f4f4f6; border: none; outline: none; }
QListWidget::item { color: #555; padding: 4px; border-radius: 6px; }
QListWidget::item:selected { background: #dbe8ff; color: #1657d0; }
QToolTip { background: #fff8b3; color: #333; border: 1px solid #e0c200; padding: 6px; }
"""

ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"   # exe(onefile)는 _MEIPASS에 풀림

# 파스텔: 라벤더 바탕에 1px 선, 작업대(PDF 뒤)만 크림, 핑크는 선택 포인트만. 글꼴 Galmuri11, 아이콘 pixelarticons (assets/에 라이선스 동봉)
PASTEL_QSS = """
QMainWindow, QDialog { background: #f7f3fb; }
QLabel { color: #5a4a6e; }
QToolBar { background: #f7f3fb; border: none; border-bottom: 1px solid #ddd3ec; padding: 4px 6px; spacing: 2px; }
QToolBar QToolButton, QStatusBar QToolButton { background: transparent; color: #5a4a6e; padding: 3px 7px;
    border: 1px solid transparent; border-radius: 0; }
QToolBar QToolButton:hover, QStatusBar QToolButton:hover { background: #eee8f8; border-color: #ddd3ec; }
QToolBar QToolButton:checked { background: #e6defb; color: #45347a; border-color: #a898dc; }
QToolBar QToolButton:pressed, QStatusBar QToolButton:pressed { background: #ddd4f7; border-color: #a898dc; }
QToolBar QToolButton:disabled { color: #c3b9d3; }
QToolBar QToolButton::menu-indicator, QStatusBar QToolButton::menu-indicator { image: none; width: 0; }
QToolBar::separator { width: 1px; background: #ddd3ec; margin: 6px 5px; }
QSpinBox { background: #fffdf7; color: #5a4a6e; padding: 3px 4px; border: 1px solid #d3c7e6; border-radius: 0;
    selection-background-color: #f4c9dc; selection-color: #5a4a6e; }
QSpinBox:focus { border-color: #a898dc; }
QStatusBar { background: #f7f3fb; border-top: 1px solid #ddd3ec; }
QDockWidget { color: #5a4a6e; font-weight: bold; }
QDockWidget::title { background: #f5dbe7; padding: 6px 8px; border-bottom: 1px solid #e8bfd2; }
QListWidget { background: #f1ecf8; border: none; outline: none; }
QListWidget::item { color: #9a8cb0; padding: 4px; border: 2px solid transparent; }
QListWidget::item:selected { background: transparent; color: #5a4a6e; border: 2px solid #eea5c4; }
QMenu { background: #fffdf8; border: 1px solid #cdbfe0; padding: 3px; }
QMenu::item { color: #5a4a6e; padding: 5px 18px 5px 26px; }
QMenu::item:selected { background: #f5dbe7; }
QMenu::separator { height: 1px; background: #e5dcef; margin: 3px 6px; }
QPushButton { background: #fffdf8; color: #5a4a6e; padding: 5px 14px; min-width: 60px; border: 1px solid #cdbfe0; }
QPushButton:hover { background: #eee8f8; }
QPushButton:pressed, QPushButton:default { border-color: #a898dc; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #f1ecf8; width: 12px; margin: 0; }
QScrollBar:horizontal { background: #f1ecf8; height: 12px; margin: 0; }
QScrollBar::handle { background: #dcd2f1; border: 1px solid #bcaedb; }
QScrollBar::handle:hover { background: #cfc2ee; }
QScrollBar::handle:vertical { min-height: 32px; }
QScrollBar::handle:horizontal { min-width: 32px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QToolTip { background: #fff6d6; color: #5a4a2e; border: 1px solid #e2c77a; padding: 6px; }
"""

THEMES = {
    "기본": dict(qss=QSS, font="Galmuri11", icons=None, canvas="#e4e5e9", dots=None, page_border="#c9c9ce", shadow=0,
                shadow_color=None, marker=("#ffd83d", "#b38f00", "#6b5600"),
                note_style="QTextEdit{background:#fff8b3;color:#333;border:1px solid #e0c200;border-radius:4px;padding:4px}"),
    "파스텔": dict(qss=PASTEL_QSS, font="Galmuri11", icons="#5a4a6e", canvas="#fcf6ce", dots="#efe6b4",
                 page_border="#d8c9a6", shadow=4, shadow_color="#ebdfae", marker=("#ffe79a", "#c7a24a", "#7a5e1f"),
                 note_style="QTextEdit{background:#fff6d6;color:#5a4a2e;border:1px solid #e2c77a;padding:4px}"),
}


@cache
def icon_px(dpr):
    """24px 격자 아이콘은 실제 화면 픽셀 정수배일 때만 선명. 125% 배율이면 24px 그대로(논리 크기 19)."""
    return 24 * max(1, int(dpr))


@cache
def theme_icon(name, color, dpr):
    svg = (ASSETS / "icons" / f"{name}.svg").read_bytes().replace(b"currentColor", color.encode())
    img = QImage(icon_px(dpr), icon_px(dpr), QImage.Format_ARGB32_Premultiplied)
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


# ---------- 페이지 위젯 ----------

class PageWidget(QWidget):
    def __init__(self, win, pno):
        super().__init__()
        self.win, self.pno = win, pno
        self.img = None
        self.words = None    # get_text("words") 캐시
        self.pts = []        # 펜 드래그 중 점(위젯 좌표)
        self.drag = None     # 형광펜 드래그 [시작, 현재]
        self.preview = []    # 형광펜 미리보기 quads
        self.moving = None   # 텍스트 드래그 이동 [xref, 시작, 원래 rect, 현재]
        self.erased = None   # 지우개 드래그 중 숨긴 xref 목록
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
            p.setPen(QPen(QColor("#1657d0"), 1, Qt.DashLine))
            p.setBrush(QColor(22, 87, 208, 30))
            p.drawRect(self.to_widget(self.moving[2]).translated(self.moving[3] - self.moving[1]))
        if self.preview:
            color.setAlpha(110)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            for q in self.preview:
                p.drawRect(self.to_widget(q.rect))

    # --- 마우스 ---
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        t, pt = self.win.tool, self.to_pdf(e.position())
        a = self.annot_at(pt)
        if a and a.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT and t in (None, "text"):
            self.moving = [a.xref, e.position(), a.rect, e.position()]   # 릴리즈 때 클릭이면 편집, 드래그면 이동
            return
        if a and t is None and a.info["content"]:
            return self.start_note(pt, a.xref)
        if t == "pen":
            self.pts = [e.position()]
        elif t == "hl":
            self.drag = [e.position(), e.position()]
        elif t == "text":
            self.start_text(pt)
        elif t == "note":
            self.start_note(pt)
        elif t == "erase":
            self.erased = []
            self.erase_at(pt)

    def erase_at(self, pt):
        a = self.annot_at(pt)
        if a is not None:
            self.erased.append(a.xref)
            set_hidden(self._page, a.xref, True)   # 즉시 숨김, 커맨드는 릴리즈 때 한 번에
            self.invalidate(thumb=False)

    def mouseMoveEvent(self, e):
        if self.pts:
            self.pts.append(e.position())
            self.update()
        elif self.erased is not None:
            self.erase_at(self.to_pdf(e.position()))
        elif self.drag:
            self.drag[1] = e.position()
            self.preview = self.drag_quads()
            self.update()
        elif self.moving:
            self.moving[3] = e.position()
            self.update()
        else:
            a = self.annot_at(self.to_pdf(e.position()))
            is_text = a is not None and a.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT and self.win.tool in (None, "text")
            self.setCursor(Qt.SizeAllCursor if is_text else self.tool_cursor)
            content = a.info["content"] if a and a.type[0] != pymupdf.PDF_ANNOT_FREE_TEXT else ""
            if content:
                if a.xref != self.tip_xref:
                    QToolTip.showText(e.globalPosition().toPoint(), content, self)
                self.tip_xref = a.xref
            else:
                QToolTip.hideText()
                self.tip_xref = None

    def drag_quads(self):
        r = pymupdf.Rect(self.to_pdf(self.drag[0]), self.to_pdf(self.drag[1]))
        r.normalize()
        return line_quads(self.page, r + (-1, -1, 1, 1), self.get_words())

    def mouseReleaseEvent(self, _e):
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
            xrefs, self.erased = self.erased, None
            if xrefs:
                win.undo.beginMacro("지우기")
                for x in xrefs:
                    win.undo.push(SetHidden(win, pno, x))
                win.undo.endMacro()
        elif self.pts:
            pts = [self.to_pdf(p) for p in self.pts]
            self.pts = []
            if len(pts) > 1:
                c, w = rgb(win.colors["pen"]), win.width
                win.undo.push(AddAnnot(win, pno, lambda page: make_ink(page, pts, c, w), "펜"))
            self.update()
        elif self.drag:
            quads, self.drag, self.preview = self.drag_quads(), None, []
            if quads:
                c = rgb(win.colors["hl"])
                win.undo.push(AddAnnot(win, pno, lambda page: make_highlight(page, quads, c), "형광펜"))
            self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.win.set_zoom(self.win.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
        else:
            e.ignore()

    def contextMenuEvent(self, e):
        win, pno = self.win, self.pno
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

TOOLS = [("✏️", "펜", "pen", "pencil"), ("🖍", "형광펜", "hl", "brush"), ("T", "텍스트", "text", "text-start-t"),
         ("💬", "메모", "note", "sticky-note-text"), ("🧽", "지우개", "erase", "eraser")]


class Win(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.doc, self.path, self.pages = None, None, []
        self.zoom, self.tool, self.width, self.font_size = 1.5, None, 2, FONT_SIZE
        self.colors = dict(DEFAULT_COLORS)
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(self.on_clean_changed)
        self.setAcceptDrops(True)
        self.resize(1200, 900)
        self.base_font = QApplication.font()
        for f in (ASSETS / "fonts").glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(f))

        tb = self.tb = QToolBar("도구")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self.themed = []   # (액션, 기본 텍스트, 아이콘 테마 텍스트, 아이콘). 텍스트 None이면 아이콘만 바꿈
        self.themed.append((tb.addAction("📂 열기", QKeySequence.Open, self.open_dialog), "📂 열기", "열기", "folder"))
        self.themed.append((tb.addAction("💾 저장", QKeySequence.Save, self.save), "💾 저장", "저장", "save"))
        tb.addAction("다른 이름으로", QKeySequence.SaveAs, self.save_as)
        tb.addSeparator()
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)   # 다시 누르면 꺼짐 = 선택 모드
        for i, (emoji, name, tool, icon) in enumerate(TOOLS):
            act = QAction(f"{emoji} {name}", self, checkable=True, shortcut=str(i + 1))
            act.setData(tool)
            self.tool_group.addAction(act)
            tb.addAction(act)
            self.themed.append((act, f"{emoji} {name}", name, icon))
        self.tool_group.triggered.connect(lambda act: self.set_tool(act.data() if act.isChecked() else None))
        tb.addSeparator()
        self.color_btn = QToolButton(text="색", popupMode=QToolButton.InstantPopup, toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.color_menu = QMenu(self.color_btn)
        self.color_menu.aboutToShow.connect(self.build_color_menu)
        self.color_btn.setMenu(self.color_menu)
        tb.addWidget(self.color_btn)
        spin = QSpinBox(minimum=1, maximum=20, value=self.width, prefix="굵기 ")
        spin.valueChanged.connect(lambda v: setattr(self, "width", v))
        tb.addWidget(spin)
        self.font_spin = QSpinBox(minimum=6, maximum=72, value=self.font_size, prefix="글자 ", suffix="pt")
        self.font_spin.valueChanged.connect(lambda v: setattr(self, "font_size", v))
        tb.addWidget(self.font_spin)
        tb.addSeparator()
        for act, key, icon in [(self.undo.createUndoAction(self, "↶"), QKeySequence.Undo, "undo"),
                               (self.undo.createRedoAction(self, "↷"), QKeySequence.Redo, "redo")]:
            act.setShortcuts(key)
            tb.addAction(act)
            self.themed.append((act, None, None, icon))   # 텍스트는 QUndoStack이 갱신
        tb.addSeparator()
        self.themed.append((tb.addAction("－", QKeySequence.ZoomOut, lambda: self.set_zoom(self.zoom / 1.2)), None, None, "zoom-out"))
        self.zoom_act = tb.addAction("100%", self.fit_width)
        self.themed.append((tb.addAction("＋", QKeySequence.ZoomIn, lambda: self.set_zoom(self.zoom * 1.2)), None, None, "zoom-in"))
        tb.addSeparator()

        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.verticalScrollBar().valueChanged.connect(self.update_page_label)
        self.setCentralWidget(self.scroll)

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
        dock = QDockWidget("페이지", self)
        dock.setWidget(self.thumbs)
        dock.setMinimumWidth(190)
        dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        toggle = dock.toggleViewAction()
        toggle.setText("☰ 페이지 목록")
        tb.addAction(toggle)
        self.themed.append((toggle, "☰ 페이지 목록", "페이지 목록", "layout"))
        theme_menu = QMenu(self)
        self.theme_group = QActionGroup(self)
        for name in THEMES:
            self.theme_group.addAction(theme_menu.addAction(name)).setCheckable(True)
        self.theme_group.triggered.connect(lambda act: self.apply_theme(act.text()))
        theme_act = QAction("🎨 테마", self)
        theme_act.setMenu(theme_menu)
        theme_btn = self.theme_btn = QToolButton(toolButtonStyle=Qt.ToolButtonTextBesideIcon)   # 툴바 끝은 창이 좁으면 잘려서 상태바 오른쪽에 둠
        theme_btn.setDefaultAction(theme_act)
        theme_btn.setPopupMode(QToolButton.InstantPopup)
        self.statusBar().addPermanentWidget(theme_btn)
        self.themed.append((theme_act, "🎨 테마", "테마", "colors-swatch"))

        self.page_spin = QSpinBox(minimum=1, maximum=1)
        self.page_spin.editingFinished.connect(lambda: self.goto_page(self.page_spin.value() - 1))
        self.page_label = QLabel("/ 0")
        center = QWidget()
        lay = QHBoxLayout(center)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.addStretch()
        for w in (QLabel("페이지 "), self.page_spin, self.page_label):
            lay.addWidget(w)
        lay.addStretch()
        self.statusBar().addWidget(center, 1)

        self.update_swatch()
        self.setWindowTitle("PDF 편집기[*]")
        self.apply_theme(QSettings("pdf-editor", "PdfEditor").value("theme", "기본"))
        if path:
            self.open(path)

    # --- 문서 ---
    def open(self, path):
        # 파일을 메모리로 읽어 열기: 파일 잠금 없음, 같은 경로에 그대로 저장 가능.
        self.doc = pymupdf.open(stream=Path(path).read_bytes(), filetype="pdf")
        self.path = path
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
        self.thumbs.clear()
        # ponytail: 열 때 전부 렌더(0.2배). 수백 페이지면 스크롤 시 지연 렌더로.
        for i in range(len(self.doc)):
            self.thumbs.addItem(QListWidgetItem(QIcon(QPixmap.fromImage(to_qimage(self.doc[i], THUMB_ZOOM))), str(i + 1)))
        self.page_spin.setMaximum(len(self.doc))
        self.page_label.setText(f"/ {len(self.doc)}")
        self.setWindowTitle(f"{Path(path).name} - PDF 편집기[*]")
        QTimer.singleShot(0, self.fit_width)

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
        self.path = path
        self.setWindowTitle(f"{Path(path).name} - PDF 편집기[*]")
        return self.save()

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
            return
        pno = self.current_page()
        for w in (self.page_spin, self.thumbs):
            w.blockSignals(True)
        self.page_spin.setValue(pno + 1)
        self.thumbs.setCurrentRow(pno)
        for w in (self.page_spin, self.thumbs):
            w.blockSignals(False)

    def goto_page(self, pno):
        if 0 <= pno < len(self.pages):
            self.scroll.verticalScrollBar().setValue(self.pages[pno].y() - 16)

    def update_thumb(self, pno):
        if pno < self.thumbs.count():
            self.thumbs.item(pno).setIcon(QIcon(QPixmap.fromImage(to_qimage(self.doc[pno], THUMB_ZOOM))))

    # --- 편집 ---
    def set_zoom(self, z):
        pno = self.current_page()
        self.zoom = max(0.3, min(5.0, z))
        for pw in self.pages:
            pw.invalidate(thumb=False)
        self.zoom_act.setText(f"{self.zoom * 100:.0f}%")
        QTimer.singleShot(0, lambda: self.goto_page(pno))

    def fit_width(self):
        if self.pages:
            self.set_zoom((self.scroll.viewport().width() - 48) / self.doc[0].rect.width)

    def set_tool(self, tool):
        self.tool = tool
        for act in self.tool_group.actions():
            act.setChecked(act.data() == tool)
        for pw in self.pages:                 # 도구 바뀌면 열려 있던 입력창은 취소
            for ed in pw.findChildren(InlineEditor):
                ed.finish(False)
        self.update_swatch()
        cursor = {"pen": Qt.CrossCursor, "hl": Qt.IBeamCursor, "text": Qt.IBeamCursor,
                  "note": Qt.PointingHandCursor, "erase": Qt.PointingHandCursor}
        for pw in self.pages:
            pw.tool_cursor = cursor.get(self.tool, Qt.ArrowCursor)
            pw.setCursor(pw.tool_cursor)

    # --- 테마 ---
    def apply_theme(self, name):
        name = name if name in THEMES else "기본"
        t = self.theme = THEMES[name]
        font = QFont(self.base_font)
        if t["font"]:
            font = QFont(t["font"])
            font.setPixelSize(12)   # Galmuri11은 12px에 맞춰 그려진 도트 글꼴
        QApplication.setFont(font)
        QApplication.instance().setStyleSheet(t["qss"])
        dpr = self.screen().devicePixelRatio()
        size = round(icon_px(dpr) / dpr)
        for w in (self.tb, self.theme_btn):
            w.setIconSize(QSize(size, size))
        for act, text, icon_text, icon in self.themed:
            if text is not None:
                act.setText(f" {icon_text}" if t["icons"] else text)   # 앞 공백 = 아이콘과 글자 사이 여백
            act.setIcon(theme_icon(icon, t["icons"], dpr) if t["icons"] else QIcon())
            if btn := self.tb.widgetForAction(act):
                icon_only = t["icons"] and text is None
                btn.setToolButtonStyle(Qt.ToolButtonIconOnly if icon_only else Qt.ToolButtonTextBesideIcon)
        for act in self.theme_group.actions():
            act.setChecked(act.text() == name)
        if self.scroll.widget():
            self.paint_canvas(self.scroll.widget())
        for pw in self.pages:
            pw.invalidate(thumb=False)
        QSettings("pdf-editor", "PdfEditor").setValue("theme", name)

    def paint_canvas(self, box):
        tile = QPixmap(14, 14)
        tile.fill(QColor(self.theme["canvas"]))
        if self.theme["dots"]:
            p = QPainter(tile)
            p.fillRect(6, 6, 2, 2, QColor(self.theme["dots"]))
            p.end()
        pal = box.palette()
        pal.setBrush(QPalette.Window, QBrush(tile))
        box.setPalette(pal)
        box.setAutoFillBackground(True)

    @property
    def color_key(self):
        return self.tool if self.tool in self.colors else "pen"

    def update_swatch(self):
        pm = QPixmap(18, 18)
        pm.fill(QColor(self.colors[self.color_key]))
        self.color_btn.setIcon(QIcon(pm))

    def build_color_menu(self):
        self.color_menu.clear()
        key = self.color_key
        for hex_color in PRESETS[key]:
            pm = QPixmap(18, 18)
            pm.fill(QColor(hex_color))
            self.color_menu.addAction(QIcon(pm), hex_color, lambda h=hex_color: self.set_color(h))
        self.color_menu.addAction("사용자 지정...", self.pick_color)

    def set_color(self, hex_color):
        self.colors[self.color_key] = hex_color
        self.update_swatch()

    def pick_color(self):
        c = QColorDialog.getColor(QColor(self.colors[self.color_key]), self, "색")
        if c.isValid():
            self.set_color(c.name())


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
    sys.exit(app.exec())
