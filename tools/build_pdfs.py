"""Render the two report markdowns to PDF.

We avoid pandoc + LaTeX (huge install) and use markdown + weasyprint
with two CSS stylesheets — one academic (serif, paragraph numbering,
tight margins), one editorial (sans-serif, wider type, looser
leading). Charts are embedded as PNGs already rendered by
``tools/render_charts.py``.
"""
from __future__ import annotations

from pathlib import Path

import markdown
from weasyprint import CSS, HTML


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


SCIENTIFIC_CSS = """
@page {
    size: A4;
    margin: 2.2cm 2cm 2.4cm 2cm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-family: "Times New Roman", Times, serif;
        font-size: 9pt;
        color: #555;
    }
}
body {
    font-family: "Times New Roman", Times, serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #111;
    counter-reset: figure;
}
h1 {
    font-size: 17pt;
    margin-top: 0;
    margin-bottom: 0.4em;
    line-height: 1.2;
    border-bottom: 2px solid #111;
    padding-bottom: 0.2em;
}
h2 {
    font-size: 12pt;
    margin-top: 1.6em;
    margin-bottom: 0.5em;
    page-break-after: avoid;
}
h3 {
    font-size: 11pt;
    margin-top: 1.2em;
    margin-bottom: 0.3em;
    page-break-after: avoid;
}
p {
    margin: 0 0 0.6em;
    text-align: justify;
}
img {
    max-width: 100%;
    display: block;
    margin: 0.6em auto;
}
img + em, p:has(+ img) {
    text-align: center;
}
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 9.5pt;
    margin: 0.8em 0 1em;
}
th, td {
    border-bottom: 1px solid #999;
    padding: 4px 6px;
    text-align: left;
}
th {
    border-bottom: 1.5px solid #111;
    background: #f7f7f0;
}
code {
    font-family: "Menlo", "Consolas", monospace;
    font-size: 9pt;
    background: #f0f0e8;
    padding: 1px 3px;
    border-radius: 2px;
}
ol, ul {
    margin: 0.4em 0 0.7em 1.4em;
}
li {
    margin-bottom: 0.2em;
}
hr {
    border: none;
    border-top: 1px solid #999;
    margin: 1em 0;
}
"""

GENERAL_CSS = """
@page {
    size: A4;
    margin: 2.5cm 2.5cm 2.5cm 2.5cm;
    @bottom-right {
        content: counter(page);
        font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
        font-size: 9pt;
        color: #5B5BD6;
    }
    @bottom-left {
        content: "Lighthouse · Harbor Gang";
        font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
        font-size: 9pt;
        color: #999;
    }
}
body {
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.55;
    color: #222;
    max-width: 100%;
}
h1 {
    font-size: 22pt;
    font-weight: 700;
    margin: 0 0 0.6em;
    line-height: 1.1;
    color: #111;
}
h2 {
    font-size: 14pt;
    font-weight: 700;
    margin-top: 1.8em;
    margin-bottom: 0.5em;
    color: #111;
    page-break-after: avoid;
}
h3 {
    font-size: 11.5pt;
    font-weight: 600;
    margin-top: 1em;
    color: #333;
    page-break-after: avoid;
}
p {
    margin: 0 0 0.7em;
}
strong {
    color: #111;
}
img {
    max-width: 100%;
    display: block;
    margin: 1em auto;
}
ul, ol {
    margin: 0.4em 0 0.9em 1.4em;
}
li {
    margin-bottom: 0.3em;
}
code {
    font-family: "Menlo", "Consolas", monospace;
    font-size: 10pt;
    background: #f4f4f8;
    padding: 1px 4px;
    border-radius: 3px;
    color: #444;
}
pre {
    background: #f4f4f8;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 10pt;
    overflow-x: auto;
}
hr {
    border: none;
    border-top: 1px solid #ddd;
    margin: 1.4em 0;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.8em 0 1em;
    font-size: 10pt;
}
th, td {
    border-bottom: 1px solid #e5e5e5;
    padding: 6px 8px;
    text-align: left;
}
th {
    background: #f7f7fb;
    color: #5B5BD6;
    font-weight: 700;
}
blockquote {
    border-left: 3px solid #5B5BD6;
    padding-left: 12px;
    margin-left: 0;
    color: #555;
}
"""


def render(md_path: Path, css_text: str, pdf_path: Path) -> None:
    html_body = markdown.markdown(
        md_path.read_text(),
        extensions=["tables", "fenced_code", "attr_list"],
    )
    full_html = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{md_path.stem}</title></head><body>{html_body}</body></html>"""
    HTML(string=full_html, base_url=str(REPORTS)).write_pdf(
        target=str(pdf_path),
        stylesheets=[CSS(string=css_text)],
    )
    print(f"wrote {pdf_path.relative_to(ROOT)}  ({pdf_path.stat().st_size // 1024} KB)")


def main():
    render(REPORTS / "report_scientific.md", SCIENTIFIC_CSS, REPORTS / "lighthouse-evals-scientific.pdf")
    render(REPORTS / "report_general.md", GENERAL_CSS, REPORTS / "lighthouse-evals-general.pdf")


if __name__ == "__main__":
    main()
