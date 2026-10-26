"""Pen hover must reuse hit geometry instead of loading every PDF annotation."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

import pdf_editor
from pdf_editor import Win, make_ink


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        pdf_editor.QSettings = lambda org, name: QSettings(QSettings.IniFormat, QSettings.UserScope, org, name)
        path = Path(directory) / "handwriting.pdf"
        doc = pymupdf.open()
        source = doc.new_page(width=600, height=400)
        for i in range(80):
            make_ink(source, [pymupdf.Point(10 + i * 4, 30), pymupdf.Point(11 + i * 4, 32)], (0, 0, 1), 2)
        source.add_highlight_annot([pymupdf.Rect(50, 200, 100, 215).quad,
                                    pymupdf.Rect(150, 200, 200, 215).quad])
        doc.save(path)
        doc.close()

        win = Win(str(path))
        win.show()
        app.processEvents()
        app.processEvents()
        win.set_tool("pen")
        page = win.pages[0]
        device = QPointingDevice("hover pen", 1, QInputDevice.DeviceType.Stylus,
                                 QPointingDevice.PointerType.Pen,
                                 QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 1)
        blank = QPointF(450 * page.zoom, 300 * page.zoom)
        page.hover_at(blank, blank)
        start = time.perf_counter()
        with patch.object(pymupdf.Page, "annots", side_effect=AssertionError("Repeated annotation scan")), \
                patch.object(pymupdf.Page, "load_annot", side_effect=AssertionError("Unneeded annotation load")):
            for i in range(500):
                pos = blank + QPointF(i % 10, 0)
                event = QTabletEvent(QEvent.TabletMove, device, pos, pos, 0,
                                     0, 0, 0, 0, 0, Qt.NoModifier, Qt.NoButton, Qt.NoButton)
                page.tabletEvent(event)
        print(f"500 pen hover events: {(time.perf_counter() - start) * 1000:.1f} ms")

        # Separate highlight quads must not turn their intervening gap into a hit.
        assert page.annot_at(pymupdf.Point(75, 207)) is not None
        assert page.annot_at(pymupdf.Point(125, 207)) is None

        # A completed stroke, undo, and redo must refresh cached hit geometry.
        first, last = QPointF(400 * page.zoom, 250 * page.zoom), QPointF(430 * page.zoom, 250 * page.zoom)
        page.update_pen_input(first, "pen")
        page.update_pen_input(last, "pen")
        page.update_pen_input(last, None)
        point = pymupdf.Point(415, 250)
        xref = page.annot_at(point).xref
        win.undo.undo()
        assert page.annot_at(point) is None
        win.undo.redo()
        assert page.annot_at(point).xref == xref

        # Canceling an in-progress erase restores the hit as well as the image.
        page.update_pen_input(last, "erase")
        assert page.annot_at(point) is None
        win.cancel_page_interaction()
        assert page.annot_at(point).xref == xref
        win.undo.setClean()
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
