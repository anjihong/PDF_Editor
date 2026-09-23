"""Offscreen checks for dark/light themes and responsive controls."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QSettings
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
        for _ in range(3):
            doc.new_page(width=595, height=842)
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

        win.resize(1024, 768)
        settle(app)
        assert win.pages[0].width() <= win.scroll.viewport().width()
        assert win.current_page() == 0
        for action in (win.open_act, win.save_act, *win.tool_group.actions()):
            assert win.tb.widgetForAction(action).isVisible(), action.text()

        win.goto_page(1)
        settle(app)
        indicator = win.scroll_page_indicator
        assert indicator.isVisible() and indicator.text() == "2"
        bar = win.scroll.verticalScrollBar()
        option = QStyleOptionSlider()
        bar.initStyleOption(option)
        handle = bar.style().subControlRect(
            QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
        handle_left = bar.mapTo(win.scroll.viewport(), handle.topLeft()).x()
        assert 0 <= indicator.x() and indicator.geometry().right() < handle_left
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
