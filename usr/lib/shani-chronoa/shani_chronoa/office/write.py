"""Writing new .docx (from Markdown), .xlsx (from rows) and .pptx (from slides) with the standard library."""

from __future__ import annotations


import io
import re
import zipfile
from typing import Optional
from xml.sax.saxutils import escape

from .common import (  # noqa: F401
    A,
    CT,
    OfficeError,
    P,
    PKG_REL,
    R,
    S,
    W,
    _XML,
    col_letters,
)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

_CT_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Types xmlns="{CT}"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>')


def _package(parts: "dict[str, str]") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # [Content_Types].xml first, as Office writes it
        z.writestr("[Content_Types].xml", parts.pop("[Content_Types].xml"))
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


def _root_rels(main: str, rel_type: str = "officeDocument") -> str:
    return (_XML + f'<Relationships xmlns="{PKG_REL}">'
            f'<Relationship Id="rId1" Type="{R}/{rel_type}" Target="{main}"/>'
            f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>')


def _core(title: str) -> str:
    import time as _t
    now = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    return (_XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(title)}</dc:title><dc:creator>Shani Chronoa</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')


_CORE_CT = ('<Override PartName="/docProps/core.xml" '
            'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>')


def _runs(text: str) -> str:
    """Inline **bold** to Word runs; everything else is plain text."""
    out = []
    for i, piece in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not piece:
            continue
        rpr = "<w:rPr><w:b/></w:rPr>" if i % 2 else ""
        out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{escape(piece)}</w:t></w:r>')
    return "".join(out)


def _docx_body(markdown: str) -> str:
    """Light Markdown to WordprocessingML paragraphs: #..###, - / * bullets, 1. lists, | tables |, **bold**."""
    out, table = [], []

    def flush_table() -> None:
        if not table:
            return
        rows = [r for r in table if not re.fullmatch(r"\|?\s*:?-{2,}.*", r.replace(" ", ""))]
        cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
        width = max(len(r) for r in cells)
        grid = "".join("<w:gridCol/>" for _ in range(width))
        trs = []
        for n, row in enumerate(cells):
            tcs = "".join(
                f'<w:tc><w:p>{_runs(("**" + c + "**") if n == 0 and c else c)}</w:p></w:tc>'
                for c in row + [""] * (width - len(row)))
            trs.append(f"<w:tr>{tcs}</w:tr>")
        out.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
                   f'<w:tblGrid>{grid}</w:tblGrid>{"".join(trs)}</w:tbl><w:p/>')
        table.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("|"):
            table.append(line)
            continue
        flush_table()
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            out.append(f'<w:p><w:pPr><w:pStyle w:val="Heading{len(m.group(1))}"/></w:pPr>{_runs(m.group(2))}</w:p>')
        elif re.match(r"^\s*[-*•]\s+", line):
            out.append('<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/>'
                       f'<w:numId w:val="1"/></w:numPr></w:pPr>{_runs(re.sub(r"^\s*[-*•]\s+", "", line))}</w:p>')
        elif re.match(r"^\s*\d+[.)]\s+", line):
            out.append('<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/>'
                       f'<w:numId w:val="2"/></w:numPr></w:pPr>{_runs(re.sub(r"^\s*\d+[.)]\s+", "", line))}</w:p>')
        elif line.strip():
            out.append(f"<w:p>{_runs(line.strip())}</w:p>")
        else:
            out.append("<w:p/>") if out and out[-1] != "<w:p/>" else None
    flush_table()
    return "".join(out)


_DOCX_STYLES = (_XML + f'<w:styles xmlns:w="{W}">'
                '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
                '<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr>'
                '</w:pPrDefault></w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
                + "".join(
                    f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
                    f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
                    f'<w:pPr><w:keepNext/><w:spacing w:before="{360 - n * 60}" w:after="120"/>'
                    f'<w:outlineLvl w:val="{n - 1}"/></w:pPr><w:rPr><w:b/><w:sz w:val="{36 - n * 4}"/></w:rPr></w:style>'
                    for n in (1, 2, 3))
                + '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
                '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="720"/></w:pPr></w:style>'
                '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
                + "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                          for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
                + '</w:tblBorders></w:tblPr></w:style></w:styles>')

_DOCX_NUMBERING = (_XML + f'<w:numbering xmlns:w="{W}">'
                   '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
                   '<w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
                   '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
                   '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
                   '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
                   '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
                   '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
                   '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>')


