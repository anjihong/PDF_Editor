"""Offscreen checks for remembering the last viewed page of each PDF."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

import pdf_editor
from pdf_editor import Win, page_settings_key


def create_pdf(path, pages):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=600, height=800)
    doc.save(path)
    doc.close()


def settle(app):
    for _ in range(5):
        app.processEvents()


def saved_page(path):
    return QSettings("pdf-editor", "PdfEditor").value(page_settings_key(path), type=int)


def main():
    with tempfile.TemporaryDirectory() as directory:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        app = QApplication([])
        first = Path(directory) / "first.pdf"
        second = Path(directory) / "second.pdf"
        copied = Path(directory) / "copied.pdf"
        create_pdf(first, 4)
        create_pdf(second, 4)

        win = Win(str(first))
        win.show()
        settle(app)
        assert win.current_page() == 0
        win.goto_page(2)
        settle(app)
        assert win.current_page() == 2 and saved_page(first) == 2
        win.close()

        persisted = subprocess.check_output([
            sys.executable, "-c",
            "import sys; from PySide6.QtCore import QSettings; "
            "QSettings.setDefaultFormat(QSettings.IniFormat); "
            "QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, sys.argv[1]); "
            "print(QSettings('pdf-editor', 'PdfEditor').value(sys.argv[2], type=int))",
            directory, page_settings_key(first),
        ], text=True)
        assert persisted.strip() == "2"

        # A new window reads persisted settings. Initial layout must not overwrite them.
        win = Win(str(first))
        assert saved_page(first) == 2
        win.show()
        settle(app)
        assert win.current_page() == 2 and win.page_spin.value() == 3

        original_dialog = pdf_editor.QFileDialog

        class OpenDialog:
            @staticmethod
            def getOpenFileName(*_args, **_kwargs):
                return str(second), "PDF (*.pdf)"

        pdf_editor.QFileDialog = OpenDialog
        try:
            win.open_dialog()
        finally:
            pdf_editor.QFileDialog = original_dialog
        settle(app)
        assert win.current_page() == 0
        win.goto_page(3)
        settle(app)
        assert saved_page(second) == 3 and saved_page(first) == 2

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(first))])
        drop = QDropEvent(QPointF(0, 0), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        win.dropEvent(drop)
        settle(app)
        assert win.current_page() == 2 and saved_page(second) == 3

        class SaveDialog:
            @staticmethod
            def getSaveFileName(*_args, **_kwargs):
                return str(copied), "PDF (*.pdf)"

        pdf_editor.QFileDialog = SaveDialog
        try:
            assert win.save_as()
        finally:
            pdf_editor.QFileDialog = original_dialog
        assert copied.exists() and saved_page(copied) == 2
        win.close()

        # Replacing a PDF with fewer pages clamps an older saved position.
        second.unlink()
        create_pdf(second, 2)
        win = Win(str(second))
        win.show()
        settle(app)
        assert win.current_page() == 1 and win.page_spin.value() == 2
        win.close()

        win = Win(str(copied))
        win.show()
        settle(app)
        assert win.current_page() == 2
        win.close()
    print("ok")


if __name__ == "__main__":
    main()
