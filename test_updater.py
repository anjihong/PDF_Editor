"""Offline updater checks; replacement tests use only disposable Windows files."""
import ctypes
import hashlib
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QPoint, QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

import updater
from pdf_editor import InlineEditor, Win


def rejects(function, *args):
    try:
        function(*args)
    except ValueError:
        return
    raise AssertionError("Invalid input was accepted")


def release(payload=b"test", tag="v1.3.0"):
    return dict(tag_name=tag, draft=False, prerelease=False, assets=[dict(
        name="PdfEditor.exe", size=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        browser_download_url=f"{updater.RELEASES_URL}/download/{tag}/PdfEditor.exe")])


class Reply:
    def __init__(self, data=b"", error=QNetworkReply.NoError, finished=True):
        self.data, self.code, self.done = data, error, finished
        self.on_abort = lambda: None

    def readAll(self):
        data, self.data = self.data, b""
        return data

    def error(self):
        return self.code

    def errorString(self):
        return "network failure"

    def attribute(self, _attribute):
        return 200

    def isFinished(self):
        return self.done

    def deleteLater(self):
        pass

    def abort(self):
        self.done, self.code = True, QNetworkReply.OperationCanceledError
        self.on_abort()


def pump(app, condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert condition(), "Timed out"


def test_metadata(root):
    assert updater.version_tuple("v1.10.0") > updater.version_tuple("1.9.9")
    for value in (None, "v1.2", "1.2.3-beta", "1.2.3/other"):
        rejects(updater.version_tuple, value)
    for tag in ("v1.1.0", "v1.2.0"):
        assert updater.release_asset(release(tag=tag)) is None
    assert updater.release_asset(dict(release(), prerelease=True)) is None
    assert updater.release_asset(dict(release(), draft=True)) is None
    for value in ([], {}, dict(release(), assets=[])):
        rejects(updater.release_asset, value)
    for key, value in (("digest", None), ("digest", "sha256:bad"), ("size", -1), ("size", True),
                       ("browser_download_url", "https://example.com/PdfEditor.exe")):
        bad = release()
        bad["assets"][0][key] = value
        rejects(updater.release_asset, bad)
    asset = updater.release_asset(release())
    path = root / "download.exe"
    path.write_bytes(b"test")
    updater.verify_download(path, asset)
    path.write_bytes(b"fail")
    rejects(updater.verify_download, path, asset)
    path.write_bytes(b"short")
    rejects(updater.verify_download, path, asset)
    assert updater.allowed_redirect(asset["url"], asset["url"])
    assert updater.allowed_redirect("https://release-assets.githubusercontent.com/x?token=1", asset["url"])
    for url in ("http://release-assets.githubusercontent.com/x", "https://github.com/other/repo/x",
                "https://release-assets.githubusercontent.com.evil.test/x", "https://user@objects.githubusercontent.com/x"):
        assert not updater.allowed_redirect(url, asset["url"])


def test_controller(app, root):
    window = QMainWindow()
    control = updater.Updater(window)
    assert not control.enabled and control.reply is None
    errors, starts = [], []
    control.error = lambda message, show=True: errors.append((message, show))
    control.start_helper = lambda: starts.append(True)
    for manual in (False, True):
        control.manual = manual
        control.metadata = bytearray()
        control.reply = Reply(error=QNetworkReply.TimeoutError)
        control.checked()
        assert errors[-1][1] == manual and control.reply is None
    for content in (b"bad json", b"[]", b"{}"):
        control.metadata = bytearray()
        control.reply = Reply(content)
        control.checked()
        assert errors[-1][1]
    control.metadata = bytearray()
    control.reply = Reply(b"x" * (1024 * 1024 + 1), finished=False)
    control.reply.on_abort = control.checked
    control.read_metadata()
    assert control.reply is None and "너무" in errors[-1][0]

    for mode in ("ok", "cancel", "network", "hash", "size", "oversize", "write", "helper_failure"):
        control.cancelled, control.failure = False, ""
        control.stage = Path(tempfile.mkdtemp(dir=root))
        staged = control.stage
        control.stream = (staged / "new.exe").open("wb")
        control.asset = updater.release_asset(release())
        data = b"fail" if mode == "hash" else b"longer" if mode == "oversize" else b"tes" if mode == "size" else b"test"
        control.reply = Reply(data, QNetworkReply.TimeoutError if mode == "network" else QNetworkReply.NoError, mode != "oversize")
        control.reply.on_abort = control.downloaded
        old_errors = len(errors)
        if mode == "cancel":
            control.cancel()
        elif mode == "write":
            control.stream.close()
            control.downloaded()
        elif mode == "oversize":
            control.read_download()
        elif mode == "helper_failure":
            with patch.object(control, "start_helper", side_effect=OSError("helper blocked")):
                control.downloaded()
        else:
            control.downloaded()
        assert control.reply is None
        if mode == "ok":
            assert starts == [True]
            assert staged.joinpath("new.exe").read_bytes() == b"test"
            control.cleanup()
        else:
            assert not staged.exists()
            assert len(errors) == old_errors + (mode != "cancel")
    with patch.object(updater.tempfile, "mkdtemp", side_effect=PermissionError("read-only folder")):
        control.download()
    assert control.stage is None and "폴더" in errors[-1][0]
    control.enabled = True
    control.stage = root  # An active operation cannot start a second request.
    with patch.object(control, "request", side_effect=AssertionError("duplicate request")):
        control.check()
    control.stage = None
    window.close()


def test_save_gate(app, root):
    pdf = root / "문서 원본.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    window = Win(str(pdf))
    window.updater = control = updater.Updater(window)
    control.installing = True
    control.stage = root / "save-gate"
    control.stage.mkdir()
    class Helper:
        @staticmethod
        def poll():
            return None
    control.helper = Helper()
    failures = []
    with patch.object(window.undo, "isClean", return_value=False), patch.object(QMessageBox, "critical", side_effect=lambda *a: failures.append(a)):
        for result in (False, OSError("disk full")):
            event = QCloseEvent()
            with patch.object(window, "save", **({"side_effect": result} if isinstance(result, Exception) else {"return_value": result})):
                window.closeEvent(event)
            assert not event.isAccepted() and not (control.stage / "approved").exists()
    assert failures
    with patch.object(Helper, "poll", return_value=1), patch.object(control, "error"):
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted() and not (control.stage / "approved").exists()
    # Existing close behavior commits inline edits before saving and authorizing.
    order = []
    editor = InlineEditor(window.pages[0], QPoint(10, 10), "pending", 12, "", lambda text: order.append("inline"))
    original_save = window.save
    def save():
        order.append("save")
        return original_save()
    with patch.object(window.undo, "isClean", return_value=False), patch.object(window, "save", side_effect=save):
        event = QCloseEvent()
        window.closeEvent(event)
    assert event.isAccepted() and control.authorized
    assert order == ["inline", "save"] and (control.stage / "approved").exists()
    window.updater = None
    window.close()


def test_windows_helper(app, root):
    if sys.platform != "win32":
        return
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    source = root / "Stub.cs"
    source.write_text('using System; using System.IO; class Stub { static void Main(string[] args) { '
                      'File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "launched.txt"), '
                      'String.Join("|", args) + "|" + Environment.GetEnvironmentVariable("PYINSTALLER_RESET_ENVIRONMENT")); } }')
    stub = root / "stub.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", f"/out:{stub}", str(source)], check=True, capture_output=True)
    script = Path(updater.__file__).with_name("updater.ps1").read_text(encoding="utf-8")
    # Suppress only the failure dialog; execute the unmodified replacement script.
    wrapper = root / "wrapper.ps1"
    wrapper.write_text('param($Script, $Manifest)\nAdd-Type -TypeDefinition @"\n'
                       'namespace System.Windows.Forms { public class MessageBox { public static void Show(string text, string title) {} } }\n'
                       '"@\nfunction Add-Type { param($AssemblyName) }\n& $Script -Manifest $Manifest\nexit $LASTEXITCODE\n', encoding="utf-8-sig")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    for mode in ("cancel", "unapproved_exit", "success", "locked", "rollback", "tampered", "read_only"):
        folder = root / f"한글 공백 {mode}"
        folder.mkdir()
        target = folder / "PDF 편집기.exe"
        target.write_bytes(b"previous executable")
        stage = Path(tempfile.mkdtemp(prefix=".pdfeditor-update-", dir=folder))
        payload = stub.read_bytes() if mode != "rollback" else b"invalid executable"
        (stage / "new.exe").write_bytes(payload)
        (stage / "updater.ps1").write_text(script, encoding="utf-8-sig")
        # A disposable parent exits on stdin EOF, never by force.
        parent = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        config = dict(target=str(target), pid=parent.pid, pdf=str(folder / "문서 & notes.pdf"),
                      sha256=hashlib.sha256(payload).hexdigest(), size=len(payload), log=str(folder / "update.log"))
        (stage / "manifest.json").write_text(json.dumps(config), encoding="utf-8")
        if mode == "tampered":
            (stage / "new.exe").write_bytes(b"modified after download")
        if mode == "read_only":
            target.chmod(stat.S_IREAD)
        helper = subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-File", str(wrapper), "-Script", str(stage / "updater.ps1"), "-Manifest", str(stage / "manifest.json")],
                                  creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lock = None
        try:
            pump(app, lambda: (stage / "ready").exists() or helper.poll() is not None)
            assert helper.poll() is None, helper.communicate()
            if mode == "cancel":
                (stage / "cancelled").touch()
            elif mode != "unapproved_exit":
                if mode == "locked":
                    lock = kernel.CreateFileW(str(target), 0x80000000, 0, None, 3, 0, None)
                    assert lock != ctypes.c_void_p(-1).value
                (stage / "approved").touch()
            parent.stdin.close()
            parent.wait(timeout=10)
            if lock:
                time.sleep(0.7)
                assert helper.poll() is None and target.exists()
                kernel.CloseHandle(lock)
                lock = None
            pump(app, lambda: helper.poll() is not None)
            stdout, stderr = helper.communicate()
            assert helper.returncode == (1 if mode in ("rollback", "tampered", "read_only") else 0), (mode, stdout, stderr, (folder / "update.log").read_text(encoding="utf-8") if (folder / "update.log").exists() else "no log")
            assert not stage.exists(), (mode, (folder / "update.log").read_text(encoding="utf-8") if (folder / "update.log").exists() else "no log")
            if mode in ("success", "locked"):
                pump(app, lambda: (folder / "launched.txt").exists())
                assert target.read_bytes() == payload
                assert (folder / "launched.txt").read_text(encoding="utf-8") == config["pdf"] + "|1"
            else:
                assert target.read_bytes() == b"previous executable"
                assert not (folder / "launched.txt").exists()
            if mode == "rollback":
                assert (folder / "update.log").exists()
        finally:
            target.chmod(stat.S_IWRITE)
            if lock:
                kernel.CloseHandle(lock)
            if parent.poll() is None:
                parent.stdin.close()
                parent.wait(timeout=10)
            if helper.poll() is None:
                if stage.exists():
                    (stage / "cancelled").touch()
                helper.wait(timeout=65)


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory(prefix="pdfeditor-tests-") as directory:
        root = Path(directory)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        with patch.object(updater, "LOG_PATH", root / "update.log"):
            test_metadata(root)
            test_controller(app, root)
            test_save_gate(app, root)
            test_windows_helper(app, root)
    print("ok: updater validation, cancellation, save gate, Windows replacement and rollback")


if __name__ == "__main__":
    main()
