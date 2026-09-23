"""Offscreen checks for dark/light themes and responsive controls."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from pdf_editor import PRESETS, THEMES, Win


def settle(app):
    for _ in range(12):
        app.processEvents()


def main():
    with tempfile.TemporaryDirectory() as directory:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        settings = QSettings("pdf-editor", "PdfEditor")
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
        win.close()
        win = Win(str(path))
        win.show()
        settle(app)
        assert win.theme_name == "라이트"
        win.close()

        # 저장된 값이 없는 최초 실행은 다크로 시작한다.
        settings.remove("theme")
        win = Win(str(path))
        assert win.theme_name == "다크"
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
