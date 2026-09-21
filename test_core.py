"""GUI 없이 PyMuPDF 헬퍼 자체 검증. 실행: python test_core.py"""
import tempfile
from pathlib import Path

import pymupdf

from pdf_editor import (ASSETS, HIDDEN, TOOLS, line_quads, make_highlight, make_ink, make_note, make_text,
                        purge_hidden, set_content, set_hidden, text_rect)


def main():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "안녕하세요 hello 123", fontname="korea", fontsize=14)
    page.insert_text((72, 130), "둘째 줄 second line", fontname="korea", fontsize=14)
    blank = page.get_pixmap().samples

    # 형광펜: 두 줄에 걸친 드래그 -> 줄당 quad 1개
    quads = line_quads(page, pymupdf.Rect(80, 85, 150, 135))
    assert len(quads) == 2, quads
    hl = make_highlight(page, quads, (1, 1, 0))
    assert hl.type[0] == pymupdf.PDF_ANNOT_HIGHLIGHT

    # 펜
    ink = make_ink(page, [pymupdf.Point(10, 10), pymupdf.Point(50, 60)], (1, 0, 0), 2)
    assert ink.type[0] == pymupdf.PDF_ANNOT_INK

    # 텍스트 (한글/영어/숫자, 파란색)
    txt = make_text(page, pymupdf.Point(72, 200), "메모 text 42", (0, 0, 1))
    assert txt.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT
    r = txt.rect
    pix = page.get_pixmap(clip=r)
    assert any(pix.pixel(x, y)[2] > 200 > pix.pixel(x, y)[0] for x in range(pix.width) for y in range(pix.height)), \
        "FreeText 파란 픽셀 없음"

    # 텍스트 편집: 내용 바꾸고 렉트 재계산, 여전히 렌더링됨
    new = "수정됨 edited\n둘째 줄"
    set_content(page, txt.xref, new, text_rect(r.top_left, new))
    a = page.load_annot(txt.xref)
    assert a.info["content"] == new and a.rect.height > r.height
    pix = page.get_pixmap(clip=a.rect)
    assert any(pix.pixel(x, y)[2] > 200 > pix.pixel(x, y)[0] for x in range(pix.width) for y in range(pix.height)), \
        "편집 후 FreeText 렌더링 안 됨"

    # 메모 (sticky note, 한글) + 형광펜에 메모 붙이기
    note = make_note(page, pymupdf.Point(300, 300), "메모 내용 한글")
    assert note.info["content"] == "메모 내용 한글"
    set_content(page, hl.xref, "형광펜 메모")
    assert page.load_annot(hl.xref).info["content"] == "형광펜 메모"

    # 숨기기 -> 렌더링에서 사라짐, 다시 보이기
    xrefs = [a.xref for a in page.annots()]
    for x in xrefs:
        set_hidden(page, x, True)
    assert page.get_pixmap().samples == blank, "숨긴 주석이 여전히 렌더링됨"
    assert all(page.load_annot(x).flags & HIDDEN for x in xrefs)
    set_hidden(page, hl.xref, False)
    assert page.get_pixmap().samples != blank

    # 저장/재오픈: 숨긴 것은 제거, 남은 것 내용 보존
    assert purge_hidden(doc) == len(xrefs) - 1
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.pdf"
        doc.save(p, garbage=3, deflate=True)
        doc2 = pymupdf.open(p)
        annots = list(doc2[0].annots())
        assert len(annots) == 1 and annots[0].info["content"] == "형광펜 메모", annots
        doc2.close()

    # 파스텔 테마 자원: 도구 아이콘 SVG와 글꼴이 assets/에 있어야 함
    for *_, icon in TOOLS:
        assert (ASSETS / "icons" / f"{icon}.svg").exists(), icon
    assert (ASSETS / "fonts" / "Galmuri11.ttf").exists()
    print("ok")


if __name__ == "__main__":
    main()
