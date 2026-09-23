"""Headless GUI checks for pen-button erasing and background touch panning."""
import os
import tempfile
import ctypes
import ctypes.wintypes
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (QContextMenuEvent, QInputDevice, QPointingDevice, QTabletEvent,
                           QTouchEvent, QWheelEvent)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdf_editor import HIDDEN, PointerPenInfo, Win, make_ink


def tablet_event(page, device, kind, x, y, pressure, button, buttons):
    pos = QPointF(x * page.zoom, y * page.zoom)
    event = QTabletEvent(kind, device, pos, QPointF(page.mapToGlobal(pos.toPoint())), pressure,
                         0, 0, 0, 0, 0, Qt.NoModifier, button, buttons)
    QApplication.sendEvent(page, event)


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.pdf"
        doc = pymupdf.open()
        first = doc.new_page(width=600, height=400)
        ink = make_ink(first, [pymupdf.Point(140, 100), pymupdf.Point(160, 100)], (1, 0, 0), 8)
        thin = make_ink(first, [pymupdf.Point(140, 160), pymupdf.Point(160, 160)], (1, 0, 0), 1.5)
        swept = make_ink(first, [pymupdf.Point(140, 200), pymupdf.Point(160, 200)], (1, 0, 0), 1.5)
        doc.new_page(width=600, height=400)
        doc.save(path)
        doc.close()

        win = Win(str(path))
        win.show()
        app.processEvents()
        app.processEvents()
        page = win.pages[0]

        # High-resolution Ctrl+wheel input uses the actual delta instead of a fixed 10% step.
        page.repaint()
        app.processEvents()
        cached_image = page.img
        old_zoom = win.zoom
        local = QPointF(100, 100)
        global_pos = QPointF(page.mapToGlobal(local.toPoint()))
        viewport_pos = QPointF(win.scroll.viewport().mapFromGlobal(global_pos.toPoint()))
        wheel = QWheelEvent(local, global_pos, QPoint(), QPoint(0, 12), Qt.NoButton,
                            Qt.ControlModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(page, wheel)
        for _ in range(3):
            app.processEvents()
        expected = old_zoom * 1.1 ** 0.1
        assert win.zoom == old_zoom and abs(win._preview_target_zoom - expected) < 1e-9
        assert win.zoom_preview.isVisible() and page.img is cached_image
        QTest.qWait(150)
        for _ in range(3):
            app.processEvents()
        assert abs(win.zoom - expected) < 1e-9 and not win.zoom_preview.isVisible()
        assert page.img is not cached_image
        anchored = QPointF(100 / old_zoom * win.zoom, 100 / old_zoom * win.zoom).toPoint()
        actual = win.scroll.viewport().mapFromGlobal(page.mapToGlobal(anchored))
        assert (QPointF(actual) - viewport_pos).manhattanLength() <= 4, (actual, viewport_pos)

        # Screen pinch previews without layout work or scrolling, then commits once.
        pinch_center = QPointF(140, 120)
        base_zoom = win.zoom
        old_scroll = (win.scroll.horizontalScrollBar().value(), win.scroll.verticalScrollBar().value())
        page.pen_input = "pen"
        page.pts = [QPointF(10, 10), QPointF(20, 20)]
        win.background_pan.target = win.scroll.widget()
        win.background_pan.touch_id = 9
        win.begin_pinch(pinch_center)
        assert page.pen_input is None and not page.pts and win.background_pan.touch_id is None
        win.update_pinch(1.15, pinch_center)
        assert win.zoom == base_zoom and abs(win._preview_target_zoom - base_zoom * 1.15) < 1e-9
        assert old_scroll == (win.scroll.horizontalScrollBar().value(), win.scroll.verticalScrollBar().value())
        win.end_pinch()
        for _ in range(4):
            app.processEvents()
        assert abs(win.zoom - base_zoom * 1.15) < 1e-9 and win._pinch_start_zoom is None

        win.set_zoom(99)
        assert win.zoom == 5.0
        win.set_zoom(0.01)
        assert win.zoom == 0.3
        win.set_zoom(base_zoom)
        for _ in range(3):
            app.processEvents()
        win.set_tool("pen")
        point = QPoint(round(150 * win.zoom), round(100 * win.zoom))

        # A plain right click is the fallback when the S Pen is reported as a mouse.
        QTest.mousePress(page, Qt.RightButton, pos=point)
        QTest.mouseRelease(page, Qt.RightButton, pos=point)
        assert win.tool == "pen" and win.doc[0].load_annot(ink.xref).flags & HIDDEN
        assert win.undo.count() == 1
        win.undo.undo()
        assert not win.doc[0].load_annot(ink.xref).flags & HIDDEN

        # A narrow handwritten stroke can be erased within an 8-pixel tip radius.
        near = QPoint(round(150 * win.zoom), round(153 * win.zoom))
        QTest.mousePress(page, Qt.RightButton, pos=near)
        QTest.mouseRelease(page, Qt.RightButton, pos=near)
        assert win.doc[0].load_annot(thin.xref).flags & HIDDEN
        assert not win.doc[0].load_annot(swept.xref).flags & HIDDEN
        win.undo.undo()
        assert not win.doc[0].load_annot(thin.xref).flags & HIDDEN

        # This Galaxy Book reports the S Pen button as a native eraser flag,
        # while the Qt tablet event still has only LeftButton set.
        device = QPointingDevice("test pen", 1, QInputDevice.DeviceType.Stylus,
                                 QPointingDevice.PointerType.Pen,
                                 QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 2)
        native = win.pen_button
        assert native is not None
        pen_flags = [0x6]
        original_get_pen_info = native.get_pen_info

        def fake_get_pen_info(_pointer_id, output):
            ctypes.cast(output, ctypes.POINTER(PointerPenInfo)).contents.penFlags = pen_flags[0]
            return True

        native.get_pen_info = fake_get_pen_info
        msg = ctypes.wintypes.MSG()
        msg.message = 0x0245
        msg.wParam = 123
        native.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
        assert native.eraser
        tablet_event(page, device, QEvent.TabletPress, 150, 100, 1, Qt.LeftButton, Qt.LeftButton)
        assert page.pen_input == "erase" and win.doc[0].load_annot(ink.xref).flags & HIDDEN
        msg.message = 0x0247
        native.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
        assert not native.eraser
        tablet_event(page, device, QEvent.TabletRelease, 150, 100, 0, Qt.LeftButton, Qt.NoButton)
        win.undo.undo()
        assert not win.doc[0].load_annot(ink.xref).flags & HIDDEN
        native.get_pen_info = original_get_pen_info

        # A fast eraser movement must hit a line crossed between two events.
        page.erased = []
        page.erase_previous = None
        page.erase_at(pymupdf.Point(120, 190))
        page.erase_at(pymupdf.Point(180, 210))
        page.finish_erase()
        assert win.doc[0].load_annot(swept.xref).flags & HIDDEN
        win.undo.undo()
        assert not win.doc[0].load_annot(swept.xref).flags & HIDDEN

        # Distant ink remains untouched.
        page.erased = []
        page.erase_previous = None
        page.erase_at(pymupdf.Point(150, 175))
        page.finish_erase()
        assert not win.doc[0].load_annot(thin.xref).flags & HIDDEN

        # A tablet side button splits a live pen stroke, erases, then resumes writing.
        tablet_event(page, device, QEvent.TabletPress, 100, 100, 1, Qt.LeftButton, Qt.LeftButton)
        tablet_event(page, device, QEvent.TabletMove, 120, 100, 1, Qt.NoButton, Qt.LeftButton)
        tablet_event(page, device, QEvent.TabletPress, 140, 100, 1, Qt.RightButton,
                     Qt.LeftButton | Qt.RightButton)
        assert page.pen_input == "erase" and win.undo.count() == 1
        first_pen_xref = win.undo.command(0).xref
        assert not win.doc[0].load_annot(first_pen_xref).flags & HIDDEN
        tablet_event(page, device, QEvent.TabletRelease, 170, 100, 1, Qt.RightButton, Qt.LeftButton)
        assert page.pen_input == "pen" and win.doc[0].load_annot(ink.xref).flags & HIDDEN
        assert not win.doc[0].load_annot(first_pen_xref).flags & HIDDEN
        tablet_event(page, device, QEvent.TabletMove, 190, 100, 1, Qt.NoButton, Qt.LeftButton)
        tablet_event(page, device, QEvent.TabletRelease, 190, 100, 0, Qt.LeftButton, Qt.NoButton)
        assert page.pen_input is None and win.undo.count() == 3 and win.tool == "pen"
        win.undo.undo()  # resumed pen stroke
        win.undo.undo()  # eraser drag
        assert not win.doc[0].load_annot(ink.xref).flags & HIDDEN

        # Holding the S Pen button temporarily overrides every selected tool.
        native.get_pen_info = fake_get_pen_info
        for selected_tool in (None, "hl", "text", "note", "erase"):
            win.set_tool(selected_tool)
            pen_flags[0] = 0x6
            msg.message = 0x0245
            native.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
            assert native.eraser and win.temporary_eraser
            assert next(a for a in win.tool_group.actions() if a.data() == "erase").isChecked()
            tablet_event(page, device, QEvent.TabletPress, 150, 100, 1, Qt.LeftButton, Qt.LeftButton)
            assert win.doc[0].load_annot(ink.xref).flags & HIDDEN
            msg.message = 0x0247
            native.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
            tablet_event(page, device, QEvent.TabletRelease, 150, 100, 0, Qt.LeftButton, Qt.NoButton)
            assert win.tool == selected_tool and not win.temporary_eraser
            assert all(a.isChecked() == (a.data() == selected_tool)
                       for a in win.tool_group.actions())
            win.undo.undo()
            assert not win.doc[0].load_annot(ink.xref).flags & HIDDEN
        native.get_pen_info = original_get_pen_info

        eraser_device = QPointingDevice("test eraser", 2, QInputDevice.DeviceType.Stylus,
                                        QPointingDevice.PointerType.Eraser,
                                        QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 2)
        win.set_tool("hl")
        tablet_event(page, eraser_device, QEvent.TabletPress, 150, 100, 1, Qt.LeftButton, Qt.LeftButton)
        assert win.temporary_eraser and win.doc[0].load_annot(ink.xref).flags & HIDDEN
        tablet_event(page, eraser_device, QEvent.TabletRelease, 150, 100, 0, Qt.LeftButton, Qt.NoButton)
        assert not win.temporary_eraser and win.tool == "hl"
        win.undo.undo()
        assert not win.doc[0].load_annot(ink.xref).flags & HIDDEN

        menu = QContextMenuEvent(QContextMenuEvent.Mouse, point, page.mapToGlobal(point))
        win.set_tool("pen")
        QApplication.sendEvent(page, menu)
        assert win.tool == "pen"

        # Other tools retain the existing right-click cancellation behavior.
        win.set_tool("hl")
        QApplication.sendEvent(page, menu)
        assert win.tool is None

        # A touch that starts on a page keeps the page's drawing behavior.
        win.set_tool("pen")
        touch = QTest.createTouchDevice()
        other = win.pages[1]
        win.scroll.verticalScrollBar().setValue(other.y() - 100)
        app.processEvents()
        app.processEvents()
        page_scroll = win.scroll.verticalScrollBar().value()
        before = win.undo.index()
        sequence = QTest.touchEvent(other, touch, False)
        sequence.press(1, QPoint(100, 100), other).commit()
        sequence.move(1, QPoint(130, 100), other).commit()
        sequence.release(1, QPoint(130, 100), other).commit()
        assert win.background_pan.touch_id is None and win.undo.index() == before + 1
        assert win.scroll.verticalScrollBar().value() == page_scroll, (
            page_scroll, win.scroll.verticalScrollBar().value())

        # Two touchscreen points are consumed by zoom and never reach scrolling/drawing.
        pinch_zoom = win.zoom
        pinch_scroll = (win.scroll.horizontalScrollBar().value(), win.scroll.verticalScrollBar().value())
        before = win.undo.index()
        sequence = QTest.touchEvent(other, touch, False)
        sequence.press(10, QPoint(140, 120), other).press(11, QPoint(240, 120), other).commit()
        sequence.move(10, QPoint(120, 120), other).move(11, QPoint(260, 120), other).commit()
        assert win._touch_pinch_consuming and win._preview_target_zoom > pinch_zoom
        assert pinch_scroll == (win.scroll.horizontalScrollBar().value(), win.scroll.verticalScrollBar().value())
        assert win.undo.index() == before
        sequence.release(10, QPoint(120, 120), other).release(11, QPoint(260, 120), other).commit()
        for _ in range(4):
            app.processEvents()
        assert not win._touch_pinch_consuming and win.zoom > pinch_zoom

        # A touch starting in the page gap scrolls in both directions, even after
        # crossing onto a page. Ending the touch clears the gesture state.
        win.set_zoom(2)
        app.processEvents()
        app.processEvents()
        canvas = win.scroll.widget()
        gap_y = (win.pages[0].geometry().bottom() + win.pages[1].geometry().top()) // 2
        hbar, vbar = win.scroll.horizontalScrollBar(), win.scroll.verticalScrollBar()
        hbar.setValue(100)
        vbar.setValue(gap_y - win.scroll.viewport().height() // 2)
        start_h, start_v = hbar.value(), vbar.value()
        sequence = QTest.touchEvent(canvas, touch, False)
        sequence.press(2, QPoint(200, gap_y), canvas).commit()
        assert win.background_pan.touch_id == 2, (
            gap_y, canvas.mapToGlobal(QPoint(200, gap_y)), win.scroll.viewport().geometry(),
            win.pages[0].geometry(), win.pages[1].geometry(), start_h, start_v)
        sequence.move(2, QPoint(170, gap_y - 40), canvas).commit()
        assert hbar.value() == start_h + 30 and vbar.value() == start_v + 40
        sequence.release(2, QPoint(170, gap_y - 40), canvas).commit()
        assert win.background_pan.touch_id is None
        sequence = QTest.touchEvent(canvas, touch, False)
        sequence.press(3, QPoint(200, gap_y), canvas).commit()
        assert win.background_pan.touch_id == 3
        QApplication.sendEvent(canvas, QTouchEvent(QEvent.TouchCancel, touch))
        assert win.background_pan.touch_id is None
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
