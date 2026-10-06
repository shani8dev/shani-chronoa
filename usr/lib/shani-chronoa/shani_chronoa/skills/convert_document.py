"""Skill: convert a document between formats - markdown, plain text, and HTML.
The document-side of convert_media.

No external converter (pandoc, libreoffice) is required: the conversions are
done in-process, so they work on any ShaniOS install. The result is a new file
beside the original; nothing is overwritten. Nothing leaves the machine.
"""

import re

from shani_chronoa import files
from shani_chronoa.skills import Skill

_DOCS = {".md", ".txt", ".html", ".htm"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_document",
        "description": (
            "Convert a document between markdown, plain text, and HTML. Writes "
            "a new file next to the original; never overwrites."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The document to convert."},
            "to": {"type": "string", "enum": ["md", "txt", "html"],
                     "description": "Target format."},
        }, "required": ["path", "to"]},
    },
}


def _to_html(text: str) -> str:
    import html as _html
    out = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append("<pre>" if in_code else "</pre>")
            continue
        if in_code:
            out.append(_html.escape(line))
            continue
        if not line.strip():
            out.append("")
        elif line.startswith("# "):
            out.append(f"<h1>{_html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{_html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{_html.escape(line[4:])}</h3>")
        elif line.startswith("- "):
            out.append(f"<li>{_html.escape(line[2:])}</li>")
        else:
            # inline markdown: **bold** and *italic*
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"\*(.+?)\*", r"<em>\1</em>", line)
            out.append(f"<p>{_html.escape(line)}</p>")
    return "\n".join(out)


def _to_md(text: str) -> str:
    # HTML -> markdown: strip tags, keep structure
    text = re.sub(r"<h1>(.*?)</h1>", r"# \1\n", text, flags=re.I | re.S)
    text = re.sub(r"<h2>(.*?)</h2>", r"## \1\n", text, flags=re.I | re.S)
    text = re.sub(r"<h3>(.*?)</h3>", r"### \1\n", text, flags=re.I | re.S)
    text = re.sub(r"<li>(.*?)</li>", r"- \1\n", text, flags=re.I | re.S)
    text = re.sub(r"<p>(.*?)</p>", r"\1\n", text, flags=re.I | re.S)
    text = re.sub(r"<pre>(.*?)</pre>", r"```\n\1\n```\n", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    import html as _html
    return _html.unescape(text).strip() + "\n"


def _run(arguments: dict) -> str:
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    if src.suffix.lower() not in _DOCS:
        return (f"{src.name} is not a document I convert - use convert_media for "
                f"this format ({', '.join(sorted(_DOCS))}).")
    to = (arguments.get("to") or "").lower().lstrip(".")
    if to not in ("md", "txt", "html"):
        return "to must be md, txt, or html."
    if src.suffix.lower() == f".{to}":
        return f"{src.name} is already {to}."

    try:
        text = src.read_text(encoding="utf-8")
    except OSError as e:
        return f"Could not read {src}: {e}"

    if to == "html":
        if src.suffix.lower() == ".md":
            body = _to_html(text)
        else:
            import html as _html
            body = "\n".join(f"<p>{_html.escape(p)}</p>" for p in text.split("\n\n") if p.strip())
        content = (f"<!DOCTYPE html>\n<html>\n<head><meta charset='utf-8'></head>\n"
                   f"<body>\n{body}\n</body>\n</html>\n")
    elif to == "md":
        if src.suffix.lower() in (".html", ".htm"):
            content = _to_md(text)
        else:
            content = text  # txt -> md is the same text
    else:  # txt
        if src.suffix.lower() == ".md":
            # strip markdown syntax
            text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
            text = re.sub(r"```", "", text)
            text = re.sub(r"^\s*-\s+", "", text, flags=re.M)
            content = text.strip() + "\n"
        else:
            import html as _html
            content = _html.unescape(re.sub(r"<[^>]+>", "", text)).strip() + "\n"

    target = src.with_suffix(f".{to}")
    n = 1
    while target.exists():
        target = src.with_name(f"{src.stem}-{n}.{to}")
        n += 1
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Could not write {target}: {e}"
    return f"Saved {target} ({target.stat().st_size} bytes) from {src.name}."


POST_CONDITION = None

SKILLS = [Skill(name="convert_document", schema=_SCHEMA, run=_run)]
