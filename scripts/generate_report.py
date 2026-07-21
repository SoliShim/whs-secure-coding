"""강의 자료와 실제 구현 근거를 기반으로 제출용 한글 PDF 보고서를 생성한다.

개인정보는 명령행 인자로만 받고, 결과 PDF는 Git에서 제외된
``output/pdf`` 아래에 작성한다.
"""

from __future__ import annotations

import argparse
import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image as PillowImage
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
PAGE_WIDTH, PAGE_HEIGHT = A4
GREEN = colors.HexColor("#245E42")
GREEN_DARK = colors.HexColor("#123D2B")
GREEN_SOFT = colors.HexColor("#EAF3ED")
MINT = colors.HexColor("#CFE7D7")
INK = colors.HexColor("#18241D")
MUTED = colors.HexColor("#59655E")
LINE_COLOR = colors.HexColor("#D6DFD9")
CREAM = colors.HexColor("#F7F7F2")
WHITE = colors.white
RED_SOFT = colors.HexColor("#FCE9E7")
AMBER_SOFT = colors.HexColor("#FFF3D6")


@dataclass(frozen=True)
class ReportIdentity:
    class_name: str
    name: str
    phone_last4: str
    repo_url: str

    @property
    def filename(self) -> str:
        safe_name = re.sub(r"\s+", "", self.name)
        return f"[WHS][secure-coding][{self.class_name}]{safe_name}({self.phone_last4}).pdf"


