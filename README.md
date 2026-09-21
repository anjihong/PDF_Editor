# ✍️ PDF 편집기

**PDF 위에 바로 쓰고, 표시하고, 메모하세요.** 펜·형광펜·텍스트·메모를 한곳에서 다루는 Windows용 데스크톱 앱입니다.

<p align="center">
  <a href="https://github.com/anjihong/PDF_Editor/releases/download/v1.0.0/PdfEditor.exe"><strong>⬇️ Windows 실행 파일 다운로드 (v1.0.0)</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/anjihong/PDF_Editor/releases/tag/v1.0.0">릴리즈 노트</a>
</p>

> [!TIP]
> 다운로드한 `PdfEditor.exe`를 실행한 뒤 **열기**를 누르거나 PDF를 창에 끌어다 놓으세요. Python 설치는 필요하지 않습니다.

## 주요 기능

| 도구 | 할 수 있는 일 |
| --- | --- |
| ✏️ 펜 | 자유롭게 그리기. 색과 굵기를 조절하고, 펜 모드에서 우클릭하는 동안 임시 지우개 사용 |
| 🖍️ 형광펜 | 글자 위를 지나가면 텍스트 줄에 맞춰 표시. 빈 공간에서는 반투명 자유 곡선으로 표시 |
| 🔤 텍스트 | 페이지에 바로 입력하고, 클릭해서 수정하거나 드래그해서 이동. 글자 크기 조절 |
| 🗒️ 메모 | 스티키 노트를 추가하거나 펜·형광펜 주석에 메모를 연결. 마우스를 올려 내용 확인 |
| 🧽 지우개 | 주석 위를 드래그해서 삭제. S펜 버튼을 누르는 동안에도 임시 지우개로 전환 |

페이지 썸네일, 확대·축소, 실행 취소·다시 실행을 지원합니다. PDF별로 마지막에 보던 페이지를 기억하고, 페이지 사이 여백이나 바깥 배경을 터치해 화면을 이동할 수 있습니다.

## 시작하기

1. [v1.0.0 릴리즈](https://github.com/anjihong/PDF_Editor/releases/tag/v1.0.0)에서 `PdfEditor.exe`를 다운로드합니다.
2. exe를 실행하고 **열기**(`Ctrl+O`)로 PDF를 선택합니다. PDF 파일을 창에 끌어다 놓아도 됩니다.
3. 도구 모음에서 펜·형광펜·텍스트·메모를 골라 편집하고 **저장**(`Ctrl+S`)합니다.

주석은 PDF 표준 형식으로 파일에 저장되어 다른 PDF 뷰어에서도 볼 수 있습니다. 변경 내용이 남아 있는 상태에서 앱을 닫으면 현재 파일에 자동 저장합니다. 저장에 실패하면 오류를 표시하고 창을 닫지 않습니다. 원본을 보존하려면 **다른 이름으로 저장**(`Ctrl+Shift+S`)을 사용하세요.

## 빠른 조작

| 키·동작 | 기능 |
| --- | --- |
| `1` · `2` · `3` · `4` · `5` | 펜 · 형광펜 · 텍스트 · 메모 · 지우개. 같은 키를 다시 누르면 선택 모드 |
| 우클릭 | 펜 모드에서는 누른 동안 지우개. 다른 도구에서는 도구 해제. 선택 모드에서는 편집 메뉴 |
| S펜 버튼 | 누른 동안 임시 지우개, 놓으면 이전 도구로 복귀 |
| `Ctrl+Enter` · `Esc` | 텍스트 입력 확정 · 취소 (`Enter`는 줄바꿈) |
| `Ctrl+Z` · `Ctrl+Y` | 실행 취소 · 다시 실행 |
| `Ctrl+휠` · `Ctrl++` · `Ctrl+-` | 확대 · 축소 |
| `Ctrl+O` · `Ctrl+S` · `Ctrl+Shift+S` | 열기 · 저장 · 다른 이름으로 저장 |

## 테마

상태 표시줄 오른쪽 **테마** 버튼에서 **기본**과 **파스텔**을 바꿀 수 있습니다. 마지막에 선택한 테마를 기억합니다.

## 개발자용

Python 환경에서 실행하려면:

```powershell
pip install -r requirements.txt
python pdf_editor.py [파일.pdf]
```

Windows 실행 파일을 직접 빌드하려면 프로젝트 루트에서 `build.bat`을 실행하세요. 가상 환경 생성, 의존성 설치, PyInstaller 빌드 후 `dist\PdfEditor.exe`가 만들어집니다.

테스트는 `python test_core.py`, `python test_highlight.py`, `python test_input.py`, `python test_page_restore.py`로 실행합니다.

## 사용한 자원

- 앱: [PySide6](https://pypi.org/project/PySide6/), [PyMuPDF](https://pypi.org/project/PyMuPDF/)
- 파스텔 테마 글꼴: [Galmuri11](https://github.com/quiple/galmuri) — SIL OFL 1.1 ([라이선스](assets/fonts/OFL.md))
- 파스텔 테마 아이콘: [pixelarticons](https://github.com/halfmage/pixelarticons) — MIT ([라이선스](assets/icons/LICENSE))
