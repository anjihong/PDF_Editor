"""Offscreen checks for the single dark workspace and responsive controls."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pdf_editor import PRESETS, THEME, Win


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
        assert win.theme is THEME and win.current_page() == 0
        assert win.pages_dock.isVisible() and win.properties_dock.isVisible()

        win.resize(1024, 768)
        settle(app)
        assert win.pages[0].width() <= win.scroll.viewport().width()
        assert win.current_page() == 0
        for action in (win.open_act, win.save_act, *win.tool_group.actions()):
            assert win.tb.widgetForAction(action).isVisible(), action.text()

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
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