def _font_path() -> Path:
    candidates = (
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        Path("/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("한글 글꼴을 찾지 못했습니다. Noto Sans CJK 또는 나눔고딕을 설치해 주세요.")


FONT_NAME = "ReportKorean"
pdfmetrics.registerFont(TTFont(FONT_NAME, str(_font_path())))


def _styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    base = ParagraphStyle(
        "BodyKorean",
        parent=sample["BodyText"],
        fontName=FONT_NAME,
        fontSize=9.2,
        leading=15.2,
        textColor=INK,
        alignment=TA_JUSTIFY,
        wordWrap="CJK",
        spaceAfter=5,
    )
    return {
        "CoverKicker": ParagraphStyle(
            "CoverKicker",
            parent=base,
            fontSize=12,
            leading=17,
            textColor=MINT,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "CoverTitle": ParagraphStyle(
            "CoverTitle",
            parent=base,
            fontSize=27,
            leading=37,
            textColor=WHITE,
            alignment=TA_LEFT,
            spaceAfter=16,
        ),
        "CoverSub": ParagraphStyle(
            "CoverSub",
            parent=base,
            fontSize=11,
            leading=18,
            textColor=colors.HexColor("#E7F3EB"),
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "CoverMeta": ParagraphStyle(
            "CoverMeta",
            parent=base,
            fontSize=10.5,
            leading=19,
            textColor=WHITE,
            alignment=TA_LEFT,
        ),
        "Title": ParagraphStyle(
            "Title",
            parent=base,
            fontSize=23,
            leading=31,
            textColor=GREEN_DARK,
            spaceBefore=3,
            spaceAfter=15,
        ),
        "Heading1": ParagraphStyle(
            "Heading1",
            parent=base,
            fontSize=18,
            leading=25,
            textColor=GREEN_DARK,
            spaceBefore=5,
            spaceAfter=12,
            keepWithNext=True,
        ),
        "Heading2": ParagraphStyle(
            "Heading2",
            parent=base,
            fontSize=13,
            leading=20,
            textColor=GREEN,
            spaceBefore=9,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "Heading3": ParagraphStyle(
            "Heading3",
            parent=base,
            fontSize=10.5,
            leading=16,
            textColor=GREEN_DARK,
            spaceBefore=7,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "Body": base,
        "Small": ParagraphStyle(
            "Small",
            parent=base,
            fontSize=7.7,
            leading=11.8,
            textColor=MUTED,
            alignment=TA_LEFT,
        ),
        "Bullet": ParagraphStyle(
            "Bullet",
            parent=base,
            leftIndent=11,
            firstLineIndent=-7,
            bulletIndent=0,
            spaceAfter=3,
        ),
        "Callout": ParagraphStyle(
            "Callout",
            parent=base,
            fontSize=9.4,
            leading=15.5,
            textColor=GREEN_DARK,
            backColor=GREEN_SOFT,
            borderColor=MINT,
            borderWidth=0.7,
            borderPadding=9,
            borderRadius=5,
            spaceBefore=5,
            spaceAfter=10,
        ),
        "Warning": ParagraphStyle(
            "Warning",
            parent=base,
            fontSize=9,
            leading=14.5,
            textColor=colors.HexColor("#6B3E0F"),
            backColor=AMBER_SOFT,
            borderColor=colors.HexColor("#E4BF69"),
            borderWidth=0.7,
            borderPadding=8,
            spaceBefore=5,
            spaceAfter=9,
        ),
        "Table": ParagraphStyle(
            "Table",
            parent=base,
            fontSize=7.2,
            leading=10.6,
            alignment=TA_LEFT,
            spaceAfter=0,
        ),
        "TableHead": ParagraphStyle(
            "TableHead",
            parent=base,
            fontSize=7.4,
            leading=10.8,
            textColor=WHITE,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "Caption": ParagraphStyle(
            "Caption",
            parent=base,
            fontSize=7.5,
            leading=11.5,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=9,
        ),
        "Code": ParagraphStyle(
            "Code",
            parent=base,
            fontName="Courier",
            fontSize=7.4,
            leading=11,
            textColor=INK,
            backColor=colors.HexColor("#F1F3F1"),
            borderColor=LINE_COLOR,
            borderWidth=0.5,
            borderPadding=8,
            leftIndent=0,
            spaceBefore=4,
            spaceAfter=8,
        ),
        "RightSmall": ParagraphStyle(
            "RightSmall",
            parent=base,
            fontSize=7.5,
            leading=11,
            textColor=MUTED,
            alignment=TA_RIGHT,
        ),
    }


STYLES = _styles()


def para(text: str, style: str = "Body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def bullets(items: Iterable[str]) -> list[Paragraph]:
    return [Paragraph(f"<bullet>-</bullet>{item}", STYLES["Bullet"]) for item in items]


def styled_table(
    rows: list[list[object]],
    widths: list[float],
    *,
    repeat_rows: int = 1,
    head: bool = True,
    font_size: float = 7.2,
) -> LongTable:
    rendered: list[list[object]] = []
    for row_index, row in enumerate(rows):
        style = "TableHead" if head and row_index == 0 else "Table"
        rendered.append([cell if hasattr(cell, "wrap") else para(esc(cell), style) for cell in row])
    table = LongTable(
        rendered,
        colWidths=widths,
        repeatRows=repeat_rows if head else 0,
        splitByRow=True,
        hAlign="LEFT",
    )
    commands: list[tuple] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE_COLOR),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("ROWBACKGROUNDS", (0, 1 if head else 0), (-1, -1), [WHITE, CREAM]),
    ]
    if head:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def two_column_cards(cards: list[tuple[str, str]]) -> Table:
    cells = []
    for title, body in cards:
        cells.append(
            [
                para(f"<b>{esc(title)}</b>", "Heading3"),
                para(body, "Small"),
            ]
        )
    rows = [cells[index : index + 2] for index in range(0, len(cells), 2)]
    if rows and len(rows[-1]) == 1:
        rows[-1].append("")
    table = Table(rows, colWidths=[84 * mm, 84 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.45, LINE_COLOR),
                ("INNERGRID", (0, 0), (-1, -1), 0.45, LINE_COLOR),
                ("BACKGROUND", (0, 0), (-1, -1), CREAM),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def architecture_diagram() -> Drawing:
    drawing = Drawing(170 * mm, 82 * mm)
    drawing.add(Rect(0, 0, 170 * mm, 82 * mm, rx=8, fillColor=CREAM, strokeColor=LINE_COLOR))

    def box(x: float, y: float, w: float, h: float, label: str, fill=WHITE) -> None:
        drawing.add(Rect(x, y, w, h, rx=5, fillColor=fill, strokeColor=GREEN, strokeWidth=0.8))
        drawing.add(
            String(
                x + w / 2,
                y + h / 2 - 3,
                label,
                fontName=FONT_NAME,
                fontSize=7.5,
                fillColor=INK,
                textAnchor="middle",
            )
        )

    box(7 * mm, 58 * mm, 31 * mm, 15 * mm, "브라우저", GREEN_SOFT)
    box(48 * mm, 58 * mm, 37 * mm, 15 * mm, "요청 크기 + Host")
    box(95 * mm, 58 * mm, 31 * mm, 15 * mm, "세션 + CSRF")
    box(136 * mm, 58 * mm, 27 * mm, 15 * mm, "보안 헤더")
    for left, right in ((38, 48), (85, 95), (126, 136)):
        drawing.add(Line(left * mm, 65.5 * mm, right * mm, 65.5 * mm, strokeColor=GREEN))

    labels = ("인증", "상품/검색", "채팅", "신고/관리", "가상 지갑")
    for index, label in enumerate(labels):
        box((7 + index * 32) * mm, 31 * mm, 27 * mm, 14 * mm, label, WHITE)
        drawing.add(
            Line(
                (20.5 + index * 32) * mm,
                58 * mm,
                (20.5 + index * 32) * mm,
                45 * mm,
                strokeColor=LINE_COLOR,
            )
        )

    box(34 * mm, 8 * mm, 45 * mm, 13 * mm, "SQLite WAL + 바인딩 SQL", GREEN_SOFT)
    box(91 * mm, 8 * mm, 45 * mm, 13 * mm, "검증 후 JPEG 저장", GREEN_SOFT)
    drawing.add(Line(85 * mm, 31 * mm, 57 * mm, 21 * mm, strokeColor=GREEN))
    drawing.add(Line(85 * mm, 31 * mm, 113 * mm, 21 * mm, strokeColor=GREEN))
    drawing.add(
        String(
            85 * mm,
            50 * mm,
            "FastAPI 단일 애플리케이션",
            fontName=FONT_NAME,
            fontSize=9,
            fillColor=GREEN_DARK,
            textAnchor="middle",
        )
    )
    return drawing


def transfer_diagram() -> Drawing:
    drawing = Drawing(170 * mm, 62 * mm)
    drawing.add(Rect(0, 0, 170 * mm, 62 * mm, rx=8, fillColor=CREAM, strokeColor=LINE_COLOR))
    stages = (
        ("세션/CSRF", "송신자 고정"),
        ("비밀번호", "Argon2id 재인증"),
        ("BEGIN IMMEDIATE", "쓰기 경쟁 직렬화"),
        ("조건부 차감", "잔액 부족 거부"),
        ("이체 + 원장", "2행, 합계 0"),
        ("COMMIT", "감사 이벤트"),
    )
    for index, (title, sub) in enumerate(stages):
        x = (4 + index * 28) * mm
        drawing.add(Rect(x, 19 * mm, 24 * mm, 25 * mm, rx=5, fillColor=WHITE, strokeColor=GREEN))
        drawing.add(
            String(
                x + 12 * mm,
                34 * mm,
                title,
                fontName=FONT_NAME,
                fontSize=6.8,
                fillColor=GREEN_DARK,
                textAnchor="middle",
            )
        )
        drawing.add(
            String(
                x + 12 * mm,
                26 * mm,
                sub,
                fontName=FONT_NAME,
                fontSize=5.9,
                fillColor=MUTED,
                textAnchor="middle",
            )
        )
        if index < len(stages) - 1:
            drawing.add(
                Line(
                    x + 24 * mm, 31.5 * mm, x + 28 * mm, 31.5 * mm, strokeColor=GREEN, strokeWidth=1
                )
            )
    drawing.add(
        String(
            85 * mm,
            8 * mm,
            "멱등 키는 송신자별로 재전송을 하나의 결과로 수렴시킨다.",
            fontName=FONT_NAME,
            fontSize=7,
            fillColor=MUTED,
            textAnchor="middle",
        )
    )
    return drawing


def screenshot(path: Path, max_width: float, max_height: float) -> Image | None:
    if not path.is_file():
        return None
    with PillowImage.open(path) as source:
        width, height = source.size
    scale = min(max_width / width, max_height / height)
    return Image(str(path), width=width * scale, height=height * scale)


class ReportDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str, identity: ReportIdentity, **kwargs):
        super().__init__(filename, **kwargs)
        self.identity = identity
        self._heading_id = 0
        self._have_heading1 = False
        frame = Frame(
            18 * mm,
            18 * mm,
            PAGE_WIDTH - 36 * mm,
            PAGE_HEIGHT - 36 * mm,
            id="normal",
            leftPadding=0,
            rightPadding=0,
            topPadding=9 * mm,
            bottomPadding=7 * mm,
        )
        self.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=self._draw_page)])

    def beforeDocument(self) -> None:
        """목차를 위한 multiBuild 각 pass에서 같은 bookmark 키를 사용한다."""

        self._heading_id = 0
        self._have_heading1 = False

    def _draw_page(self, canvas, doc) -> None:
        page_number = canvas.getPageNumber()
        canvas.saveState()
        if page_number == 1:
            canvas.setFillColor(GREEN_DARK)
            canvas.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)
            canvas.setFillColor(GREEN)
            canvas.circle(PAGE_WIDTH - 20 * mm, PAGE_HEIGHT - 20 * mm, 46 * mm, fill=1, stroke=0)
            canvas.setFillColor(colors.HexColor("#1B4D37"))
            canvas.circle(PAGE_WIDTH - 5 * mm, 18 * mm, 58 * mm, fill=1, stroke=0)
            canvas.setStrokeColor(MINT)
            canvas.setLineWidth(1)
            canvas.line(18 * mm, 31 * mm, PAGE_WIDTH - 18 * mm, 31 * mm)
        else:
            canvas.setStrokeColor(MINT)
            canvas.setLineWidth(1.2)
            canvas.line(18 * mm, PAGE_HEIGHT - 15 * mm, PAGE_WIDTH - 18 * mm, PAGE_HEIGHT - 15 * mm)
            canvas.setFillColor(GREEN_DARK)
            canvas.setFont(FONT_NAME, 7.2)
            canvas.drawString(
                18 * mm,
                PAGE_HEIGHT - 11.5 * mm,
                "Tiny Second-hand Shopping Platform - 보안개발 보고서",
            )
            canvas.setFillColor(MUTED)
            canvas.setFont(FONT_NAME, 7)
            canvas.drawRightString(
                PAGE_WIDTH - 18 * mm, 10.5 * mm, f"{page_number} / WHS secure-coding"
            )
            canvas.drawString(18 * mm, 10.5 * mm, "2026-07-22 기준")
        canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, Paragraph) and flowable.style.name in {"Heading1", "Heading2"}:
            if flowable.style.name == "Heading2" and not self._have_heading1:
                return
            if flowable.style.name == "Heading1":
                self._have_heading1 = True
            level = 0 if flowable.style.name == "Heading1" else 1
            self._heading_id += 1
            key = f"heading-{self._heading_id}"
            text = flowable.getPlainText()
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=False)
            self.notify("TOCEntry", (level, text, self.page, key))


