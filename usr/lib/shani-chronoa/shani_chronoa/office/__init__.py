"""Word, Excel and PowerPoint files - and their OpenDocument cousins - with the standard library.

Both images ship OnlyOffice (a Flatpak) for people, and nothing a skill can
script: no LibreOffice, no python-docx/openpyxl/python-pptx. A .docx, .xlsx
or .pptx is a zip of XML parts (ECMA-376 / ISO 29500), and .odt/.ods/.odp is
a zip with a `content.xml` (ODF 1.2), so `zipfile` + `xml.etree` read all
six and write the three OOXML ones without adding a dependency. Harvested
from goose's computercontroller `docx_tool` / `xlsx_tool` (whose operations
- read a range, update a cell, append or replace text, structured create -
this mirrors) but written for Chronoa's constraints.

What is deliberately *not* here, so nobody promises it:
- the legacy binary formats (.doc/.xls/.ppt) - say so and suggest opening and
  re-saving them in OnlyOffice;
- recalculation: a formula written to a cell is stored, and the workbook is
  marked to recalculate when it is opened, but no value is computed here;
- styling beyond headings, bold, bullets, numbered lists, tables and a bold
  header row - the goal is a correct document someone then polishes, not a
  layout engine.

Edits keep everything they do not understand. The one change an edit makes
beyond the cell or text asked for is dropping `xl/calcChain.xml`, a cache
Excel rebuilds, because a stale chain is what makes Excel offer "repair".
Namespace prefixes the parser would otherwise rename are put back (see
`_serialize`), because `mc:Ignorable` names prefixes by string and a renamed
prefix makes Office reject the file.
"""

# The package's public API: every name the module had, from where it now lives.
from .common import (  # noqa: F401
    A,
    CT,
    DRAW,
    LEGACY,
    MAX_PART,
    OFFICE,
    OfficeError,
    P,
    PKG_REL,
    R,
    READABLE,
    S,
    TABLE,
    TEXT,
    W,
    WRITABLE,
    _XML,
    _open,
    _q,
    _rels,
    _xml,
    col_letters,
    parse_ref,
)
from .read import (  # noqa: F401
    _a_paragraphs,
    _cell_value,
    _docx_blocks,
    _odf_blocks,
    _odf_text,
    _pptx_blocks,
    _shared_strings,
    _w_text,
    _xlsx_blocks,
    _xlsx_sheets,
    read,
    read_xlsx_rows,
)
from .write import (  # noqa: F401
    _CORE_CT,
    _CT_HEAD,
    _DOCX_NUMBERING,
    _DOCX_STYLES,
    _EMPTY_PARA,
    _EMPTY_TREE,
    _NS_P,
    _NUMBER,
    _Strings,
    _THEME,
    _a_runs,
    _core,
    _docx_body,
    _package,
    _ph_shape,
    _root_rels,
    _runs,
    _sheet_name,
    _xlsx_cell,
    make_docx,
    make_pptx,
    make_xlsx,
    rows_from_text,
)
from .edit import (  # noqa: F401
    _declared,
    _drop_calc_chain,
    _rewrite,
    _serialize,
    docx_append,
    docx_replace,
    iter_parts,
    set_cells,
)
