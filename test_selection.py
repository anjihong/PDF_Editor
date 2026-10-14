"""Offscreen checks for selected text, annotation menus, and note hover."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pdf_editor
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from pdf_editor import HIDDEN, InlineEditor, Win


def widget_point(page, point):
    return page.to_widget(pymupdf.Rect(point, point)).center().toPoint()


def center(rect):
    return pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def select(page, first, last, choice=None):
    start, end = widget_point(page, first), widget_point(page, last)
    menus = []

    class ChoiceMenu(QMenu):
        def exec(self, global_pos):
            menus.append(([action.text() for action in self.actions()], global_pos))
            if choice == "Ctrl+C":
                QTest.keyClick(self, Qt.Key_C, Qt.ControlModifier)
            elif choice:
                next(action for action in self.actions() if action.text() == choice).trigger()

    with patch.object(pdf_editor, "QMenu", ChoiceMenu):
        QTest.mousePress(page, Qt.LeftButton, pos=start)
        QTest.mouseMove(page, end)
        QTest.mouseRelease(page, Qt.LeftButton, pos=end)
    return menus


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
        app.clipboard().setText("old")
        menus = select(page, first, last, "Ctrl+C")
        assert menus == [(["형광펜", "메모"], page.mapToGlobal(widget_point(page, last)))]
        assert app.clipboard().text() == "lph"
        assert len(page.selection_quads) == 1
        assert page.selection_quads[0].rect.width < pymupdf.Rect(page.get_words()[0][:4]).width
        app.clipboard().setText("old")
        QTest.keyClick(page, Qt.Key_C, Qt.ControlModifier)
        assert app.clipboard().text() == "lph"
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
        editor.selectAll()
        QTest.keyClick(editor, Qt.Key_C, Qt.ControlModifier)
        assert app.clipboard().text() == "기존 형광펜 메모"
        editor.finish(True)
        assert win.doc[0].load_annot(annots[0].xref).info["content"] == "기존 형광펜 메모"

        # Copy preserves spaces inside and between words, plus line breaks.
        select(page, center(chars[0][0]), center(chars[8][0]))
        QTest.keyClick(page, Qt.Key_C, Qt.ControlModifier)
        assert app.clipboard().text() == "alpha beta"
        app.clipboard().setText("old")
        QTimer.singleShot(0, lambda: QTest.keyClick(page.selection_menu, Qt.Key_C, Qt.ControlModifier))
        page.show_selection_menu(center(chars[8][0]), page.mapToGlobal(widget_point(page, center(chars[8][0]))))
        assert app.clipboard().text() == "alpha beta"
        app.processEvents()

        first_rect = chars[0][0]
        y = (first_rect.y0 + first_rect.y1) / 2
        select(page, pymupdf.Point(first_rect.x0 + 1, y), pymupdf.Point(first_rect.x1 - 1, y))
        QTest.keyClick(page, Qt.Key_C, Qt.ControlModifier)
        assert app.clipboard().text() == "a"
        select(page, center(chars[6][0]), center(chars[-2][0]))
        QTest.keyClick(page, Qt.Key_C, Qt.ControlModifier)
        assert app.clipboard().text() == "eta gamma\nsecond ro"

        # Selected text spanning two rows gets one annotation with two quads.
        menus = select(page, center(chars[6][0]), center(chars[-2][0]), "메모")
        assert len(menus) == 1
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        editor.setPlainText("선택한 글자 메모")
        editor.finish(True)
        annots = list(win.doc[0].annots())
        note = annots[-1]
        assert note.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT
        assert note.info["content"] == "선택한 글자 메모" and len(note.vertices) == 8
        assert not page.selection_quads

        app.clipboard().setText("keep")
        QTest.keyClick(page, Qt.Key_C, Qt.ControlModifier)
        assert app.clipboard().text() == "keep"
        win.show_search()
        win.search_edit.setText("search text")
        win.search_edit.selectAll()
        QTest.keyClick(win.search_edit, Qt.Key_C, Qt.ControlModifier)
        assert app.clipboard().text() == "search text"
        win.hide_search()

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

        # A single click opens no menu; direct highlight still works after a drag.
        assert not select(page, center(chars[0][0]), center(chars[0][0]))
        assert not page.selection_quads
        before = len(list(win.doc[0].annots()))
        assert len(select(page, center(chars[0][0]), center(chars[1][0]), "형광펜")) == 1
        assert len(list(win.doc[0].annots())) == before + 1
        assert not page.selection_quads

        assert win.save()
        assert win.close()
        saved = pymupdf.open(path)
        assert any(a.info["content"] == "바뀐 메모" for a in saved[0].annots())
        saved.close()
    print("ok")


if __name__ == "__main__":
    main()
