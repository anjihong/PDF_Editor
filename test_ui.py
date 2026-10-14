"""Offscreen checks for dark/light themes and responsive controls."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pdf_editor
from PySide6.QtCore import QPoint, QPointF, QRect, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from pdf_editor import InlineEditor, PRESETS, THEMES, Win, note_text_width, note_x_and_width


def settle(app):
    for _ in range(12):
        app.processEvents()


def main():
    with tempfile.TemporaryDirectory() as directory:
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        pdf_editor.QSettings = lambda org, app: QSettings(QSettings.IniFormat, QSettings.UserScope, org, app)
        settings = pdf_editor.QSettings("pdf-editor", "PdfEditor")
        settings.setValue("theme", "파스텔")
        path = Path(directory) / "workspace.pdf"
        doc = pymupdf.open()
        first = doc.new_page(width=595, height=842)
        first.insert_text((72, 72), "search target")
        first.add_freetext_annot((300, 100, 430, 130), "annotation target")
        doc.new_page(width=842, height=595)
        last = doc.new_page(width=420, height=842)
        last.insert_text((72, 72), "search target")
        doc.save(path)
        doc.close()

        app = QApplication([])
        win = Win(str(path))
        win.show()
        settle(app)
        # 이전 버전의 테마 값은 현재 기본 다크 테마로 마이그레이션된다.
        assert win.theme is THEMES["다크"] and win.theme_name == "다크"
        assert settings.value("theme") == "다크" and win.current_page() == 0
        assert win.theme_actions["다크"].isChecked() and not win.theme_actions["라이트"].isChecked()
        assert win.pages_dock.isVisible() and win.properties_dock.isVisible()
        assert not win.scroll_page_indicator.isVisible()
        QTest.qWait(130)
        settle(app)

        def assert_thumbnails_fit():
            expected_width = win.thumbnail_width()
            assert win.thumbs.iconSize().width() == expected_width
            for pno in range(win.thumbs.count()):
                actual = win.thumbs.item(pno).icon().actualSize(win.thumbs.iconSize())
                page = win.doc[pno]
                assert actual.width() == expected_width
                assert abs(actual.height() / actual.width() - page.rect.height / page.rect.width) < 0.02

        assert_thumbnails_fit()
        selected_page = win.current_page()
        old_thumb_width = win.thumbs.iconSize().width()
        win.resizeDocks([win.pages_dock], [320], Qt.Horizontal)
        QTest.qWait(130)
        settle(app)
        assert win.thumbs.iconSize().width() > old_thumb_width
        assert win.current_page() == selected_page
        assert win.thumbs.currentRow() == selected_page
        assert_thumbnails_fit()
        win.update_thumb(0)
        assert win.thumbs.item(0).icon().actualSize(win.thumbs.iconSize()).width() == win.thumbs.iconSize().width()
        wide_thumb_width = win.thumbs.iconSize().width()
        win.resizeDocks([win.pages_dock], [205], Qt.Horizontal)
        QTest.qWait(130)
        settle(app)
        assert win.thumbs.iconSize().width() < wide_thumb_width
        assert win.current_page() == selected_page
        assert_thumbnails_fit()

        win.resize(1024, 768)
        settle(app)
        assert win.pages[0].width() <= win.scroll.viewport().width()
        assert win.current_page() == 0
        for action in (win.open_act, win.save_act, *win.tool_group.actions()):
            assert win.tb.widgetForAction(action).isVisible(), action.text()

        QTest.keyClick(win, Qt.Key_F, Qt.ControlModifier)
        settle(app)
        assert win.search_bar.isVisible() and win.search_edit.hasFocus()
        win.search_edit.setText("search target")
        settle(app)
        assert len(win.search_results) == 2 and win.search_count.text() == "1 / 2"
        assert len(win.pages[0].search_hits) == 1 and len(win.pages[2].search_hits) == 1
        QTest.keyClick(win.search_edit, Qt.Key_Return)
        settle(app)
        assert win.search_count.text() == "2 / 2" and win.current_page() == 2
        QTest.keyClick(win.search_edit, Qt.Key_Return)
        settle(app)
        assert win.search_count.text() == "1 / 2" and win.current_page() == 0
        QTest.keyClick(win.search_edit, Qt.Key_Return, Qt.ShiftModifier)
        settle(app)
        assert win.search_count.text() == "2 / 2" and win.current_page() == 2
        win.search_edit.setText("missing")
        assert not win.search_results and win.search_count.text() == "0 / 0"
        assert not win.search_prev.isEnabled() and not win.search_next.isEnabled()
        win.search_edit.setText("annotation target")
        assert not win.search_results
        win.search_edit.setText("search target")
        QTest.keyClick(win.search_edit, Qt.Key_Escape)
        settle(app)
        assert not win.search_bar.isVisible() and not win.search_results
        assert all(not page.search_hits for page in win.pages)
        win.goto_page(0)
        settle(app)

        win.goto_page(1)
        settle(app)
        indicator = win.scroll_page_indicator
        assert indicator.isVisible() and indicator.text() == "2"
        bar = win.scroll.verticalScrollBar()
        option = QStyleOptionSlider()
        bar.initStyleOption(option)
        handle = bar.style().subControlRect(
            QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
        assert indicator.x() + indicator.width() == win.scroll.viewport().width() - 4
        assert abs(indicator.geometry().center().y() - handle.center().y()) <= 1
        assert indicator.geometry().bottom() < win.scroll.viewport().height()

        QTest.qWait(400)
        remaining = win.scroll_page_timer.remainingTime()
        win.goto_page(2)
        settle(app)
        assert indicator.isVisible() and indicator.text() == "3"
        assert win.scroll_page_timer.remainingTime() > remaining
        QTest.qWait(750)
        assert not indicator.isVisible()

        win.set_tool("hl")
        assert win.color_section.isVisible() and win.width_section.isVisible()
        assert not win.font_section.isVisible()
        win.width_spin.setValue(24)
        win.set_color(PRESETS["hl"][1])
        assert win.hl_width == 24 and win.colors["hl"] == PRESETS["hl"][1]

        win.set_tool("pen")
        assert win.width_spin.value() == 2 and win.colors["pen"] == "#426eff"
        win.set_tool("text")
        assert win.color_section.isVisible() and win.font_section.isVisible()
        assert not win.width_section.isVisible()
        win.font_spin.setValue(26)
        assert win.font_size == 26

        win.goto_page(0)
        settle(app)
        page = win.pages[0]
        for tool, start in (("text", page.start_text), ("note", page.start_note)):
            before = len(list(win.doc[0].annots()))
            win.set_tool(tool)
            start(pymupdf.Point(150, 150))
            editor = page.findChild(InlineEditor)
            QTest.keyClicks(editor, tool)
            QTest.mouseClick(page, Qt.LeftButton, pos=QPointF(300 * page.zoom, 300 * page.zoom).toPoint())
            settle(app)
            assert win.tool is None and not page.findChildren(InlineEditor)
            assert len(list(win.doc[0].annots())) == before + 1

        win.set_tool(None)
        assert not win.color_section.isVisible() and not win.width_section.isVisible()

        win.properties_dock.hide()
        settle(app)
        assert not win.properties_dock.isVisible()
        win.properties_dock.toggleViewAction().trigger()
        settle(app)
        assert win.properties_dock.isVisible()

        win.theme_actions["라이트"].trigger()
        settle(app)
        assert win.theme is THEMES["라이트"] and settings.value("theme") == "라이트"
        assert win.theme_actions["라이트"].isChecked() and "다크" in win.theme_toggle_act.text()
        assert win.scroll.widget().palette().color(win.scroll.widget().backgroundRole()).name() == "#e7ebeb"
        win.theme_toggle_act.trigger()
        settle(app)
        assert win.theme_name == "다크" and "라이트" in win.theme_toggle_act.text()

        note = win.doc[0].add_text_annot((100, 100), "노란 메모")
        page = win.pages[0]
        page.hover_at(page.to_widget(note.rect).center(), QPointF(10, 10))
        assert page.hover_note.isVisible() and "#fff8d7" in page.hover_note.styleSheet()
        hover_pos = page.hover_note.pos()
        page.hover_at(page.to_widget(note.rect).topLeft() + QPointF(1, 1), QPointF(500, 500))
        assert page.hover_note.pos() == hover_pos
        win.apply_theme("라이트")
        assert page.hover_note.isVisible() and "#fff8d7" in page.hover_note.styleSheet()
        page.hover_at(QPointF(0, 0), QPointF(0, 0))
        assert not page.hover_note.isVisible()

        # Note width follows the longest line and stays inside the document area.
        assert note_x_and_width(100, QRect(0, 0, 800, 400), 180, 8) == (100, 180)
        assert note_x_and_width(310, QRect(0, 0, 320, 400), 500, 12, 12) == (12, 296)
        win.set_tool("note")
        page.start_note(pymupdf.Point(80, 250))
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        assert editor.width() == 40
        editor.setPlainText("메모")
        short_width = editor.width()
        assert short_width == note_text_width(editor.fontMetrics(), "메모")
        editor.setPlainText("메모\n이 줄은 조금 더 긴 메모")
        assert editor.width() > short_width
        assert editor.width() == note_text_width(editor.fontMetrics(), "이 줄은 조금 더 긴 메모")
        editor.setPlainText("메모")
        assert editor.width() == short_width
        editor.finish(False)
        settle(app)
        edge = pymupdf.Point(page.page.rect.width - 10, 300)
        page.start_note(edge)
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        editor.setPlainText("메모 내용이 오른쪽 속성 패널로 넘어가면 안 됩니다")
        assert editor.x() < page.to_widget(pymupdf.Rect(edge, edge)).x()
        viewport = win.scroll.viewport()
        viewport_rect = QRect(viewport.mapToGlobal(QPoint()), viewport.size())
        editor_rect = QRect(editor.mapToGlobal(QPoint()), editor.size())
        assert editor_rect.left() >= viewport_rect.left() and editor_rect.right() <= viewport_rect.right()
        editor.finish(False)
        settle(app)
        hover_point = page.to_widget(note.rect).center()
        page.hover_at(hover_point, page.mapToGlobal(hover_point))
        assert page.hover_note.width() == note_text_width(page.hover_note.fontMetrics(), "노란 메모")
        assert page.hover_note.x() + page.hover_note.width() <= viewport_rect.right() + 1
        note.set_info(content="짧은 줄\n이 줄은 더 긴 메모")
        page.start_note(pymupdf.Point(100, 100), note.xref)
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        assert editor.toPlainText() == "짧은 줄\n이 줄은 더 긴 메모"
        assert editor.document().blockCount() == 2
        assert editor.height() >= editor.document().size().height()
        assert editor.verticalScrollBar().maximum() == 0
        editor.finish(False)
        settle(app)
        page.hover_at(hover_point, page.mapToGlobal(hover_point))
        assert page.hover_note.width() == note_text_width(page.hover_note.fontMetrics(), "이 줄은 더 긴 메모")
        wrapped_note = "긴 메모 " * 50
        note.set_info(content=wrapped_note)
        page.start_note(pymupdf.Point(100, 100), note.xref)
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        assert editor.width() < note_text_width(editor.fontMetrics(), wrapped_note)
        assert editor.height() >= editor.document().size().height()
        assert editor.verticalScrollBar().maximum() == 0
        editor.finish(False)
        settle(app)
        edge_note = win.doc[0].add_text_annot((page.page.rect.width - 10, 100), "가장자리")
        edge_point = page.to_widget(edge_note.rect).center()
        page.hover_at(edge_point, page.mapToGlobal(edge_point))
        edge_anchor = page.mapToGlobal(page.to_widget(edge_note.rect).topRight().toPoint())
        assert page.hover_note.x() < edge_anchor.x()
        assert page.hover_note.x() + page.hover_note.width() <= viewport_rect.right() + 1
        note.set_info(content="긴 메모 " * 100)
        page.hover_at(hover_point, page.mapToGlobal(hover_point))
        assert page.hover_note.width() <= viewport_rect.width() - 24
        assert page.hover_note.x() >= viewport_rect.left()
        assert page.hover_note.x() + page.hover_note.width() <= viewport_rect.right() + 1
        page.hover_at(QPointF(0, 0), QPointF(0, 0))

        win.resize(700, 768)
        settle(app)
        win.fit_width()
        settle(app)
        page.start_note(pymupdf.Point(page.page.rect.width - 10, 300))
        editor = next(ed for ed in page.findChildren(InlineEditor) if not ed.done)
        blank_height = editor.height()
        editor.setPlainText("긴 메모 " * 100)
        viewport_rect = QRect(viewport.mapToGlobal(QPoint()), viewport.size())
        editor_rect = QRect(editor.mapToGlobal(QPoint()), editor.size())
        assert editor.width() < note_text_width(editor.fontMetrics(), editor.toPlainText())
        assert editor.height() > blank_height
        assert editor_rect.left() >= viewport_rect.left()
        assert editor_rect.right() <= viewport_rect.right()
        editor.finish(False)
        settle(app)

        single = Path(directory) / "single.pdf"
        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        doc.save(single)
        doc.close()
        win.open(str(single))
        settle(app)
        assert not win.search_bar.isVisible() and not win.search_edit.text() and not win.search_results
        win.scroll.verticalScrollBar().setValue(win.scroll.verticalScrollBar().maximum())
        settle(app)
        assert not win.scroll_page_indicator.isVisible()
        win.open(str(path))
        settle(app)

        # 마지막 선택은 새 창에서도 복원된다.
        win.apply_theme("라이트")
        win.set_tool("pen")
        win.width_spin.setValue(7)
        win.set_color(PRESETS["pen"][3])
        win.set_tool("text")
        win.set_color(PRESETS["text"][2])
        win.close()
        win = Win(str(path))
        win.show()
        settle(app)
        assert win.theme_name == "라이트"
        assert (win.width, win.hl_width, win.font_size) == (7, 24, 26)
        assert win.colors == {"pen": PRESETS["pen"][3], "hl": PRESETS["hl"][1],
                              "text": PRESETS["text"][2]}
        win.set_tool("hl")
        assert win.width_spin.value() == 24
        win.set_tool("pen")
        assert win.width_spin.value() == 7
        win.close()

        # 저장된 값이 없는 최초 실행은 다크로 시작한다.
        settings.remove("theme")
        settings.setValue("tools/pen/width", 999)
        settings.setValue("tools/hl/width", "broken")
        settings.setValue("tools/text/font_size", 2)
        settings.setValue("tools/text/color", "not-a-color")
        win = Win(str(path))
        assert win.theme_name == "다크"
        assert (win.width, win.hl_width, win.font_size) == (2, 20, 20)
        assert win.colors["text"] == "#2864c6"
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