def cover(identity: ReportIdentity) -> list[object]:
    repo_link = (
        f'<link href="{esc(identity.repo_url)}" color="#CFE7D7">{esc(identity.repo_url)}</link>'
    )
    return [
        Spacer(1, 29 * mm),
        para("WHS SECURE CODING ASSIGNMENT", "CoverKicker"),
        para("Tiny Second-hand<br/>Shopping Platform", "CoverTitle"),
        para("안전한 작은 중고거래 플랫폼 <b>바로마켓</b>", "CoverSub"),
        Spacer(1, 22 * mm),
        para(
            "요구사항 분석 - 시스템 설계 - 구현 - 보안 점검 - 테스트 - 유지보수",
            "CoverSub",
        ),
        Spacer(1, 20 * mm),
        para(
            f"<b>분반</b>  {esc(identity.class_name)}<br/>"
            f"<b>성명</b>  {esc(identity.name)}<br/>"
            f"<b>전화번호 뒷자리</b>  {esc(identity.phone_last4)}<br/>"
            f"<b>GitHub</b>  {repo_link}",
            "CoverMeta",
        ),
        Spacer(1, 28 * mm),
        para("작성 기준일 2026-07-22  |  제출 기한 2026-07-24 23:59:59", "CoverSub"),
        PageBreak(),
    ]


def executive_summary(identity: ReportIdentity) -> list[object]:
    return [
        para("요약", "Title"),
        para(
            "바로마켓은 강의 슬라이드와 녹음에서 제시한 7개 최소 기능을 모두 구현한 "
            "FastAPI 기반 Tiny Second-hand Shopping Platform이다. 회원, 상품, 전체/1:1 실시간 채팅, "
            "신고와 임계값 자동 격리, 가상 KRW 송금, 검색, 관리자 관리 화면을 하나의 재현 가능한 애플리케이션으로 완성했다."
        ),
        para(
            "보안은 마지막에 추가한 기능이 아니라 요구사항과 설계의 품질 속성으로 다루었다. "
            "Argon2id, 해시 세션, CSRF + Origin, 소유권/RBAC, SQL 바인딩, 이미지 재인코딩, WebSocket 출처 검증, "
            "송금 원자성과 멱등성, 속도/용량/동시성 제한, 추가 전용 원장과 감사 로그를 적용했다."
        ),
        para(
            "<b>최종 로컬 검증:</b> Pytest 36개 모두 통과, branch coverage 73%, Ruff 26개 Python 파일 통과, "
            "Bandit 발견 0건, pip-audit 알려진 취약점 0건이다. 서로 다른 멱등 키와 동일 키의 병렬 송금, "
            "Content-Length가 없는 6MB 초과 본문, 이미지 폭탄, 느린 WebSocket 수신자도 회귀 테스트에 포함했다.",
            "Callout",
        ),
        para("범위 결정", "Heading3"),
        *bullets(
            [
                "송금은 현실 금융결제가 아닌 현금 가치가 없는 과제용 가상 KRW 이체로 한정했다.",
                "카드, 계좌, 주민등록번호를 수집하지 않아 민감정보 처리 범위를 최소화했다.",
                "SQLite와 로컬 이미지 저장소를 사용하는 단일 인스턴스 교육용 범위로 설계했다.",
                "실제 HTTPS/WSS 역방향 프록시, 다중 프로세스 고부하, 백업 복구 리허설은 미검증 외부 운영 항목으로 분리했다.",
            ]
        ),
        para(
            f'공개 저장소: <link href="{esc(identity.repo_url)}" color="#245E42">{esc(identity.repo_url)}</link>',
            "Body",
        ),
        PageBreak(),
    ]


def toc() -> list[object]:
    contents = TableOfContents()
    contents.levelStyles = [
        ParagraphStyle(
            "TOC1",
            fontName=FONT_NAME,
            fontSize=9.2,
            leading=13,
            leftIndent=0,
            firstLineIndent=0,
            textColor=GREEN_DARK,
            spaceBefore=2,
        ),
        ParagraphStyle(
            "TOC2",
            fontName=FONT_NAME,
            fontSize=7.8,
            leading=10.7,
            leftIndent=13,
            firstLineIndent=0,
            textColor=MUTED,
        ),
    ]
    return [
        para("목차", "Title"),
        para("제목을 클릭하면 해당 섹션으로 이동한다.", "Small"),
        Spacer(1, 5 * mm),
        contents,
        PageBreak(),
    ]


def section_materials() -> list[object]:
    requirement_rows = [
        ["구분", "강의 근거", "본 프로젝트 반영"],
        ["과제 범위", "슬라이드 p.22-30, 녹음", "7개 최소 기능을 FR-01~07로 구조화"],
        [
            "보안 점검",
            "슬라이드 p.31",
            "입력, CSRF, 비밀번호, 세션, 소유권, XSS, WS, 신고 악용 체크리스트",
        ],
        [
            "검증 방법",
            "슬라이드 p.32, 녹음",
            "정상, 예외, 권한, 남용, 병렬 시나리오와 반응형 화면 확인",
        ],
        [
            "유지보수",
            "슬라이드 p.33",
            "요구사항-설계-구현-테스트를 되돌아가는 변경 절차, CI, 백업/사고대응 계획",
        ],
        [
            "제출 요건",
            "슬라이드 p.34-36, 녹음 정정",
            "public GitHub, README 실행법, 개발 전 과정 PDF, 2026-07-24 23:59:59",
        ],
    ]
    return [
        para("1. 과제 및 강의 자료 분석", "Heading1"),
        para(
            "구현 전에 강의 슬라이드 v2의 과제 섹션과 강의 녹음 전사문을 대조했다. "
            "녹음에서 강조한 다중 사용자/휴대폰 채팅, README의 재현성, AI 도구 적극 활용, 제출일 정정을 최종 범위에 반영했다. "
            "강의 원문 파일은 저작권과 개인 자료 분리를 위해 public 저장소에 포함하지 않았다."
        ),
        styled_table(requirement_rows, [25 * mm, 51 * mm, 94 * mm]),
        para("1.1 품질 속성", "Heading2"),
        two_column_cards(
            [
                ("기밀성", "비밀번호, 세션, CSRF, IP 파생값을 응답과 감사 목록에 노출하지 않는다."),
                (
                    "무결성",
                    "송금은 이체, 잔액, 더블엔트리 원장, 감사를 단일 트랜잭션으로 처리한다.",
                ),
                ("가용성", "본문, 이미지, 채팅 프레임, 연결, 행위별 요청에 유한 상한을 둔다."),
                (
                    "추적성",
                    "인증, 제재, 송금 상태 변경은 actor, 대상, 시각, 이벤트를 감사 로그에 추가한다.",
                ),
                (
                    "최소 권한",
                    "일반 회원, 판매자, 채팅 참여자, 관리자의 경계를 서버에서 매번 검사한다.",
                ),
                (
                    "개인정보 최소화",
                    "회원가입에서 아이디, 표시명, 비밀번호 외 주소, 전화, 실명, 금융정보를 요구하지 않는다.",
                ),
            ]
        ),
        PageBreak(),
    ]


