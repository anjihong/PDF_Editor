"""Check eraser cache reuse and text hit testing without timing-dependent gates."""
import os
import tempfile
from pathlib import Path
from unittest.mock import PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QPointF, QSettings
from PySide6.QtWidgets import QApplication

import pdf_editor
from pdf_editor import HIDDEN, Win, make_ink, segment_rect_hits


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        pdf_editor.QSettings = lambda org, name: QSettings(QSettings.IniFormat, QSettings.UserScope, org, name)
        path = Path(directory) / "tools.pdf"
        doc = pymupdf.open()
        source = doc.new_page(width=720, height=900)
        for i in range(60):
            source.insert_text((20, 25 + i * 12), "alpha beta gamma delta " * 5, fontsize=9)
        for i in range(80):
            make_ink(source, [pymupdf.Point(10 + i * 8, 800), pymupdf.Point(12 + i * 8, 810)], (0, 0, 1), 2)
        source.add_text_annot((680, 80), "note")
        doc.save(path)
        doc.close()

        win = Win(str(path))
        win.show()
        app.processEvents()
        app.processEvents()
        page = win.pages[0]
        regions = page.get_annot_regions()
        source = page.page
        assert [entry[0] for entry in regions] == [a.xref for a in source.annots() if not a.flags & HIDDEN]
        win.set_tool("erase")
        page.erased = []
        blank = pymupdf.Point(700, 870)

        # Eraser moves on empty paper must not reload any PDF annotation.
        with patch.object(pymupdf.Page, "annots", side_effect=AssertionError("Repeated annotation scan")), \
                patch.object(pymupdf.Page, "load_annot", side_effect=AssertionError("Unneeded annotation load")):
            for i in range(100):
                page.erase_at(pymupdf.Point(blank.x + i % 5, blank.y))
        assert page.erased == []

        # Deleting ink updates cached geometry even across paints during the drag.
        page.erase_previous = None
        with patch.object(pymupdf.Page, "first_annot", new_callable=PropertyMock,
                          side_effect=AssertionError("Cache rebuilt during erase")):
            page.erase_at(pymupdf.Point(10, 805))
            first_erased = set(page.erased)
            assert first_erased
            page.repaint()
            app.processEvents()
            page.erase_at(pymupdf.Point(170, 805))
            assert set(page.erased) > first_erased
            page.repaint()
            assert not set(page.erased).intersection(entry[0] for entry in page.get_annot_regions())
        erased = list(page.erased)
        page.finish_erase()
        assert win.undo.count() == 1
        win.undo.undo()
        assert all(not source.load_annot(xref).flags & HIDDEN for xref in erased)
        assert set(erased).issubset(entry[0] for entry in page.get_annot_regions())
        win.undo.redo()
        assert all(source.load_annot(xref).flags & HIDDEN for xref in erased)
        win.undo.undo()

        # Dense text outside the pointer needs no native containment calls.
        chars = page.get_text_chars()
        assert len(chars) > 5000
        with patch.object(pymupdf.Rect, "contains", side_effect=AssertionError("Distant text hit test")):
            assert page.text_char_at(blank, inside=True) is None
            assert not list(segment_rect_hits(blank, pymupdf.Point(705, 870), (item[0] for item in chars), 1))

        # Moving inside the same character must reuse the highlight preview.
        win.set_tool("hl")
        pos = page.to_widget(chars[0][0]).center()
        page.start_highlight(pos)
        preview = page.preview
        assert preview
        with patch.object(pymupdf, "recover_span_quad", side_effect=AssertionError("Unchanged highlight rebuilt")):
            page.move_highlight(pos + QPointF(0.01, 0))
        assert page.preview is preview
        page.cancel_highlight()

        # Broad-phase filtering preserves tolerance, reversed segments, and diagonal misses.
        rects = [pymupdf.Rect(10, 10, 20, 20), pymupdf.Rect(50, 50, 60, 60)]
        assert list(segment_rect_hits(pymupdf.Point(30, 15), pymupdf.Point(0, 15), rects)) == [0]
        assert list(segment_rect_hits(pymupdf.Point(9, 5), pymupdf.Point(9, 25), rects, 1)) == [0]
        assert not list(segment_rect_hits(pymupdf.Point(0, 15), pymupdf.Point(15, 30), rects))
        win.undo.setClean()
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
