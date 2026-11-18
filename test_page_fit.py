"""혼합 비율 PDF의 실제 화면 픽셀과 스크롤 끝의 편집 좌표를 검사한다."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import pdf_editor
from pdf_editor import Win


def settle(app):
    for _ in range(12):
        app.processEvents()


def make_pdf(path):
    with pymupdf.open() as doc:
        for width, height, rotation in [(4400, 700, 0), (500, 740, 90), (180, 14000, 0)]:
            page = doc.new_page(width=width, height=height)
            size = min(width, height) / 12
            for x in (0, width - size):
                for y in (0, height - size):
                    page.draw_rect((x, y, x + size, y + size), color=None, fill=(1, 0, 0))
            page.insert_text((40, 100), "corner test")
            page.set_rotation(rotation)
        doc.save(path)


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        pdf_editor.QSettings = lambda org, name: QSettings(QSettings.IniFormat, QSettings.UserScope, org, name)
        path = Path(directory) / "mixed.pdf"
        make_pdf(path)
        win = Win(str(path))
        win.show()
        settle(app)
        viewport = win.scroll.viewport()
        bar = win.scroll.verticalScrollBar()
        wide, rotated, tall = win.pages
        assert wide.zoom < 0.3 and tall.height() > 32767

        def assert_widths():
            assert win.scroll.horizontalScrollBar().maximum() == 0
            for page in win.pages:
                assert abs(page.width() - (viewport.width() - 32)) <= 1

        assert_widths()
        for page in win.pages:
            rect = page.page_size
            inset = min(rect.width, rect.height) / 24
            for y in (inset, rect.height - inset):
                bar.setValue(round(page.y() + y * page.zoom - viewport.height() / 2))
                settle(app)
                screenshot = viewport.grab().toImage()
                for x in (inset, rect.width - inset):
                    pos = page.mapTo(viewport, (QPointF(x, y) * page.zoom).toPoint())
                    assert viewport.rect().contains(pos)
                    pixel = screenshot.pixelColor((QPointF(pos) * screenshot.devicePixelRatio()).toPoint())
                    assert pixel.red() > 240 and pixel.green() < 20, (page.pno, pos, pixel.name())
        assert bar.value() == bar.maximum()
        assert tall.img.height() <= viewport.height() * tall.devicePixelRatioF() + 2
        QTest.qWait(140)
        settle(app)
        win.update_thumb(tall.pno)
        icon = win.thumbs.item(tall.pno).icon()
        size = icon.actualSize(win.thumbs.iconSize())
        assert 0 < size.height() <= 3 * win.thumbnail_width() + 1, (size, win.thumbs.iconSize())
        assert abs(size.width() - size.height() * tall.page_size.width / tall.page_size.height) <= 1

        # 실제 펜 입력으로 하단 PDF 좌표에 주석을 남긴다.
        win.set_tool("pen")
        point = pymupdf.Point(70, 13960)
        start = (QPointF(point.x, point.y) * tall.zoom).toPoint()
        end = (QPointF(point.x + 20, point.y) * tall.zoom).toPoint()
        QTest.mousePress(tall, Qt.LeftButton, pos=start)
        QTest.mouseMove(tall, end)
        QTest.mouseRelease(tall, Qt.LeftButton, pos=end)
        pdf_page = tall.page
        vertices = pdf_page.first_annot.vertices[0]
        assert abs(pymupdf.Point(vertices[0]) - point) < 1
        assert abs(pymupdf.Point(vertices[-1]) - (point + (20, 0))) < 1
        win.undo.undo()

        bar.setValue(bar.maximum() // 2)
        settle(app)
        pno, before = win.capture_zoom_anchor(win.viewport_center())
        win.resize(1030, win.height() - 100)
        settle(app)
        win.resizeDocks([win.pages_dock], [275], Qt.Horizontal)
        settle(app)
        assert_widths()
        after_page, after = win.capture_zoom_anchor(win.viewport_center())
        assert after_page == pno and abs(after.y() - before.y()) < 1

        # 가운데 페이지와 포인터가 놓인 페이지의 배율이 달라도 포인터를 기준으로 확대한다.
        bar.setValue(0)
        settle(app)
        pos = QPointF(rotated.mapTo(viewport, (QPointF(50, 50) * rotated.zoom).toPoint()))
        base = rotated.zoom
        assert viewport.rect().contains(pos.toPoint()) and base != wide.zoom
        win.begin_pinch(pos)
        win.update_pinch(1.2, pos)
        win.end_pinch()
        settle(app)
        assert abs(win.zoom - base * 1.2) < 1e-9
        win.fit_width()
        settle(app)
        assert_widths()
        win.search_text("corner test")
        expected, = rotated.page.search_for("corner test")
        hit, = rotated.search_hits
        assert max(abs(a - b) for a, b in zip(hit, expected)) < 0.01
        win.goto_page(tall.pno)
        settle(app)
        win.close()
        win = Win(str(path))
        win.show()
        settle(app)
        assert win.current_page() == tall.pno
        # 한 페이지가 Qt 스크롤 캔버스의 최대 높이를 초과해도 끝 표식이 보인다.
        huge_path = Path(directory) / "long-strip.pdf"
        with pymupdf.open() as doc:
            doc.new_page(width=600, height=800)
            strip = doc.new_page(width=2, height=100000)
            strip.draw_rect((0, 99998, 2, 100000), color=None, fill=(1, 0, 0))
            doc.save(huge_path)
        win.open(str(huge_path))
        settle(app)
        normal, strip = win.pages
        assert normal.width() > strip.width()
        bar = win.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        settle(app)
        viewport = win.scroll.viewport()
        pos = strip.mapTo(viewport, (QPointF(1, 99999) * strip.zoom).toPoint())
        assert viewport.rect().contains(pos), pos
        image = viewport.grab().toImage()
        pixel = image.pixelColor((QPointF(pos) * image.devicePixelRatio()).toPoint())
        assert pixel.red() > 240 and pixel.green() < 20, pixel.name()
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