def section_requirements() -> list[object]:
    rows = [
        ["ID", "최소 기능", "구현 결과", "주요 검증"],
        [
            "FR-01",
            "플랫폼 가입",
            "가입, 로그인/아웃, 공개 프로필, 소개/비밀번호 변경",
            "Argon2id, 세션 해시/회전/폐기, 잠금, 일반화 오류",
        ],
        [
            "FR-02",
            "상품 등록/열람/관리",
            "이미지 포함 CRUD, 상태, 내 상품",
            "소유권, 형식/크기/화소 검증, 재인코딩, XSS",
        ],
        [
            "FR-03",
            "사용자 소통",
            "전체 실시간 채팅 + 사용자별 1:1 DM",
            "WS Origin/세션/첫 CSRF 프레임, DM 참여자, nonce, fan-out",
        ],
        [
            "FR-04",
            "악성 사용자/상품 차단",
            "신고, 3명 임계값 자동 격리, 관리자 확정/기각/복구",
            "중복, 속도, 10분 신뢰 대기, 자동/수동 출처 분리",
        ],
        [
            "FR-05",
            "사용자 간 송금",
            "현금 가치 없는 가상 KRW, 이체 내역",
            "현재 비밀번호, BEGIN IMMEDIATE, 멱등키, 더블엔트리",
        ],
        [
            "FR-06",
            "상품 검색",
            "이름/설명 검색, 12개 페이징",
            "바인딩 SQL, LIKE 이스케이프, 검색어/페이지/속도 상한",
        ],
        [
            "FR-07",
            "관리자 전체 관리",
            "사용자, 상품, 신고, 이체, 감사 대시보드",
            "중앙 RBAC, CSRF, 허용 상태, 세션/WS 즉시 폐기",
        ],
    ]
    return [
        para("2. 요구사항 분석과 추적성", "Heading1"),
        para(
            "최소 기능을 단순 UI 목록이 아니라 주체, 사전조건, 정상 결과, 예외, 보안 제약으로 분해했다. "
            "각 ID는 설계 문서, 라우터, 스키마, 테스트에서 동일하게 추적된다."
        ),
        styled_table(rows, [14 * mm, 31 * mm, 66 * mm, 59 * mm]),
        para("2.1 비기능 요구사항", "Heading2"),
        *bullets(
            [
                "보안: OWASP 체크리스트와 강의 p.31의 인증, CSRF, 세션, 소유권, XSS, WebSocket, 신고 악용 항목을 충족한다.",
                "성능/가용성: 검색 페이지당 12개, 본문 6MB, 채팅 4KB, 이미지 4MB/20Mpx, 활성 상품 100개로 상한을 정의한다.",
                "접근성: 라벨, 본문 건너뛰기, 대체텍스트, 키보드 포커스, 모바일 메뉴를 제공한다.",
                "운영: production은 약한 비밀, HTTP Origin, 비보안 쿠키, wildcard Host, debug로 시작할 수 없다.",
                "재현성: Python 3.12, 고정된 직접 의존성, CLI DB 초기화/데모 생성, README 명령으로 재현한다.",
            ]
        ),
        para("2.2 위험 기반 우선순위", "Heading2"),
        styled_table(
            [
                ["우선", "자산/경계", "실패 영향", "설계 반응"],
                [
                    "1",
                    "비밀번호, 세션, 관리자",
                    "계정 탈취, 권한 상승",
                    "Argon2id, 해시 세션, CSRF, RBAC, 즉시 폐기",
                ],
                [
                    "2",
                    "잔액, 이체, 원장",
                    "이중 출금, 부분 커밋",
                    "재인증, 직렬화, 멱등성, 불변 원장",
                ],
                [
                    "3",
                    "DM, 신고, 상품",
                    "사생활 노출, 허위 제재",
                    "참여자 제한, 신뢰 대기, 제재 출처 분리",
                ],
                [
                    "4",
                    "서버/저장소",
                    "DoS, 비밀 누출",
                    "크기/속도/동시성 상한, 일반화 오류, fail-closed 설정",
                ],
            ],
            [14 * mm, 42 * mm, 51 * mm, 63 * mm],
        ),
        PageBreak(),
    ]


def section_design() -> list[object]:
    tables = [
        ["users", "계정, 역할, 상태, 지갑 잔액", "username UNIQUE, 허용 상태, 음수 금지"],
        ["sessions", "해시 토큰, CSRF, 만료/최근", "원문 비저장, FK, 시간 상한"],
        ["products", "판매자, 상품, 이미지, 상태/가시성", "price 범위, seller FK, 상태 CHECK"],
        ["messages", "전체/DM, 본문, nonce", "송신자+nonce UNIQUE, 길이 제한"],
        ["reports", "신고자, 대상, 사유, 판정", "신고자-대상 UNIQUE, auto_eligible"],
        ["transfers", "송/수신, 금액, 멱등키", "송신자+키 UNIQUE, 수정/삭제 금지"],
        ["ledger_entries", "사용자별 debit/credit", "이체당 2행, delta 비영, 수정/삭제 금지"],
        ["audit_logs", "actor, event, target, metadata", "자동 시간, 수정/삭제 금지"],
    ]
    return [
        para("3. 시스템 설계", "Heading1"),
        para(
            "서버 렌더링 HTML과 작은 JavaScript를 사용해 클라이언트 신뢰 범위를 줄였다. "
            "요청은 본문 크기와 Host를 가장 먼저 검사한 뒤 서버 세션, CSRF, 입력 검증, 소유권을 통과해 바인딩 SQL로 전달된다. "
            "WebSocket은 별도의 Origin/세션/첫 프레임 인증 경계를 둔다."
        ),
        architecture_diagram(),
        para("그림 1. 보안 경계를 포함한 논리 아키텍처", "Caption"),
        para("3.1 기술 스택 결정", "Heading2"),
        styled_table(
            [
                ["계층", "선택", "선택 이유와 보안 트레이드오프"],
                [
                    "Web",
                    "FastAPI 0.139.2 + Starlette 1.3.1",
                    "입력 타입과 ASGI/WS 지원. OpenAPI/debug 화면은 비활성화",
                ],
                [
                    "Template",
                    "Jinja2 3.1.6",
                    "HTML/XML 자동 이스케이프. 템플릿에 safe 우회를 사용하지 않음",
                ],
                [
                    "DB",
                    "SQLite 3 + WAL",
                    "과제용 단일 인스턴스에 적합. FK, busy timeout, 트랜잭션과 트리거 사용",
                ],
                [
                    "Password",
                    "argon2-cffi 25.1.0",
                    "OWASP 권고를 반영한 Argon2id 19MiB, time 2, parallelism 1",
                ],
                [
                    "Image",
                    "Pillow 12.3.0",
                    "실제 디코딩과 폭탄 검사, 1600px JPEG 재인코딩, EXIF/원본명 제거",
                ],
                [
                    "Deploy",
                    "Uvicorn worker 1 + non-root container",
                    "인메모리 WS/rate limiter 일관성. 수평 확장은 외부 저장소 도입 전까지 제한",
                ],
            ],
            [25 * mm, 48 * mm, 97 * mm],
        ),
        PageBreak(),
        para("3.2 데이터 설계와 불변식", "Heading2"),
        para(
            "8개 테이블의 키는 SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`를 사용한다. "
            "순차 ID를 숨기는 것을 권한 통제로 오인하지 않고, 열람/변경할 때 소유자, 참여자, 역할을 따로 검증한다. "
            "스키마는 CHECK, UNIQUE, FOREIGN KEY, 불변 트리거로 애플리케이션 실수의 피해를 줄인다."
        ),
        styled_table([["테이블", "책임", "핵심 제약"]] + tables, [31 * mm, 64 * mm, 75 * mm]),
        para("3.3 가상 송금 트랜잭션", "Heading2"),
        transfer_diagram(),
        para("그림 2. 재인증부터 감사까지의 단일 송금 트랜잭션", "Caption"),
        para(
            "송금은 `BEGIN IMMEDIATE`로 쓰기 경쟁을 직렬화하고 `balance >= amount`를 포함한 조건부 UPDATE로 음수 잔액을 방지한다. "
            "송신 debit와 수신 credit의 합은 항상 0이며 transfer, ledger 2행, audit는 함께 커밋되거나 모두 롤백된다. "
            "가입 초기 100,000원은 교육용 시작 잔액이며 genesis ledger를 생성하지 않는 한계를 잔여 위험에 기록했다."
        ),
        PageBreak(),
    ]


