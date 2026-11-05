"""The printed edition as a PDF: Jinja2 templates turned into pages by WeasyPrint, with QR codes drawn by segno.

Both libraries are optional (`pip install 'research-agent[print]'`; WeasyPrint also needs the Pango system library). The rest of the program never imports
this module unless a PDF is asked for.

Fitting. A High paper's card must fit on one page whatever its length. Each card is laid out on its own first and measured; if it spills onto a second page
the next step of the fit ladder is tried, and the first step that fits is used:
    fit0  the normal spacing
    fit1  vertical gaps about 25% tighter
    fit2  about 40% tighter, shorter line height
    fit3  body text 14px (from 15px) and a smaller title
    fit4  the citation condensed to authors, year and journal, at most 4 chips, body text 13px (the floor: never smaller)
    fit5  at most 2 key findings, the smallest title
    flow  last resort: the card continues on the next page, headed "Continued"; no text is ever cut, and the DOI and QR code are always kept
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..domain.print_edition import Chip, limit_chips

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
FIT_LEVELS = ("fit0", "fit1", "fit2", "fit3", "fit4", "fit5")
CONDENSED_CHIPS = 4
CONDENSED_FINDINGS = 3
QR_COLOUR = "#0b2545"


class PrintUnavailable(RuntimeError):
    """WeasyPrint or segno is not installed (or WeasyPrint cannot find its system libraries)."""


@dataclass
class PrintPdf:
    pdf: bytes
    pages: int
    fit: list[dict] = field(default_factory=list)     # per High card: {"title", "level"} where level is fit0..fit5 or flow


def _load():
    try:
        import segno
        import weasyprint
    except (ImportError, OSError) as exc:
        raise PrintUnavailable("the printed edition needs the 'print' extra (pip install 'research-agent[print]') and the Pango library; on macOS with Homebrew set "
                               f"DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib ({type(exc).__name__}: {str(exc)[:120]})") from exc
    return segno, weasyprint


def css_string(value: str) -> str:
    """A text safe to put between double quotes in a CSS `content:` rule."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class PrintEditionRenderer:
    def __init__(self, directory: Path | None = None):
        self.segno, self.weasyprint = _load()
        self.env = Environment(loader=FileSystemLoader(directory or TEMPLATES), autoescape=lambda name: bool(name and name.endswith(".html.j2")),    # escape the HTML, never the stylesheet
                               trim_blocks=True, lstrip_blocks=True)
        self.env.filters["css"] = css_string

    # ------------------------------------------------------------------ pieces
    def qr_uri(self, url: str) -> str:
        """The QR code for a link as an inline image. Error correction M, a one-module quiet zone (the card's own white space is the rest)."""
        return self.segno.make(url, error="m", micro=False).svg_data_uri(scale=5, border=1, dark=QR_COLOUR, light="#ffffff")

    def _css(self, view: dict, last_page: int | None) -> str:
        return self.env.get_template("print_edition.css.j2").render(view=view, last_page=last_page)

    def _card(self, card: dict, level: str, kind: str = "full", flow: bool = False) -> str:
        return self.env.get_template("print_card.html.j2").render(card=card, level=level, flow=flow, kind=kind)

    def _html(self, html: str):
        """The document for WeasyPrint. The templates folder is its base address, so the stylesheet's `fonts/...` links find the fonts shipped with the program."""
        return self.weasyprint.HTML(string=html, base_url=str(TEMPLATES) + "/")

    def _pages(self, html: str) -> int:
        return len(self._html(html).render().pages)

    @staticmethod
    def condense(card: dict, level: int, max_chips: int) -> dict:
        """The card as it must read at a given fit level. Levels 0 to 3 change only the CSS; 4 and 5 shorten what is printed (nothing essential is dropped)."""
        c = copy.deepcopy(card)
        c["limit"] = max_chips
        if level >= 4:
            c["condensed"] = True            # the citation without the title (the heading already carries it)
            c["limit"] = min(max_chips, CONDENSED_CHIPS)
            if c.get("text"):
                c["text"]["key_findings"] = c["text"]["key_findings"][:CONDENSED_FINDINGS]
        if level >= 5 and c.get("text"):
            c["text"]["key_findings"] = c["text"]["key_findings"][:CONDENSED_FINDINGS - 1]
        return c

    @staticmethod
    def with_chips(card: dict) -> dict:
        shown, more = limit_chips([Chip(x["kind"], x["label"]) for x in card.get("chips", [])], card["limit"])
        card["shown_chips"], card["more_chips"] = [{"kind": x.kind, "label": x.label} for x in shown], more
        return card

    def fit_card(self, card: dict, view: dict) -> tuple[str, str]:
        """The card's HTML at the first fit level that keeps it on one page, and that level's name ('flow' when none does)."""
        css = self._css(view, None)
        for i, level in enumerate(FIT_LEVELS):
            html = self._card(self.with_chips(self.condense(card, i, view["max_chips"])), level)
            # the cover's first-page rule (no top margin) must not apply to the probe: in the real document a card is never on the first page
            probe = f"<html><head><style>{css} @page :first {{ margin-top: 17mm; }}</style></head><body>{html}</body></html>"
            if self._pages(probe) == 1:
                return html, level
        log.warning("card %r does not fit one page even at the tightest level: it will continue on the next page", card.get("title", "")[:60])
        return self._card(self.with_chips(self.condense(card, 5, view["max_chips"])), "fit5", flow=True), "flow"

    # ------------------------------------------------------------------ the document
    def render(self, view: dict) -> PrintPdf:
        """`view` is built by application/print_edition.py: plain dicts and strings only."""
        everyone = [*view["high"], *[c for _, cs in view["medium_groups"] for c in cs], *view["listed"], *view["retractions"]]
        for c in everyone:
            c["qr_uri"] = self.qr_uri(c["qr"]["url"]) if c.get("qr") else ""
        cards, fit = [], []
        for c in view["high"]:
            html, level = self.fit_card(c, view)
            cards.append(html)
            fit.append({"title": c["title"], "level": level})
        view["high_html"] = cards
        view["medium_html"] = [(g, [self._card(self.with_chips({**c, "limit": view["max_chips"]}), "fit0", kind="compact") for c in cs]) for g, cs in view["medium_groups"]]
        view["listed_html"] = [self._card({**c, "limit": view["max_chips"]}, "fit0", kind="row") for c in view["listed"]]
        view["retraction_html"] = [self._card({**c, "limit": view["max_chips"]}, "fit0", kind="retraction") for c in view["retractions"]]
        base = self.env.get_template("print_edition.html.j2")
        pages = self._pages(base.render(view=view, css=self._css(view, None)))     # first pass: how many pages, so the credit line can sit on the last one only
        rendered = self._html(base.render(view=view, css=self._css(view, pages))).render()
        return PrintPdf(rendered.write_pdf(), len(rendered.pages), fit)
