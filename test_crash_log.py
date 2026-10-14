"""Exercise exception and native-crash logging in isolated child processes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap


def run_child(directory, source):
    env = dict(os.environ, LOCALAPPDATA=str(directory), QT_QPA_PLATFORM="offscreen",
               PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", textwrap.dedent(source)], env=env,
                          cwd=Path(__file__).parent, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=30)


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run_child(root, """
            import sys
            sys.stderr = None  # PyInstaller --windowed has no console.
            from crash_log import install_crash_logging
            install_crash_logging()
            raise ValueError('uncaught 한글 -24')
        """)
        assert result.returncode != 0
        path = root / "PdfEditor" / "crash.log"
        first_log = path.read_text(encoding="utf-8")
        assert "Unhandled exception" in first_log and "uncaught 한글 -24" in first_log

        result = run_child(root, """
            from crash_log import install_crash_logging
            install_crash_logging()
            import pymupdf
            import pdf_editor
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QUndoStack
            app = QApplication([])
            class Win:
                doc = pymupdf.open()
            win = Win()
            page = win.doc.new_page()
            annot = page.add_freetext_annot((10, 10, 200, 50), 'old')
            def fail(*args):
                int('commit 한글 -24')
            commands = [pdf_editor.AddAnnot(win, 0, fail, 'text'),
                        pdf_editor.SetContent(win, 0, annot.xref, 'new')]
            pdf_editor.set_content = fail
            for command in commands:
                stack = QUndoStack()
                try:
                    stack.push(command)
                except ValueError as exc:
                    assert 'commit 한글 -24' in str(exc)
                else:
                    raise AssertionError('Command error was swallowed')
            print('survived')
        """)
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        logged = path.read_text(encoding="utf-8")
        assert logged.startswith(first_log), "Restart must retain the preceding crash"
        assert "Failed: AddAnnot.redo" in logged and "Failed: SetContent.redo" in logged
        assert "commit 한글 -24" in logged

        result = run_child(root, """
            import ctypes, faulthandler, sys
            if sys.platform == 'win32':
                ctypes.windll.kernel32.SetErrorMode(0x8003)
            from crash_log import install_crash_logging
            install_crash_logging()
            faulthandler._sigsegv()
        """)
        assert result.returncode != 0
        logged = path.read_text(encoding="utf-8")
        assert "Windows fatal exception" in logged or "Fatal Python error" in logged
        assert "most recent call first" in logged
    print("ok")


if __name__ == "__main__":
    main()
