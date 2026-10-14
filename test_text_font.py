"""Local font checks for new PDF text and the app's inline editors."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFontDatabase, QFontInfo, QFontMetricsF, QUndoStack
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from pdf_editor import (InlineEditor, MoveAnnot, SetContent, TEXT_FONT_FAMILY,
                        TEXT_FONT_PATH, THEMES, custom_text_rect, is_custom_text,
                        make_text, set_hidden, text_font)


def main():
    assert TEXT_FONT_PATH.is_file(), f"Missing local font: {TEXT_FONT_PATH}"
    app = QApplication([])
    assert QFontDatabase.addApplicationFont(str(TEXT_FONT_PATH)) >= 0
    doc = pymupdf.open()
    page = doc.new_page()
    legacy = page.add_freetext_annot((50, 180, 300, 230), "기존 글", fontsize=16)
    legacy_ap = legacy._getAP()
    original_contents = page.get_contents()
    source = "한글 ABC 123\n둘째 줄"
    expected = "한글 ABC 123\n둘째 줄"
    annot = make_text(page, pymupdf.Point(40, 50), source, (0.2, 0.4, 0.8), 20)
    xref = annot.xref
    assert page.get_contents() == original_contents
    assert is_custom_text(doc, xref)
    assert annot.info["content"] == expected
    assert expected in page.get_text()
    assert "\u00a0" not in page.get_text()
    assert legacy._getAP() == legacy_ap
    set_hidden(page, xref, True)   # Clicking existing text opens an editor.
    set_hidden(page, xref, False)  # Closing it without changes must keep the font.
    spans = [span for block in page.get_text("dict")["blocks"]
             for line in block.get("lines", []) for span in line["spans"]
             if "한글" in span["text"] or "둘째" in span["text"]]
    assert spans and all(span["font"].startswith(TEXT_FONT_FAMILY) for span in spans)

    class FakePage:
        def invalidate(self):
            pass

    class FakeWin:
        pages = [FakePage()]

        def __init__(self):
            self.doc = doc

    win = FakeWin()
    stack = QUndoStack()
    edited = "한글 띄어쓰기\nABC 123"
    stack.push(SetContent(win, 0, xref, edited,
                          custom_text_rect(annot.rect.top_left, edited, 26),
                          fontsize=26))
    assert edited in page.get_text() and "\u00a0" not in page.get_text()
    stack.undo()
    assert expected in page.get_text() and "\u00a0" not in page.get_text()
    stack.redo()
    assert edited in page.get_text()
    old = page.load_annot(xref).rect
    stack.push(MoveAnnot(win, 0, xref, old, old + pymupdf.Rect(40, 50, 40, 50)))
    assert edited in page.get_text()
    stack.undo()
    assert edited in page.get_text()
    assert page.load_annot(xref).rect == old

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "font.pdf"
        doc.save(path)
        reopened = pymupdf.open(path)
        result = reopened[0]
        assert edited in result.get_text() and "\u00a0" not in result.get_text()
        assert is_custom_text(reopened, xref)
        embedded = reopened.extract_font(next(item[0] for item in result.get_fonts()
                                           if item[3].startswith("Memoment")))[3]
        assert len(embedded) > 100_000
        assert result.load_annot(legacy.xref)._getAP() == legacy_ap
        reopened.close()

    parent = QWidget()
    parent.setStyleSheet("QTextEdit { font-size: 10pt; }")
    text_style = f"QTextEdit{{font-family:'{TEXT_FONT_FAMILY}';padding:0}}"
    editor = InlineEditor(parent, QPoint(0, 0), "한글 ABC", 30, text_style, lambda _: None)
    app.processEvents()
    assert QFontInfo(editor.font()).family() == TEXT_FONT_FAMILY
    assert editor.font().pixelSize() == 30, editor.font().toString()
    sample = "한글 ABC 123 공백"
    preview_width = QFontMetricsF(editor.font()).horizontalAdvance(sample)
    pdf_width = text_font().text_length(sample, fontsize=20) * 1.5
    assert abs(preview_width - pdf_width) / pdf_width < 0.05
    editor.set_font_px(45)
    app.processEvents()
    assert editor.font().pixelSize() == 45
    preview_width = QFontMetricsF(editor.font()).horizontalAdvance(sample)
    pdf_width = text_font().text_length(sample, fontsize=30) * 1.5
    assert abs(preview_width - pdf_width) / pdf_width < 0.05
    editor.hide()
    note = InlineEditor(parent, QPoint(0, 0), "메모", 13,
                        THEMES["다크"]["note_style"], lambda _: None, width=220)
    app.processEvents()
    assert QFontInfo(note.font()).family() == TEXT_FONT_FAMILY
    assert TEXT_FONT_FAMILY in THEMES["다크"]["note_style"]
    assert TEXT_FONT_FAMILY in THEMES["라이트"]["note_style"]
    hover = QLabel("메모", parent)
    hover.setStyleSheet(THEMES["다크"]["note_style"])
    app.processEvents()
    assert QFontInfo(hover.font()).family() == TEXT_FONT_FAMILY
    note.hide()
    parent.close()
    doc.close()
    print("ok")


if __name__ == "__main__":
    main()