def make_docx(markdown: str, title: str = "") -> bytes:
    body = _docx_body(markdown)
    document = (_XML + f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" '
                'w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>')
    wp = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    return _package({
        "[Content_Types].xml": _CT_HEAD
        + f'<Override PartName="/word/document.xml" ContentType="{wp}.document.main+xml"/>'
        + f'<Override PartName="/word/styles.xml" ContentType="{wp}.styles+xml"/>'
        + f'<Override PartName="/word/numbering.xml" ContentType="{wp}.numbering+xml"/>' + _CORE_CT + "</Types>",
        "_rels/.rels": _root_rels("word/document.xml"),
        "docProps/core.xml": _core(title),
        "word/document.xml": document,
        "word/styles.xml": _DOCX_STYLES,
        "word/numbering.xml": _DOCX_NUMBERING,
        "word/_rels/document.xml.rels": _XML + f'<Relationships xmlns="{PKG_REL}">'
        f'<Relationship Id="rId1" Type="{R}/styles" Target="styles.xml"/>'
        f'<Relationship Id="rId2" Type="{R}/numbering" Target="numbering.xml"/></Relationships>',
    })


_NUMBER = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


class _Strings:
    """The workbook's shared-string table, appended to as cells are written.

    Text goes here rather than into `t="inlineStr"` cells: OnlyOffice's
    converter keeps only the first inline-string cell of each row (verified
    with its own x2t on a generated file), and Excel itself always writes a
    shared table, so this is the form every reader handles.
    """

    def __init__(self, existing: "Optional[list[str]]" = None) -> None:
        self.items = list(existing or [])
        self._index = {t: i for i, t in enumerate(self.items)}
        self.added = False

    def index(self, text: str) -> int:
        if text not in self._index:
            self._index[text] = len(self.items)
            self.items.append(text)
            self.added = True
        return self._index[text]

    def xml(self) -> str:
        sis = "".join(f'<si><t xml:space="preserve">{escape(t)}</t></si>' for t in self.items)
        return _XML + f'<sst xmlns="{S}" count="{len(self.items)}" uniqueCount="{len(self.items)}">{sis}</sst>'


def _xlsx_cell(ref: str, value, style: int = 0, strings: "Optional[_Strings]" = None) -> str:
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{s}><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value!r}</v></c>'
    text = str(value)
    if text.startswith("=") and len(text) > 1:
        return f'<c r="{ref}"{s}><f>{escape(text[1:])}</f></c>'
    if _NUMBER.match(text.strip()) and not (len(text.strip()) > 1 and text.strip().startswith("0") and "." not in text):
        return f'<c r="{ref}"{s}><v>{text.strip()}</v></c>'
    if strings is not None:
        return f'<c r="{ref}" t="s"{s}><v>{strings.index(text)}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_name(name: str, used: set) -> str:
    clean = re.sub(r"[\[\]\*\?/\\:]", " ", str(name or "Sheet")).strip()[:31] or "Sheet"
    base, n = clean, 2
    while clean.lower() in used:
        clean = f"{base[:28]} {n}"
        n += 1
    used.add(clean.lower())
    return clean


def make_xlsx(sheets: "dict[str, list[list]]", header: bool = True) -> bytes:
    """One worksheet per entry; the first row bold when `header`; '=...' strings become formulas."""
    if not sheets:
        sheets = {"Sheet1": []}
    used: set = set()
    names = [_sheet_name(n, used) for n in sheets]
    strings = _Strings()
    parts = {}
    sheet_xml = []
    for i, rows in enumerate(sheets.values(), 1):
        out = []
        for r, row in enumerate(rows or [], 1):
            cells = "".join(_xlsx_cell(f"{col_letters(c)}{r}", v, 1 if header and r == 1 else 0, strings)
                            for c, v in enumerate(row or []))
            out.append(f'<row r="{r}">{cells}</row>')
        freeze = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                  'state="frozen"/></sheetView></sheetViews>') if header and rows else ""
        parts[f"xl/worksheets/sheet{i}.xml"] = (_XML + f'<worksheet xmlns="{S}" xmlns:r="{R}">{freeze}'
                                                f'<sheetData>{"".join(out)}</sheetData></worksheet>')
        sheet_xml.append(f'<sheet name="{escape(names[i - 1], {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>')
    n = len(names)
    sm = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    parts.update({
        "[Content_Types].xml": _CT_HEAD
        + f'<Override PartName="/xl/workbook.xml" ContentType="{sm}.sheet.main+xml"/>'
        + f'<Override PartName="/xl/styles.xml" ContentType="{sm}.styles+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{sm}.worksheet+xml"/>'
                  for i in range(1, n + 1))
        + f'<Override PartName="/xl/sharedStrings.xml" ContentType="{sm}.sharedStrings+xml"/>' + _CORE_CT + "</Types>",
        "xl/sharedStrings.xml": strings.xml(),
        "_rels/.rels": _root_rels("xl/workbook.xml"),
        "docProps/core.xml": _core(names[0]),
        "xl/workbook.xml": _XML + f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>{"".join(sheet_xml)}</sheets>'
        '<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>',
        "xl/_rels/workbook.xml.rels": _XML + f'<Relationships xmlns="{PKG_REL}">'
        + "".join(f'<Relationship Id="rId{i}" Type="{R}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                  for i in range(1, n + 1))
        + f'<Relationship Id="rId{n + 1}" Type="{R}/styles" Target="styles.xml"/>'
        + f'<Relationship Id="rId{n + 2}" Type="{R}/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
        "xl/styles.xml": _XML + f'<styleSheet xmlns="{S}">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
    })
    return _package(parts)