def section_implementation() -> list[object]:
    return [
        para("4. 구현", "Heading1"),
        para("4.1 인증과 세션", "Heading2"),
        *bullets(
            [
                "아이디는 영문자로 시작하는 3~30자, 표시명은 2~30자, 소개는 500자로 서버에서 재검증한다.",
                "비밀번호는 12~128자와 흔한 값 거부를 적용하고 Argon2id로 저장한다.",
                "존재하지 않는 계정도 dummy Argon2를 수행하고, 로그인 실패 응답을 동일하게 표현한다.",
                "5회 실패 시 15분 잠금, IP 단위 5분 20회 제한, 비밀번호 변경 15분 5회 제한을 둔다.",
                "세션 토큰은 256비트 난수이고 DB에는 SHA-256 해시만 남긴다. 절대 8시간, 유휴 30분 만료를 적용한다.",
                "로그인 전 CSRF는 DB 행 대신 발급 시각과 토큰을 HMAC 서명한 HttpOnly SameSite=Lax 쿠키를 사용한다.",
            ]
        ),
        para("4.2 상품, 검색, 이미지", "Heading2"),
        *bullets(
            [
                "상품 제목 2~100자, 설명 1~5,000자, 가격 0~100,000,000원, 상태 허용 목록을 검증한다.",
                "수정/삭제는 seller_id가 세션 사용자와 일치할 때만 성공한다. 순차 ID를 바꾸는 IDOR 시도는 거부된다.",
                "JPEG/PNG/WebP만 실제 디코딩하며 SVG나 파일명을 신뢰하지 않는다. 40자 난수 이름의 JPEG로 재인코딩한다.",
                "원본 4MB, 10,000px 변, 20Mpx, 결과 1,600px 상한과 전역 동시 처리 3개를 적용한다.",
                "검색은 `%`, `_`, `!`를 LIKE 와일드카드로 해석하지 않도록 이스케이프하고 모든 값을 SQL 매개변수로 바인딩한다.",
            ]
        ),
        para("4.3 실시간 채팅", "Heading2"),
        *bullets(
            [
                "핸드셰이크의 정확한 Origin과 해시 세션을 검사한 뒤, 첫 4KB JSON 프레임의 CSRF가 일치해야 인증한다.",
                "발신자는 클라이언트 필드가 아닌 세션에서 결정하고 DM은 송신자와 수신자에게만 fan-out한다.",
                "사용자당 3개, 프로세스 80개 연결, 인증 전 IP당 분당 30회, 메시지 10초 20개 상한을 둔다.",
                "각 클라이언트에 전송 lock과 2초 timeout을 적용하고 병렬 fan-out으로 느린 수신자가 전체를 막지 못하게 한다.",
                "정지/비밀번호 변경/로그아웃은 기존 WebSocket을 4401로 즉시 종료한다.",
            ]
        ),
        PageBreak(),
        para("4.4 신고와 관리자", "Heading2"),
        *bullets(
            [
                "사용자와 상품을 사유와 함께 신고하며 신고자-대상 유일 제약으로 중복을 막는다.",
                "서로 다른 3명의 활성 신고가 누적되면 상품을 숨기거나 사용자를 일시 정지한다.",
                "가입 10분 미만 계정의 신고는 접수되지만 자동 제재 계산에서 제외하고, 계정당 시간당 10회로 제한한다.",
                "자동 제재는 auto_report, 관리자 제재는 admin 출처를 기록하여 신고 기각이 수동 정지까지 되돌리지 못하게 한다.",
                "중앙 require_admin 경계가 모든 관리자 라우트를 보호하며, 목록에는 비밀번호 해시, 세션 토큰, IP 파생값을 포함하지 않는다.",
            ]
        ),
        para("4.5 운영 하드닝", "Heading2"),
        styled_table(
            [
                ["항목", "development", "production fail-closed"],
                [
                    "APP_SECRET",
                    "프로세스 수명 난수 허용 + 경고",
                    "명시적 32바이트 이상, 약한 패턴 거부",
                ],
                ["Cookie", "HttpOnly, SameSite=Lax", "HttpOnly, SameSite=Lax, Secure 필수"],
                ["Origin", "localhost HTTP 허용", "HTTPS 오리진만 허용"],
                ["Host", "localhost/127.0.0.1/testserver", "명시 Host, wildcard 금지"],
                ["Debug/API docs", "비활성화", "debug false 필수, OpenAPI/docs 비활성화"],
                ["Container", "로컬 실행 가능", "non-root marketplace, worker 1, 4KB ws frame"],
            ],
            [37 * mm, 58 * mm, 75 * mm],
        ),
        PageBreak(),
    ]


def section_weaknesses() -> list[object]:
    weaknesses = [
        [
            "1",
            "익명 CSRF용 DB 세션 남용",
            "GET 반복으로 DB DoS",
            "만료형 HMAC 서명 익명 쿠키",
            "hardening: 세션 0행/변조 403",
        ],
        [
            "2",
            "HTTP 전체 본문 상한 부재",
            "multipart 파싱 전 메모리 DoS",
            "pure ASGI 6MB, 선언+실제 스트림 계수",
            "Content-Length/chunked 둘 다 413",
        ],
        [
            "3",
            "확장자/MIME/원본명 신뢰",
            "SVG XSS, 경로, 덮어쓰기",
            "실제 디코딩, 난수 40-hex, JPEG 재인코딩",
            "SVG 거부, JPEG 확인",
        ],
        [
            "4",
            "압축 이미지/병렬 디코딩",
            "메모리/CPU 고갈",
            "20Mpx/10,000px 선검사, 1600px, worker thread, cap 3",
            "이미지 폭탄 거부",
        ],
        [
            "5",
            "약한 비밀번호 저장 위험",
            "DB 유출 시 탈취",
            "12~128자, 흔한 값 거부, Argon2id",
            "해시 형식/원문 불일치",
        ],
        [
            "6",
            "세션 원문/고정/장기 재사용",
            "DB 유출, fixation",
            "해시 저장, 로그인 회전, 절대/유휴 만료, 폐기",
            "원문 DB 불일치, 변경 후 전 세션 무효",
        ],
        [
            "7",
            "로그인 오류 차이/무제한 대입",
            "계정 열거, stuffing",
            "dummy hash, 일반 오류, IP 제한, 5회 잠금",
            "존재/부재 응답 동일",
        ],
        [
            "8",
            "상태 변경 CSRF/교차 Origin",
            "사용자 권한 오용",
            "세션 결합 토큰 + Origin/Referer + POST",
            "누락/오류/악성 Origin 403",
        ],
        [
            "9",
            "로그인 후 open redirect",
            "피싱/외부 유도",
            "로컬 절대경로만, //, scheme, host, \\ 거부",
            "safe_next 경계 코드",
        ],
        [
            "10",
            "상품/프로필/채팅 XSS",
            "세션 탈취",
            "Jinja autoescape, textContent, self CSP",
            "스크립트 문자열 이스케이프",
        ],
        [
            "11",
            "검색 SQL 문자열/와일드카드",
            "SQLi, 결과 확대",
            "매개변수 바인딩 + LIKE 이스케이프",
            "SQL 모양 입력을 리터럴로 처리",
        ],
        [
            "12",
            "순차 ID 타인 상품 변경",
            "IDOR",
            "seller_id 서버 소유권 검사",
            "비소유자 수정/삭제 거부",
        ],
        [
            "13",
            "쿠키만 확인하는 WebSocket",
            "CSWSH, 사칭, DM 노출",
            "Origin+세션+첫 CSRF, 세션 발신자, DM 참여자",
            "악성 Origin/CSRF/제3자 거부",
        ],
        [
            "14",
            "무제한 WS/느린 수신자",
            "연결/팬아웃 DoS",
            "user 3/global 80, 4KB, timeout 2s, send lock, 병렬 fan-out",
            "연결 cap/느린 클라이언트 회귀",
        ],
        [
            "15",
            "중복/즉석 다계정 신고",
            "정상 대상 자동 차단",
            "unique, 시간당 10, 가입 10분 자동제재 제외",
            "중복/신규 신고자 테스트",
        ],
        [
            "16",
            "자동/수동 제재 출처 혼합",
            "기각이 수동조치를 복구",
            "status/visibility source, 자동분만 조건부 복구",
            "자동/수동 복구 분기",
        ],
        [
            "17",
            "조회-변경 송금/재전송",
            "이중 출금, 부분 원장",
            "재인증, BEGIN IMMEDIATE, 조건부 차감, 멱등키, 한 트랜잭션",
            "순차+병렬 멱등/초과 지출",
        ],
        [
            "18",
            "내부 예외/운영 오설정",
            "SQL/경로/비밀 노출",
            "일반 500+요청 ID, API docs off, 보안 헤더, production fail-closed",
            "예외 응답/설정 거부 테스트",
        ],
    ]
    return [
        para("5. 보안 약점 발견과 개선", "Heading1"),
        para(
            "아래 표는 단순히 적용한 보안 기능을 나열한 것이 아니라, 개발 중 실제로 식별한 약점/악용 가능성, 영향, 설계 변경, 회귀 근거를 추적한 결과다. "
            "AI 리뷰가 제안한 이미지 병렬 디코딩, 느린 WebSocket fan-out, 동기 SQLite event-loop 점유도 코드와 추가 테스트로 확인한 뒤 개선했다."
        ),
        styled_table(
            [["#", "발견한 약점/위험", "가능한 영향", "변경 내용", "검증 근거"]] + weaknesses,
            [7 * mm, 39 * mm, 31 * mm, 55 * mm, 38 * mm],
            font_size=6.8,
        ),
        para(
            "자동화 브라우저는 로컬 페이지 폼 전송에서 `Origin: null`을 보냈고, 애플리케이션은 정확한 허용 Origin이 아니므로 403을 반환했다. "
            "스크린샷을 위해 `null`을 허용하는 예외를 추가하지 않았다. curl과 테스트 클라이언트로 정상 Origin, 쿠키, 폼 토큰을 제공했을 때는 로그인 303을 다시 확인했다.",
            "Warning",
        ),
        PageBreak(),
    ]


