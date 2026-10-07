"""Offscreen checks for selected text, annotation menus, and note hover."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pdf_editor
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from pdf_editor import HIDDEN, InlineEditor, Win


def widget_point(page, point):
    return page.to_widget(pymupdf.Rect(point, point)).center().toPoint()


def center(rect):
    return pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def select(page, first, last):
    start, end = widget_point(page, first), widget_point(page, last)
    QTest.mousePress(page, Qt.LeftButton, pos=start)
    QTest.mouseMove(page, end)
    QTest.mouseRelease(page, Qt.LeftButton, pos=end)


def menu_action(page, point, label):
    pos = widget_point(page, point)

    class ChoiceMenu(QMenu):
        def exec(self, _global_pos):
            next(a for a in self.actions() if a.text() == label).trigger()

    with patch.object(pdf_editor, "QMenu", ChoiceMenu):
        QApplication.sendEvent(page, QContextMenuEvent(QContextMenuEvent.Mouse, pos,
                                                        page.mapToGlobal(pos)))


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "selection.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=300)
        page.insert_text((50, 90), "alpha beta gamma", fontsize=16)
        page.insert_text((50, 130), "second row", fontsize=16)
        page.set_rotation(90)
        doc.save(path)
        doc.close()

        win = Win(str(path))
        win.show()
        app.processEvents()
        app.processEvents()
        page = win.pages[0]
        chars = page.get_text_chars()
        assert chars

        # A partial word remains a partial word on a rotated page.
        first, last = center(chars[1][0]), center(chars[3][0])
        select(page, first, last)
        assert len(page.selection_quads) == 1
        assert page.selection_quads[0].rect.width < pymupdf.Rect(page.get_words()[0][:4]).width
        page.repaint()
        menu_action(page, center(chars[2][0]), "형광펜")
        assert not page.selection_quads
        annots = list(win.doc[0].annots())
        assert len(annots) == 1 and annots[0].type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT
        win.undo.undo()
        assert win.doc[0].load_annot(annots[0].xref).flags & HIDDEN
        win.undo.redo()
        menu_action(page, center(chars[2][0]), "메모 추가")
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        editor.setPlainText("기존 형광펜 메모")
        editor.finish(True)
        assert win.doc[0].load_annot(annots[0].xref).info["content"] == "기존 형광펜 메모"

        # Selected text spanning two rows gets one annotation with two quads.
        select(page, center(chars[6][0]), center(chars[-2][0]))
        assert len(page.selection_quads) == 2
        menu_action(page, center(chars[7][0]), "메모")
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        editor.setPlainText("선택한 글자 메모")
        editor.finish(True)
        annots = list(win.doc[0].annots())
        note = annots[-1]
        assert note.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT
        assert note.info["content"] == "선택한 글자 메모" and len(note.vertices) == 8
        assert not page.selection_quads

        # The existing highlight can edit its note, and hovering shows updates.
        point = center(chars[-2][0])
        menu_action(page, point, "메모 수정")
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        editor.setPlainText("바뀐 메모")
        editor.finish(True)
        page.hover_at(QPointF(widget_point(page, point)), QPointF(10, 10))
        assert page.hover_note.isVisible() and page.hover_note.text() == "바뀐 메모"
        page.leaveEvent(QEvent(QEvent.Leave))
        page.hover_at(QPointF(widget_point(page, point)), QPointF(10, 10))
        assert page.hover_note.isVisible()

        # The marker outside the annotation rectangle also opens the note.
        source = page.page
        marker = page.to_widget(source.load_annot(note.xref).rect)
        marker_pos = QPointF(marker.right() + 2, marker.top() - 2)
        page.hover_at(marker_pos, QPointF(10, 10))
        assert page.hover_note.isVisible() and page.hover_note.text() == "바뀐 메모"

        # Canceling a new selected note creates no annotation.
        select(page, center(chars[0][0]), center(chars[1][0]))
        before = win.undo.count()
        menu_action(page, center(chars[0][0]), "메모")
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        editor.finish(False)
        assert win.undo.count() == before

        assert win.save()
        assert win.close()
        saved = pymupdf.open(path)
        assert any(a.info["content"] == "바뀐 메모" for a in saved[0].annots())
        saved.close()
    print("ok")


if __name__ == "__main__":
    main()