# PresentationML needs a master, a layout and a theme before a single slide is
# valid. These are the smallest ones PowerPoint, OnlyOffice and LibreOffice all
# accept: one master, two layouts (title slide, title + content), Office colours.
_THEME = (_XML + f'<a:theme xmlns:a="{A}" name="Chronoa"><a:themeElements>'
          '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
          '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F2937"/></a:dk2>'
          '<a:lt2><a:srgbClr val="E5E7EB"/></a:lt2><a:accent1><a:srgbClr val="2563EB"/></a:accent1>'
          '<a:accent2><a:srgbClr val="DC2626"/></a:accent2><a:accent3><a:srgbClr val="16A34A"/></a:accent3>'
          '<a:accent4><a:srgbClr val="9333EA"/></a:accent4><a:accent5><a:srgbClr val="EA580C"/></a:accent5>'
          '<a:accent6><a:srgbClr val="0891B2"/></a:accent6><a:hlink><a:srgbClr val="2563EB"/></a:hlink>'
          '<a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink></a:clrScheme>'
          '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/>'
          '<a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/>'
          '<a:cs typeface=""/></a:minorFont></a:fontScheme>'
          '<a:fmtScheme name="Office"><a:fillStyleLst>'
          + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
          + '</a:fillStyleLst><a:lnStyleLst>'
          + '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3
          + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3
          + '</a:effectStyleLst><a:bgFillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
          + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')

_NS_P = f'xmlns:a="{A}" xmlns:r="{R}" xmlns:p="{P}"'
_EMPTY_TREE = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
               '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
               '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


_EMPTY_PARA = '<a:p><a:endParaRPr lang="en-US"/></a:p>'


def _ph_shape(sid: int, name: str, ph: str, x: int, y: int, cx: int, cy: int, paras: str = "", idx: str = "") -> str:
    idx_attr = f' idx="{idx}"' if idx else ""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            f'<p:nvPr><p:ph type="{ph}"{idx_attr}/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
            f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>'
            f'{paras or _EMPTY_PARA}</p:txBody></p:sp>')


def _a_runs(text: str, size: int = 0) -> str:
    sz = f' sz="{size}"' if size else ""
    out = []
    for i, piece in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if piece:
            b = ' b="1"' if i % 2 else ""
            out.append(f'<a:r><a:rPr lang="en-US"{sz}{b} dirty="0"/><a:t>{escape(piece)}</a:t></a:r>')
    return "".join(out) or f'<a:endParaRPr lang="en-US"{sz}/>'