def section_testing() -> list[object]:
    files = [
        [
            "tests/test_auth.py",
            "6",
            "Argon2id, 세션 해시, 일반화 오류, CSRF/Origin, 쿠키, 세션 폐기",
        ],
        [
            "tests/test_products_moderation.py",
            "5",
            "이미지 재인코딩/SVG, XSS, SQL 입력, 소유권, 3명 신고",
        ],
        [
            "tests/test_security_websocket.py",
            "8",
            "헤더, 오류 은닉, WS Origin/CSRF, 연결 cap, 느린 fan-out, 전체/DM/nonce",
        ],
        [
            "tests/test_wallet_admin.py",
            "7",
            "원장 합 0, rollback, 순차/병렬 멱등, 병렬 초과지출, 관리자 RBAC",
        ],
        [
            "tests/test_hardening.py",
            "10",
            "익명 HMAC CSRF, 6MB 선언/스트림, 이미지 폭탄, 신규 신고자, 비밀번호 속도, 제재 복구",
        ],
        ["합계", "36", "모두 통과, branch coverage 73%"],
    ]
    checklist = [
        ["영역", "주요 체크", "결과", "근거/남은 항목"],
        [
            "인증",
            "입력, Argon2id, 일반화 오류, 잠금, 세션 회전/폐기",
            "통과",
            "제한 만료 후 회복 경계 시간 자동화는 남음",
        ],
        [
            "CSRF",
            "세션 토큰, Origin/Referer, 익명 서명 쿠키",
            "통과",
            "실제 역방향 프록시 오리진 재확인 필요",
        ],
        [
            "상품",
            "소유권, XSS, 상태, 이미지 디코딩/상한",
            "통과",
            "장시간 대량 이미지 soak 테스트는 남음",
        ],
        [
            "WebSocket",
            "Origin, 세션, CSRF, 사칭, DM, nonce, cap, timeout",
            "통과",
            "실제 WSS 프록시/두 물리 기기 시연은 남음",
        ],
        [
            "신고",
            "중복, 신뢰 대기, 임계값, 자동/수동 복구",
            "통과",
            "숙성된 Sybil 공모는 잔여 위험",
        ],
        [
            "송금",
            "재인증, 잔액, rollback, 원장, 순차/병렬 멱등",
            "통과",
            "다중 프로세스/대규모 부하는 남음",
        ],
        [
            "관리자",
            "RBAC, CSRF, 허용 상태, 세션/WS 폐기, 감사",
            "통과",
            "MFA/작업별 재인증은 교육용 범위 밖",
        ],
        [
            "운영",
            "보안 헤더, fail-closed, 예외 은닉, 의존성 감사",
            "부분",
            "원격 Actions, HTTPS/WSS, 백업 복구는 배포 후 검증",
        ],
    ]
    return (
        [
            para("6. 체크리스트와 테스팅", "Heading1"),
            para(
                "강의 p.31의 질문을 기반으로 정상, 예외, 권한, 남용, 동시성 시나리오를 작성했다. "
                "테스트는 매번 새 임시 SQLite DB와 업로드 디렉터리를 사용하며, 테스트 비밀과 계정은 합성 데이터다."
            ),
            Preformatted(
                "$ pytest -q\n36 passed, 2 warnings\nbranch coverage: 73%\n\n$ ruff check . && ruff format --check .\nAll checks passed / 26 files formatted\n\n$ bandit -q -r app\nexit 0\n\n$ pip-audit --local\nNo known vulnerabilities found",
                STYLES["Code"],
            ),
            styled_table([["테스트 파일", "수", "범위"]] + files, [47 * mm, 13 * mm, 110 * mm]),
            para(
                "2개 경고는 Starlette TestClient의 향후 httpx2 전환 안내와 개발용 APP_SECRET 미설정 시 프로세스 수명 키 생성 안내다. "
                "test fixture는 고정된 테스트 비밀을 사용하며 production은 APP_SECRET 미설정 시 시작하지 않는다.",
                "Small",
            ),
            PageBreak(),
            para("6.1 보안 게이트 요약", "Heading2"),
            styled_table(checklist, [27 * mm, 66 * mm, 17 * mm, 60 * mm]),
            para("6.2 수동 UI 검증", "Heading2"),
            para(
                "로컬 서버에서 데스크톱 상품 목록/상세, 로그인, 회원가입, 검색, 390x844 모바일 레이아웃과 펼쳐진 메뉴를 확인했다. "
                "키보드/스크린리더가 사용하는 role, label, heading 구조도 DOM snapshot에서 확인했다."
            ),
        ]
        + screenshot_section()
        + [PageBreak()]
    )


def screenshot_section() -> list[object]:
    directory = ROOT / "output" / "screenshots"
    product = screenshot(directory / "03-product-detail.png", 168 * mm, 115 * mm)
    login = screenshot(directory / "06-login.png", 109 * mm, 88 * mm)
    mobile = screenshot(directory / "05-mobile-menu.png", 52 * mm, 88 * mm)
    blocked = screenshot(directory / "02-csrf-blocked.png", 168 * mm, 92 * mm)
    output: list[object] = []
    if product:
        output.extend(
            [product, para("그림 3. 비회원이 열람한 상품 상세와 안전거래 안내", "Caption")]
        )
    if login and mobile:
        table = Table([[login, mobile]], colWidths=[112 * mm, 56 * mm], hAlign="CENTER")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        output.extend([table, para("그림 4. 데스크톱 로그인과 390px 모바일 메뉴", "Caption")])
    if blocked:
        output.extend(
            [
                blocked,
                para(
                    "그림 5. Origin 검증에 의해 차단된 자동화 브라우저의 null Origin 폼 전송",
                    "Caption",
                ),
            ]
        )
    if not output:
        output.append(
            para("로컬 스크린샷이 없는 clean clone에서 생성하여 UI 이미지는 생략했다.", "Small")
        )
    return output


