"""Offscreen mouse and stylus checks for text-aware highlighting."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdf_editor import HIDDEN, Win


def widget_point(widget, point):
    rotated = point * widget.page.rotation_matrix
    return QPoint(round(rotated.x * widget.zoom), round(rotated.y * widget.zoom))


def mouse_stroke(widget, points):
    positions = [widget_point(widget, point) for point in points]
    QTest.mousePress(widget, Qt.LeftButton, pos=positions[0])
    for pos in positions[1:-1]:
        QTest.mouseMove(widget, pos)
    QTest.mouseRelease(widget, Qt.LeftButton, pos=positions[-1])


def tablet_event(widget, device, kind, point, pressure, buttons):
    pos = QPointF(widget_point(widget, point))
    event = QTabletEvent(kind, device, pos, QPointF(widget.mapToGlobal(pos.toPoint())), pressure,
                         0, 0, 0, 0, 0, Qt.NoModifier, Qt.LeftButton, buttons)
    QApplication.sendEvent(widget, event)
    assert event.isAccepted()


def tablet_stroke(widget, device, points):
    tablet_event(widget, device, QEvent.TabletPress, points[0], 1, Qt.LeftButton)
    for point in points[1:-1]:
        tablet_event(widget, device, QEvent.TabletMove, point, 1, Qt.LeftButton)
    tablet_event(widget, device, QEvent.TabletRelease, points[-1], 0, Qt.NoButton)


def annots(widget):
    widget._page = widget.page
    return list(widget._page.annots())


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "highlight.pdf"
        doc = pymupdf.open()
        text = doc.new_page(width=400, height=300)
        text.insert_text((50, 90), "alpha beta gamma", fontsize=16)
        text.insert_text((50, 130), "second row", fontsize=16)
        image = text.get_pixmap().tobytes("png")
        scan = doc.new_page(width=400, height=300)
        scan.insert_image(scan.rect, stream=image)
        doc[0].set_rotation(90)
        doc.save(path)
        doc.close()

        win = Win(str(path))
        win.show()
        app.processEvents()
        app.processEvents()
        win.set_tool("hl")
        page = win.pages[0]
        words = page.get_words()
        assert len(words) >= 5
        first, third = (pymupdf.Point((words[i][0] + words[i][2]) / 2,
                                      (words[i][1] + words[i][3]) / 2) for i in (0, 2))

        # On a rotated page, an uneven mouse path snaps to one text line.
        mouse_stroke(page, [first, pymupdf.Point(first.x + 25, first.y - 12), third])
        result = annots(page)[0]
        assert result.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT
        assert result.rect.y1 < pymupdf.Rect(words[3][:4]).y0
        assert win.undo.count() == 1
        win.undo.undo()
        assert result.flags & HIDDEN
        win.undo.redo()
        assert not result.flags & HIDDEN

        # A mixed path selects text only, even when it begins and ends on blank paper.
        mouse_stroke(page, [pymupdf.Point(25, 200), first, pymupdf.Point(300, 200)])
        assert len(annots(page)) == 2
        assert annots(page)[-1].type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT

        # An empty path uses a broad, separately adjustable highlight width.
        assert win.width_spin.value() == 20 and win.hl_width == 20 and win.width == 2
        win.width_spin.setValue(24)
        mouse_stroke(page, [pymupdf.Point(30, 240), pymupdf.Point(80, 245), pymupdf.Point(130, 240)])
        freehand = annots(page)[-1]
        assert freehand.type[0] == pymupdf.PDF_ANNOT_INK
        assert abs(freehand.opacity - 0.35) < 0.01
        assert freehand.border["width"] == 24 and win.width == 2
        assert all(abs(actual - expected) < 0.01 for actual, expected in
                   zip(freehand.colors["stroke"], (253 / 255, 1.0, 149 / 255)))

        # Tool changes and a temporary eraser cancel a live highlight stroke.
        count = win.undo.count()
        page.start_highlight(widget_point(page, pymupdf.Point(30, 250)))
        win.set_tool("pen")
        assert not page.hl_points and win.undo.count() == count
        assert win.width_spin.value() == 2
        win.set_tool("hl")
        assert win.width_spin.value() == 24
        page.start_highlight(widget_point(page, pymupdf.Point(30, 250)))
        win.set_qt_pen_eraser(True)
        assert not page.hl_points and win.undo.count() == count
        win.set_qt_pen_eraser(False)

        device = QPointingDevice("highlight pen", 4, QInputDevice.DeviceType.Stylus,
                                 QPointingDevice.PointerType.Pen,
                                 QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 2)
        tablet_stroke(page, device, [pymupdf.Point(30, 260), pymupdf.Point(90, 260)])
        assert annots(page)[-1].type[0] == pymupdf.PDF_ANNOT_INK
        tablet_stroke(page, device, [pymupdf.Point(25, 210), first, pymupdf.Point(250, 210)])
        assert annots(page)[-1].type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT

        scanned = win.pages[1]
        assert not scanned.get_words()
        tablet_stroke(scanned, device, [pymupdf.Point(55, 85), pymupdf.Point(160, 85)])
        assert annots(scanned)[0].type[0] == pymupdf.PDF_ANNOT_INK

        assert win.save()
        assert win.close()
        reopened = pymupdf.open(path)
        reopened_text, reopened_scan = reopened[0], reopened[1]
        assert [a.type[0] for a in reopened_text.annots()] == [
            pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_HIGHLIGHT,
            pymupdf.PDF_ANNOT_INK, pymupdf.PDF_ANNOT_INK,
            pymupdf.PDF_ANNOT_HIGHLIGHT]
        assert [a.type[0] for a in reopened_scan.annots()] == [pymupdf.PDF_ANNOT_INK]
        assert abs(next(a for a in reopened_text.annots() if a.type[0] == pymupdf.PDF_ANNOT_INK).opacity - 0.35) < 0.01
        reopened.close()
    print("ok")


if __name__ == "__main__":
    main()