def make_pptx(slides: "list[dict]", title: str = "") -> bytes:
    """Slides of {title, bullets: [..], subtitle?}; the first slide with only a subtitle is a title slide."""
    if not slides:
        raise OfficeError("give at least one slide")
    W16, H9 = 12192000, 6858000
    master = (_XML + f'<p:sldMaster {_NS_P}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
              f'<p:spTree>{_EMPTY_TREE}'
              + _ph_shape(2, "Title", "title", 838200, 365125, 10515600, 1325563)
              + _ph_shape(3, "Body", "body", 838200, 1825625, 10515600, 4351338, idx="1")
              + '</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
              'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
              'hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
              '<p:sldLayoutId id="2147483650" r:id="rId2"/></p:sldLayoutIdLst>'
              '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="4000"><a:solidFill><a:schemeClr val="tx1"/>'
              '</a:solidFill><a:latin typeface="+mj-lt"/></a:defRPr></a:lvl1pPr></p:titleStyle><p:bodyStyle>'
              '<a:lvl1pPr marL="228600" indent="-228600"><a:buFont typeface="Arial"/><a:buChar char="•"/>'
              '<a:defRPr sz="2400"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill></a:defRPr></a:lvl1pPr>'
              '</p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles>'
              '</p:sldMaster>')

    def layout(kind: str, name: str, shapes: str) -> str:
        return (_XML + f'<p:sldLayout {_NS_P} type="{kind}" preserve="1"><p:cSld name="{name}"><p:spTree>'
                f'{_EMPTY_TREE}{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')

    title_layout = layout("title", "Title Slide",
                          _ph_shape(2, "Title", "ctrTitle", 1524000, 1122363, 9144000, 2387600)
                          + _ph_shape(3, "Subtitle", "subTitle", 1524000, 3602038, 9144000, 1655762, idx="1"))
    content_layout = layout("obj", "Title and Content",
                            _ph_shape(2, "Title", "title", 838200, 365125, 10515600, 1325563)
                            + _ph_shape(3, "Content", "body", 838200, 1825625, 10515600, 4351338, idx="1"))
    lrel = (_XML + f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{R}/slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/></Relationships>')
    pm = "application/vnd.openxmlformats-officedocument.presentationml"
    parts = {
        "ppt/slideMasters/slideMaster1.xml": master,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _XML + f'<Relationships xmlns="{PKG_REL}">'
        f'<Relationship Id="rId1" Type="{R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        f'<Relationship Id="rId2" Type="{R}/slideLayout" Target="../slideLayouts/slideLayout2.xml"/>'
        f'<Relationship Id="rId3" Type="{R}/theme" Target="../theme/theme1.xml"/></Relationships>',
        "ppt/slideLayouts/slideLayout1.xml": title_layout,
        "ppt/slideLayouts/slideLayout2.xml": content_layout,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": lrel,
        "ppt/slideLayouts/_rels/slideLayout2.xml.rels": lrel,
        "ppt/theme/theme1.xml": _THEME,
    }
    ids, prels, overrides = [], [], []
    for i, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            slide = {"title": str(slide)}
        heading = str(slide.get("title") or "")
        bullets = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
        subtitle = str(slide.get("subtitle") or "")
        is_title = bool(subtitle) or (i == 1 and not bullets)
        if is_title:
            shapes = (_ph_shape(2, "Title", "ctrTitle", 1524000, 1122363, 9144000, 2387600,
                                f"<a:p>{_a_runs(heading)}</a:p>")
                      + _ph_shape(3, "Subtitle", "subTitle", 1524000, 3602038, 9144000, 1655762,
                                  f"<a:p>{_a_runs(subtitle)}</a:p>", idx="1"))
        else:
            paras = "".join(f"<a:p>{_a_runs(b)}</a:p>" for b in bullets)
            shapes = (_ph_shape(2, "Title", "title", 838200, 365125, 10515600, 1325563, f"<a:p>{_a_runs(heading)}</a:p>")
                      + _ph_shape(3, "Content", "body", 838200, 1825625, 10515600, 4351338, paras, idx="1"))
        parts[f"ppt/slides/slide{i}.xml"] = (_XML + f'<p:sld {_NS_P}><p:cSld><p:spTree>{_EMPTY_TREE}{shapes}'
                                             '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = (
            _XML + f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{R}/slideLayout" '
            f'Target="../slideLayouts/slideLayout{1 if is_title else 2}.xml"/></Relationships>')
        ids.append(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>')
        prels.append(f'<Relationship Id="rId{i + 1}" Type="{R}/slide" Target="slides/slide{i}.xml"/>')
        overrides.append(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="{pm}.slide+xml"/>')
    n = len(slides)
    parts["ppt/presentation.xml"] = (
        _XML + f'<p:presentation {_NS_P} saveSubsetFonts="1"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
        f'</p:sldMasterIdLst><p:sldIdLst>{"".join(ids)}</p:sldIdLst><p:sldSz cx="{W16}" cy="{H9}"/>'
        '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
    parts["ppt/_rels/presentation.xml.rels"] = (
        _XML + f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{R}/slideMaster" '
        f'Target="slideMasters/slideMaster1.xml"/>{"".join(prels)}'
        f'<Relationship Id="rId{n + 2}" Type="{R}/theme" Target="theme/theme1.xml"/></Relationships>')
    parts["_rels/.rels"] = _root_rels("ppt/presentation.xml")
    parts["docProps/core.xml"] = _core(title or str((slides[0] or {}).get("title", "")) if isinstance(slides[0], dict) else title)
    parts["[Content_Types].xml"] = (
        _CT_HEAD + f'<Override PartName="/ppt/presentation.xml" ContentType="{pm}.presentation.main+xml"/>'
        f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="{pm}.slideMaster+xml"/>'
        f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="{pm}.slideLayout+xml"/>'
        f'<Override PartName="/ppt/slideLayouts/slideLayout2.xml" ContentType="{pm}.slideLayout+xml"/>'
        f'<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
        + "".join(overrides) + _CORE_CT + "</Types>")
    return _package(parts)


def rows_from_text(text: str) -> "list[list[str]]":
    """CSV/TSV or Markdown-table text to rows, for 'make a spreadsheet of this'."""
    import csv
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    if lines and all(l.strip().startswith("|") for l in lines):
        rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
        return [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(lines, dialect)]