def section_ai() -> list[object]:
    return [
        para("7. AI 도구 활용", "Heading1"),
        para(
            "과제의 요구처럼 Codex를 주 협업 도구로 사용했다. 주 에이전트가 요구사항, 구현 범위, 통합, 최종 검증을 조율했고, "
            "병렬 서브에이전트가 강의 자료 분석, 아키텍처/위협 리뷰, 테스트 근거 문서화, CI/공급망 점검을 나눠 수행했다."
        ),
        styled_table(
            [
                ["단계", "AI 활용", "사람/실행 검증"],
                [
                    "요구사항",
                    "PDF/녹음의 최소 기능과 보안 질문을 구조화",
                    "슬라이드 페이지, 녹음의 기한 정정과 다중 사용자 요구를 대조",
                ],
                [
                    "설계",
                    "STRIDE/강의 체크리스트, 세션/송금/신고 불변식 제안",
                    "현실 금융 배제, 단일 인스턴스와 잔여 위험 수용 결정",
                ],
                [
                    "구현",
                    "라우터, repository, 스키마, 템플릿, JS/CSS, CLI 초안",
                    "실제 코드 리뷰, 런타임 오류 수정, 스키마 불변식 확인",
                ],
                [
                    "공격면 리뷰",
                    "익명 DB DoS, 이미지 병렬 메모리, 느린 WS fan-out, 동기 DB, Sybil 식별",
                    "테스트로 재현 후 수정. 숙성 Sybil은 잔여 위험으로 기록",
                ],
                [
                    "테스트",
                    "정상/예외/권한/남용/병렬 시나리오 생성",
                    "36개 Pytest, 분기 73%, Ruff, Bandit, pip-audit, DOM/스크린샷",
                ],
                [
                    "문서/배포",
                    "README, 설계/위협/체크리스트/유지보수, PDF 생성기",
                    "코드-문서 불일치 수정, 비밀/개인정보/강의 원문의 Git 제외",
                ],
            ],
            [27 * mm, 67 * mm, 76 * mm],
        ),
        para("7.1 AI 사용 중 발견한 한계", "Heading2"),
        *bullets(
            [
                "구현이 변한 뒤에도 AI가 작성한 문서에 UUID 설계, 31개 테스트, 71% coverage가 일시적으로 남았다. 최종에 코드/실행 출력을 기준으로 36개/73%와 INTEGER PK로 동기화했다.",
                "정적 분석이 취약점 가능성을 제안해도 실제 영향은 테스트와 트랜잭션 불변식으로 확인해야 했다.",
                "보안 도구의 0건은 완전성을 의미하지 않으며, coverage 수치도 미실행 분기와 운영 환경의 공격면을 대신하지 않는다.",
                "최종 범위 결정, 잔여 위험 수용, public 저장소 확인, 제출 책임은 사람에게 있다.",
            ]
        ),
        para(
            "실제 사용자 비밀번호, 세션 토큰, 전화번호, 강의 원문을 AI 프롬프트나 공개 저장소의 예시로 사용하지 않았다. 데모 계정과 가상 금액은 모두 합성 데이터다.",
            "Callout",
        ),
        PageBreak(),
    ]


def section_maintenance() -> list[object]:
    return [
        para("8. 유지보수와 운영 계획", "Heading1"),
        para(
            "변경은 요구사항 영향 분석 -> 위협/설계 갱신 -> 최소 코드 변경 -> 회귀 테스트 -> 문서 동기화 -> 배포/관찰 순서를 따른다. "
            "강의 p.33처럼 운영에서 발견한 오류가 잘못된 요구사항이나 설계에서 비롯되었다면 해당 단계로 되돌아간다."
        ),
        two_column_cards(
            [
                (
                    "CI 게이트",
                    "Python 3.12에서 Ruff lint/format, Pytest+branch coverage, Bandit, pip-audit strict를 실행한다. workflow 토큰 권한은 contents: read다.",
                ),
                (
                    "의존성",
                    "직접 런타임/개발 의존성을 exact pin하고 Dependabot이 pip와 Actions를 주간 점검한다. 간접 재현성은 lock 추가 전까지 잔여 항목이다.",
                ),
                (
                    "데이터베이스",
                    "PRAGMA user_version=3, 보수적 마이그레이션, integrity_check, FK 위반, 원장 2행/합 0, 음수 잔액을 점검한다.",
                ),
                (
                    "백업/복구",
                    "실행 중 파일 복사 대신 SQLite online backup/.backup, 이미지 manifest, checksum을 사용하고 분리 경로에 복원 리허설한다.",
                ),
                (
                    "모니터링",
                    "5xx, 로그인/CSRF/Origin 거부, WS cap, SQLite busy/rollback, 디스크, 백업 실패를 지표로 수집하되 토큰/비밀/메시지는 기록하지 않는다.",
                ),
                (
                    "사고 대응",
                    "탐지/분류 -> 봉쇄 -> 증거 보존 -> 수정 -> 검증된 복구 -> 사후 분석 순으로 진행한다.",
                ),
            ]
        ),
        para("8.1 배포 전/후 체크리스트", "Heading2"),
        styled_table(
            [
                ["시점", "필수 확인", "실패 시 조치"],
                [
                    "commit 전",
                    "`.env`, DB, 업로드, 백업, 로그, PDF 개인정보 제외; diff 리뷰",
                    "파일 제거/비밀 회전 후 새 이력 생성",
                ],
                [
                    "push 전",
                    "36 tests, Ruff, Bandit, pip-audit, README clean 실행, 생성 DB 미추적",
                    "원인 수정 후 전체 게이트 재실행",
                ],
                [
                    "GitHub 후",
                    "public 접근, Actions 성공, 파일 목록/이력 secret scan",
                    "원격 유출 시 즉시 삭제만 하지 말고 비밀 회전",
                ],
                [
                    "배포 전",
                    "APP_SECRET, HTTPS Origin, Secure cookie, Host, debug off, 프록시 WS upgrade/body limit",
                    "fail-closed 시작 오류를 해결하기 전 공개 금지",
                ],
                [
                    "배포 후",
                    "헬스, 로그인, 상품, DM, 송금, 신고, 관리자, 백업/복구",
                    "이상 시 트래픽 차단/롤백, 증거 보존",
                ],
            ],
            [28 * mm, 83 * mm, 59 * mm],
        ),
        PageBreak(),
    ]


