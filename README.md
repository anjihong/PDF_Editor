# PDF 편집기

PDF에 펜, 형광펜, 텍스트, 메모 주석을 다는 데스크톱 앱. PySide6 + PyMuPDF.

주석은 PDF 표준 annotation으로 원본 파일에 직접 저장되므로 다른 PDF 뷰어에서도 보인다.

변경한 PDF는 앱을 종료할 때 현재 파일에 자동 저장된다. 저장에 실패하면 오류를 표시하고 창을 닫지 않는다.

## 기능

- **펜** — 자유 곡선 그리기 (색, 굵기 조절). 펜 도구에서 우클릭하는 동안 임시 지우개
- **S펜 버튼** — 어떤 도구를 선택했든 누른 동안 임시 지우개로 전환하고, 놓으면 이전 도구로 복귀
- **형광펜** — 드래그한 영역의 글자 줄에 맞춰 칠함
- **텍스트** — 페이지 위에 바로 입력. 클릭하면 편집, 드래그하면 이동. 글자 크기 조절
- **메모** — 스티키 노트. 형광펜·펜 주석에도 메모를 붙일 수 있고, 마우스를 올리면 내용이 뜸
- **지우개** — 드래그로 지나간 주석 삭제
- 실행 취소 / 다시 실행, 확대·축소, 페이지 썸네일 목록, 드래그 앤 드롭으로 열기
- PDF를 다시 열 때 파일별로 마지막에 보던 페이지 복원
- PDF 바깥 배경과 페이지 사이 여백에서 한 손가락으로 화면 이동

## 실행

```bash
pip install -r requirements.txt
python pdf_editor.py [파일.pdf]
```

## exe 빌드 (Windows)

`build.bat` 실행. `.venv` 생성, 의존성 설치, PyInstaller 빌드까지 한 번에 하고 `dist\PdfEditor.exe`가 만들어진다.

## 테마

상태바 오른쪽 **테마** 버튼으로 기본 / 파스텔 전환 (마지막 선택 기억). 파스텔 테마 자원은 `assets/`:

- 글꼴 [Galmuri11](https://github.com/quiple/galmuri) — SIL OFL 1.1 (`assets/fonts/OFL.md`)
- 아이콘 [pixelarticons](https://github.com/halfmage/pixelarticons) — MIT (`assets/icons/LICENSE`)

## 단축키

| 키 | 동작 |
|---|---|
| `1`~`5` | 펜 / 형광펜 / 텍스트 / 메모 / 지우개 (다시 누르면 선택 모드) |
| 우클릭 | 펜 모드에선 누른 동안 지우개, 다른 도구에선 도구 해제, 선택 모드에선 메뉴(메모·텍스트 추가, 편집, 삭제) |
| `Ctrl+Enter` / `Esc` | 입력 확정 / 취소 (`Enter`는 줄바꿈) |
| `Ctrl+Z` / `Ctrl+Y` | 실행 취소 / 다시 실행 |
| `Ctrl+휠`, `Ctrl++` / `Ctrl+-` | 확대 / 축소 |
| `Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S` | 열기 / 저장 / 다른 이름으로 저장 |

## 테스트

주석 헬퍼와 오프스크린 GUI 입력을 검증한다.

```bash
python test_core.py
python test_input.py
python test_page_restore.py
```
