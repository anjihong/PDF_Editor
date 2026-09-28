"""GitHub Releases updater for the packaged Windows application."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QMessageBox, QProgressDialog

APP_VERSION = "1.1.1"
REPOSITORY = "anjihong/PDF_Editor"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
LOG_PATH = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "PdfEditor" / "update.log"


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise ValueError("올바르지 않은 버전입니다.")
    return tuple(int(part) for part in value.removeprefix("v").split("."))


def release_asset(data, current=APP_VERSION):
    if not isinstance(data, dict):
        raise ValueError("올바르지 않은 릴리즈 응답입니다.")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if version_tuple(tag) <= version_tuple(current):
        return None
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("릴리즈 파일 목록이 없습니다.")
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == "PdfEditor.exe"]
    if len(matches) != 1:
        raise ValueError("릴리즈에 PdfEditor.exe 파일이 없습니다.")
    asset = matches[0]
    expected = f"{RELEASES_URL}/download/{tag}/PdfEditor.exe"
    digest = asset.get("digest")
    size = asset.get("size")
    if asset.get("browser_download_url") != expected:
        raise ValueError("허용되지 않은 다운로드 경로입니다.")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("릴리즈 파일의 SHA-256 검증값이 없습니다.")
    if type(size) is not int or not 0 < size <= 512 * 1024 * 1024:
        raise ValueError("릴리즈 파일 크기가 올바르지 않습니다.")
    return dict(version=tag, url=expected, size=size, sha256=digest[7:].lower())


def allowed_redirect(url, asset_url=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    if asset_url:
        return url == asset_url or parsed.hostname in ("release-assets.githubusercontent.com", "objects.githubusercontent.com")
    return url == LATEST_URL


def verify_download(path, asset):
    if path.stat().st_size != asset["size"]:
        raise ValueError("다운로드 파일 크기가 일치하지 않습니다.")
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != asset["sha256"]:
        raise ValueError("다운로드 파일의 SHA-256이 일치하지 않습니다.")


def log_error(message):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        logging.error("Update: %s", message)


class Updater(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.enabled = sys.platform == "win32" and getattr(sys, "frozen", False)
        self.network = QNetworkAccessManager(self)
        self.reply = self.progress = self.stream = self.stage = self.helper = None
        self.asset = None
        self.manual = self.installing = self.authorized = self.cancelled = False
        self.failure = ""
        self.poll = QTimer(self, interval=100)
        self.poll.timeout.connect(self.helper_ready)
        self.action = QAction("업데이트 확인", window)
        self.action.triggered.connect(lambda: self.check(manual=True))
        window.menuBar().addMenu("도움말").addAction(self.action)
        if self.enabled:
            QTimer.singleShot(1000, self.check)

    def error(self, message, show=True):
        log_error(message)
        if show:
            box = QMessageBox(QMessageBox.Warning, "업데이트 실패", message, parent=self.window)
            link = box.addButton("릴리즈 페이지", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if box.clickedButton() == link:
                QDesktopServices.openUrl(QUrl(RELEASES_URL))

    def request(self, url, finished):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"PdfEditor/{APP_VERSION}".encode())
        request.setRawHeader(b"Accept", b"application/vnd.github+json" if url == LATEST_URL else b"application/octet-stream")
        request.setTransferTimeout(30000)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.UserVerifiedRedirectPolicy)
        self.reply = self.network.get(request)
        self.reply.redirected.connect(self.redirect)
        self.reply.finished.connect(finished)

    def redirect(self, url):
        try:
            allowed = allowed_redirect(url.toString(), self.asset["url"] if self.asset else None)
        except ValueError:
            allowed = False
        if allowed:
            self.reply.redirectAllowed.emit()
        else:
            self.failure = "허용되지 않은 다운로드 리디렉션입니다."
            self.reply.abort()

    def check(self, manual=False):
        if not self.enabled:
            if manual:
                QMessageBox.information(self.window, "업데이트 확인", f"현재 버전: {APP_VERSION}\n자동 업데이트는 Windows EXE에서만 사용할 수 있습니다.")
            return
        if self.reply or self.stage or self.helper:
            return
        self.manual, self.failure, self.asset, self.cancelled = manual, "", None, False
        self.action.setEnabled(False)
        self.metadata = bytearray()
        self.request(LATEST_URL, self.checked)
        self.reply.readyRead.connect(self.read_metadata)

    def read_metadata(self):
        self.metadata.extend(bytes(self.reply.readAll()))
        if len(self.metadata) > 1024 * 1024:
            self.failure = "릴리즈 응답이 너무 큽니다."
            if not self.reply.isFinished():
                self.reply.abort()

    def finish_reply(self):
        reply, self.reply = self.reply, None
        message = self.failure
        if not message and reply.error() != QNetworkReply.NoError:
            message = reply.errorString()
        if not message and reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) != 200:
            message = "서버가 정상 응답을 반환하지 않았습니다."
        reply.deleteLater()
        self.action.setEnabled(True)
        return message

    def checked(self):
        self.read_metadata()
        message = self.finish_reply()
        if self.cancelled:
            return
        try:
            if message:
                raise ValueError(message)
            self.asset = release_asset(json.loads(self.metadata))
        except (ValueError, TypeError) as exc:
            self.error(str(exc), self.manual)
            return
        if not self.asset:
            if self.manual:
                QMessageBox.information(self.window, "업데이트 확인", f"최신 버전입니다. ({APP_VERSION})")
            return
        box = QMessageBox(QMessageBox.Information, "새 버전 사용 가능",
                          f"현재 버전: {APP_VERSION}\n새 버전: {self.asset['version']}\n\n업데이트 후 문서를 저장하고 앱을 다시 시작합니다.", parent=self.window)
        install = box.addButton("지금 업데이트", QMessageBox.AcceptRole)
        later = box.addButton("나중에", QMessageBox.RejectRole)
        box.setDefaultButton(later)
        box.exec()
        if box.clickedButton() == install:
            self.download()

    def download(self):
        self.cancelled, self.failure = False, ""
        try:
            self.stage = Path(tempfile.mkdtemp(prefix=".pdfeditor-update-", dir=Path(sys.executable).parent))
            self.stream = (self.stage / "new.exe").open("wb")
        except OSError as exc:
            self.cleanup()
            self.error(f"EXE 폴더에 파일을 쓸 수 없습니다. 쓰기 가능한 폴더로 옮긴 뒤 다시 시도하세요.\n{exc}")
            return
        self.action.setEnabled(False)
        self.progress = QProgressDialog("업데이트 다운로드 중…", "취소", 0, 100, self.window)
        self.progress.setWindowTitle("업데이트")
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.canceled.connect(self.cancel)
        self.progress.show()
        self.request(self.asset["url"], self.downloaded)
        self.reply.readyRead.connect(self.read_download)
        self.reply.downloadProgress.connect(lambda received, total: self.progress.setValue(min(99, received * 100 // self.asset["size"])) if self.progress else None)

    def read_download(self):
        try:
            chunk = bytes(self.reply.readAll())
            if self.stream.tell() + len(chunk) > self.asset["size"]:
                raise ValueError("다운로드 파일이 예상 크기를 초과했습니다.")
            self.stream.write(chunk)
        except (OSError, ValueError) as exc:
            self.failure = str(exc)
            if not self.reply.isFinished():
                self.reply.abort()

    def downloaded(self):
        # Cancellation and write failures may deliver finished synchronously from abort().
        if not self.cancelled and not self.failure:
            self.read_download()
        message = self.finish_reply()
        try:
            self.stream.close()
            self.stream = None
            if self.cancelled:
                self.cleanup()
                return
            if message:
                raise ValueError(message)
            verify_download(self.stage / "new.exe", self.asset)
            self.start_helper()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.cancel()
            self.error(str(exc))

    def start_helper(self):
        source = Path(__file__).with_name("updater.ps1")
        # Windows PowerShell 5.1 requires a BOM for non-ASCII script text.
        (self.stage / "updater.ps1").write_text(source.read_text(encoding="utf-8"), encoding="utf-8-sig")
        manifest = dict(target=str(Path(sys.executable).resolve()), pid=os.getpid(),
                        pdf=str(Path(self.window.path).resolve()) if self.window.path else "",
                        sha256=self.asset["sha256"], size=self.asset["size"], log=str(LOG_PATH))
        (self.stage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.helper = subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                        "-File", str(self.stage / "updater.ps1"), "-Manifest", str(self.stage / "manifest.json")],
                                       creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(self.stage.parent),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.deadline = time.monotonic() + 15
        self.progress.setLabelText("업데이트 준비 중…")
        self.poll.start()

    def helper_ready(self):
        if self.cancelled:
            if self.helper.poll() is not None:
                self.helper = None
                self.poll.stop()
                self.cleanup()
            return
        if self.helper.poll() is not None or time.monotonic() > self.deadline:
            self.cancel()
            self.error("업데이트 보조 프로세스를 준비하지 못했습니다. 기존 앱을 유지합니다.")
        elif (self.stage / "ready").exists():
            self.poll.stop()
            self.installing = True
            self.progress.setCancelButton(None)
            self.progress.hide()
            if not self.window.close():
                self.installing = False
                self.cancel()

    def before_close(self):
        """Called only after the editor has successfully saved the document."""
        if self.installing:
            try:
                if self.helper.poll() is not None:
                    raise OSError("업데이트 보조 프로세스가 종료되었습니다.")
                (self.stage / "approved").touch()
                self.authorized = True
            except OSError as exc:
                self.error(str(exc))
                return False
        else:
            self.cancel()
        return True

    def cancel(self):
        if self.authorized:
            return
        self.cancelled = True
        if self.reply:
            self.reply.abort()
        elif self.helper:
            try:
                (self.stage / "cancelled").touch()
            except OSError as exc:
                log_error(str(exc))
            self.poll.start()
        else:
            self.cleanup()

    def cleanup(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        if self.progress:
            self.progress.deleteLater()
            self.progress = None
        if self.stage:
            shutil.rmtree(self.stage, ignore_errors=True)
            self.stage = None
        self.action.setEnabled(True)