def section_risks_conclusion(identity: ReportIdentity) -> list[object]:
    risks = [
        [
            "숙성된 Sybil 신고",
            "10분 대기는 즉석 계정만 줄임",
            "이메일 검증, 거래/대화 관계, 신뢰도 점수 추가",
        ],
        [
            "단일 프로세스 제한",
            "WS/rate limiter가 인메모리라 worker 1",
            "Redis 등 공유 저장소로 이전 후 수평 확장",
        ],
        [
            "DM 평문 저장",
            "참여자 제한은 있지만 E2EE 아님",
            "보존기간/삭제, 저장 암호화, E2EE는 별도 제품 설계",
        ],
        [
            "관리자 MFA 부재",
            "RBAC/CSRF는 있지만 계정 탈취 방어 한계",
            "MFA, 민감 작업 재인증, 승인 흐름",
        ],
        [
            "초기 잔액 ledger 미생성",
            "원장만으로 전체 잔액 재구성 불가",
            "genesis/mint 이벤트를 추가하고 마이그레이션",
        ],
        [
            "SQLite/로컬 이미지",
            "HA, 파일 암호화, 다중 노드 부적합",
            "운영 전환 시 관리 DB/객체 저장소/KMS 검토",
        ],
        [
            "외부 운영 검증 미수행",
            "HTTPS/WSS, 프록시, 백업, 대규모 부하 증거 없음",
            "공개 배포 전 staging 게이트로 실제 수행",
        ],
        [
            "자동화 범위",
            "73% coverage/Bandit 0이 완전성을 증명하지 않음",
            "변경분 위협 모델, fuzz/부하/침투 테스트 확장",
        ],
    ]
    return [
        para("9. 잔여 위험과 한계", "Heading1"),
        para(
            "시큐어코딩은 취약점 0을 단정하는 활동이 아니라 위험을 식별하고, 제거/완화/수용을 근거와 함께 기록하는 과정이다. "
            "다음은 과제용 단일 인스턴스에서 의도적으로 수용했거나 실제 운영 전에 추가 검증할 항목이다."
        ),
        styled_table(
            [["잔여 위험", "현재 한계", "후속 조치"]] + risks, [45 * mm, 57 * mm, 68 * mm]
        ),
        para("10. 결론", "Heading1"),
        para(
            "바로마켓은 강의에서 요구한 7개 기능을 모두 제공하면서 인증, 입력, 권한, 실시간 통신, 자동 제재, 금액 무결성, 운영 설정을 보안 경계로 설계했다. "
            "개발 중 발견한 18개 약점/악용 가능성을 코드와 회귀 테스트로 개선했고, 36개 자동 테스트와 정적/공급망 점검을 통과했다."
        ),
        para(
            "가장 중요한 결과는 '정상적으로 동작한다'를 넘어, 잘못된 주체가 조작된 입력을 빠르게/동시에/재전송했을 때도 보호 불변식이 유지되는지 검증한 것이다. "
            "다만 현장 배포의 TLS, 다중 프로세스, 백업 복구를 로컬 결과로 과장하지 않고 외부 검증 게이트로 남겼다."
        ),
        para(
            f'최종 코드와 재현 방법: <link href="{esc(identity.repo_url)}" color="#245E42">{esc(identity.repo_url)}</link>',
            "Callout",
        ),
        PageBreak(),
    ]


def appendix(identity: ReportIdentity) -> list[object]:
    return [
        para("부록 A. 재현 및 검증 명령", "Heading1"),
        Preformatted(
            f"git clone {identity.repo_url}.git\n"
            "cd whs-secure-coding\n"
            "python3.12 -m venv .venv\n"
            "source .venv/bin/activate\n"
            "python -m pip install -r requirements-dev.txt\n"
            "cp .env.example .env\n"
            'python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "# Paste the output into APP_SECRET in .env.\n"
            "python -m app.cli init-db\n"
            "uvicorn app.main:app --host 127.0.0.1 --port 8000 \\\n"
            "  --ws-max-size 4096 --ws-max-queue 16 --limit-concurrency 100\n\n"
            "pytest -q\n"
            "ruff check .\n"
            "ruff format --check .\n"
            "bandit -q -r app\n"
            "pip-audit --local",
            STYLES["Code"],
        ),
        para(
            "개발 서버의 비밀은 예시를 복사하지 말고 매번 난수로 생성한다. 실제 production은 README의 HTTPS Origin, Secure cookie, Host 요건을 모두 충족하지 않으면 시작하지 않는다.",
            "Small",
        ),
        para("부록 B. 참고자료", "Heading1"),
        *bullets(
            [
                "유효곤, 시큐어코딩 강의 슬라이드 v2, p.22-36, 및 강의 녹음 전사문. public 저장소에는 미포함.",
                '<link href="https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html" color="#245E42">OWASP Password Storage Cheat Sheet</link>',
                '<link href="https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html" color="#245E42">OWASP Session Management Cheat Sheet</link>',
                '<link href="https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html" color="#245E42">OWASP CSRF Prevention Cheat Sheet</link>',
                '<link href="https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html" color="#245E42">OWASP WebSocket Security Cheat Sheet</link>',
                '<link href="https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html" color="#245E42">OWASP File Upload Cheat Sheet</link>',
                '<link href="https://cwe.mitre.org/data/definitions/79.html" color="#245E42">CWE-79 XSS</link>, <link href="https://cwe.mitre.org/data/definitions/89.html" color="#245E42">CWE-89 SQL Injection</link>, <link href="https://cwe.mitre.org/data/definitions/352.html" color="#245E42">CWE-352 CSRF</link>, <link href="https://cwe.mitre.org/data/definitions/639.html" color="#245E42">CWE-639 IDOR</link>',
            ]
        ),
        para("부록 C. 제출 항목 확인", "Heading1"),
        styled_table(
            [
                ["항목", "확인 결과"],
                ["프로그램", "7개 최소 기능 구현, README 설치/실행법 작성"],
                [
                    "보고서",
                    "요구사항, 설계, 구현, 약점 개선, 체크리스트/테스트, 유지보수, AI 활용 포함",
                ],
                ["GitHub", "public `whs-secure-coding`, 강의 원문/비밀/DB/업로드/개인정보 미포함"],
                ["파일명", "`[WHS][secure-coding][XX반]이름(뒷자리4개).pdf`, 공백 없음"],
                ["제출", "2026-07-24(금) 23:59:59 전 LMS에 PDF 1개 제출"],
            ],
            [44 * mm, 126 * mm],
        ),
    ]


def build_report(identity: ReportIdentity, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = ReportDocTemplate(
        str(output_path),
        identity,
        pagesize=A4,
        title="Tiny Second-hand Shopping Platform 시큐어코딩 보고서",
        author=identity.name,
        subject="WHS secure-coding assignment",
        creator="whs-secure-coding report generator",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    story: list[object] = []
    story += cover(identity)
    story += executive_summary(identity)
    story += toc()
    story += section_materials()
    story += section_requirements()
    story += section_design()
    story += section_implementation()
    story += section_weaknesses()
    story += section_testing()
    story += section_ai()
    story += section_maintenance()
    story += section_risks_conclusion(identity)
    story += appendix(identity)
    document.multiBuild(story)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="제출용 시큐어코딩 PDF 보고서 생성")
    parser.add_argument("--class-name", required=True, help="예: 01반")
    parser.add_argument("--name", required=True, help="제출자 성명")
    parser.add_argument("--phone-last4", required=True, help="전화번호 뒷 4자리")
    parser.add_argument("--repo-url", required=True, help="public GitHub 저장소 URL")
    parser.add_argument("--output", type=Path, help="선택 출력 경로")
    return parser.parse_args()


def validate_identity(args: argparse.Namespace) -> ReportIdentity:
    class_name = re.sub(r"\s+", "", args.class_name)
    if re.fullmatch(r"\d{1,2}", class_name):
        class_name += "반"
    if not re.fullmatch(r"(?:\d{1,2}|XX)반", class_name, flags=re.IGNORECASE):
        raise SystemExit("--class-name은 01반 또는 XX반 형식이어야 합니다.")
    name = args.name.strip()
    if not name or len(name) > 40 or any(character in name for character in "/\\[]()"):
        raise SystemExit("--name을 파일명에 사용할 수 있는 1~40자로 입력하세요.")
    phone = args.phone_last4.strip()
    if not re.fullmatch(r"\d{4}", phone):
        raise SystemExit("--phone-last4는 숫자 4자리여야 합니다.")
    repo_url = args.repo_url.strip().rstrip("/")
    parsed = urlparse(repo_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise SystemExit("--repo-url은 https://github.com/... 형식이어야 합니다.")
    if not parsed.path.lower().endswith("/whs-secure-coding"):
        raise SystemExit("저장소 이름은 whs-secure-coding이어야 합니다.")
    return ReportIdentity(class_name=class_name, name=name, phone_last4=phone, repo_url=repo_url)


def main() -> None:
    args = parse_args()
    identity = validate_identity(args)
    output_path = args.output or ROOT / "output" / "pdf" / identity.filename
    output_path = output_path.expanduser().resolve()
    allowed_output = (ROOT / "output" / "pdf").resolve()
    if allowed_output not in output_path.parents:
        raise SystemExit("개인정보 보호를 위해 출력은 output/pdf/ 아래만 허용합니다.")
    build_report(identity, output_path)
    print(output_path)


if __name__ == "__main__":
    main()
