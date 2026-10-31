"""Full-text types, licence handling and the JATS (PubMed Central / Springer Nature) to plain-text converter."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .capabilities import Rights

OK, NOT_AVAILABLE, NOT_PERMITTED, ERROR = "ok", "not_available", "not_permitted", "error"
MIN_WORDS = 300  # anything shorter than this is an abstract, a stub or a failed conversion, not a paper

_CC = re.compile(r"creativecommons\.org/(?:licenses/(by(?:-nc)?(?:-nd|-sa)?)|publicdomain/zero)", re.I)


def licence_key(value: str | None) -> str | None:
    """Normalise a licence URL or label to cc-by, cc-by-nc, cc-by-nc-nd, cc-by-sa, cc-by-nd, cc0, or 'other'. None if unknown."""
    if not value:
        return None
    v = value.strip().lower()
    m = _CC.search(v)
    if m:
        return "cc0" if "zero" in m.group(0) else "cc-" + m.group(1)
    m = re.fullmatch(r"cc[-_ ]?(by(?:[-_ ]nc)?(?:[-_ ](?:nd|sa))?|0)(?:[-_ ]\d(?:\.\d)?)?", v)
    if m:
        return "cc0" if m.group(1) == "0" else "cc-" + m.group(1).replace("_", "-").replace(" ", "-")
    return "other"


@dataclass
class FullTextRequest:
    """Everything a provider may need to find one paper's text, taken from what the pipeline already knows."""

    paper_id: int
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    journal: str | None = None
    title: str = ""
    links: list[dict] = field(default_factory=list)   # {kind, url, is_free, licence}
    licences: list[str] = field(default_factory=list)

    @property
    def doi_prefix(self) -> str:
        return (self.doi or "").split("/", 1)[0]

    def free_pdf_links(self) -> list[dict]:
        return [l for l in self.links if l.get("kind") == "pdf" and l.get("is_free")]


@dataclass
class FullTextResult:
    outcome: str  # ok | not_available | not_permitted | error
    provider: str = ""
    text: str = ""
    format: str | None = None  # jats_xml | plain_text | pdf_text
    source_url: str | None = None
    licence_url: str | None = None
    licence: str | None = None  # normalised key
    rights: Rights = Rights.UNKNOWN
    detail: str = ""
    abstract: str | None = None         # an abstract found in the text itself (used only when the paper has none)
    abstract_kind: str | None = None    # jats | pdf_abstract | pdf_summary | pdf_structured | pdf_intro

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class FullTextPolicy:
    """Which retrieved text goes in the print edition.

    Default (owner decision 2026-10-31): licences do not limit printing, so any full text we hold is printable. The licence, rights class and provider are
    still recorded for every text. `regardless_of_licence=False` restores the cautious rules (only the listed licences, and paid access only if allowed),
    which another profile may want.
    """

    regardless_of_licence: bool = True
    print_licences: tuple[str, ...] = ("cc-by", "cc0")      # used only when regardless_of_licence is False
    allow_entitled_in_print: bool = False                    # used only when regardless_of_licence is False
    store_entitled_text: bool = True                         # keep text obtained through a paid entitlement

    def print_allowed(self, r: FullTextResult) -> bool:
        if self.regardless_of_licence:
            return True
        if r.licence in self.print_licences:
            return True
        return r.rights == Rights.ENTITLED and self.allow_entitled_in_print and r.licence is None


# --------------------------------------------------------------------------- JATS

@dataclass
class JatsDoc:
    title: str
    text: str
    licence_url: str | None
    has_body: bool
    abstract: str = ""

    @property
    def words(self) -> int:
        return len(self.text.split())


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for k in list(el.attrib):
            if "}" in k:
                el.attrib[k.split("}", 1)[1]] = el.attrib.pop(k)
    return root


_KEEP_XREF = {"fig", "table", "supplementary-material", "sec", "boxed-text", "disp-formula"}


def _inline(el: ET.Element) -> str:
    """Text of an element, keeping 'Figure 1'-style cross references and dropping citation numbers."""
    parts = [el.text or ""]
    for child in el:
        tag = child.tag
        if tag == "xref" and child.get("ref-type") not in _KEEP_XREF:
            pass
        elif tag in ("inline-formula", "disp-formula"):
            tex = child.find(".//tex-math")
            alt = child.find(".//alt-text")
            parts.append((tex.text if tex is not None and tex.text else None) or (alt.text if alt is not None and alt.text else None) or "".join(child.itertext()))
        elif tag in ("fig", "table-wrap", "supplementary-material"):
            pass  # handled as blocks
        else:
            parts.append(_inline(child))
        parts.append(child.tail or "")
    return "".join(parts)


_EMPTY_BRACKETS = re.compile(r"\s*[\[(]\s*[,;\u2013\u2014\-\s]*[\])]")


def _clean(s: str) -> str:
    """Collapse whitespace and tidy what removed citation markers leave behind ('perspectives [] define', 'results , which')."""
    s = _EMPTY_BRACKETS.sub("", re.sub(r"\s+", " ", s))
    return re.sub(r"\s+([,.;:])", r"\1", s).strip()


def _caption(block: ET.Element, label: str) -> str:
    lab = _clean("".join(block.find("label").itertext())) if block.find("label") is not None else label
    cap = block.find("caption")
    text = _clean(_inline(cap)) if cap is not None else ""
    return f"[{lab}: {text}]" if text else f"[{lab}]"


def _blocks(sec: ET.Element, depth: int, out: list[str]) -> None:
    for child in sec:
        tag = child.tag
        if tag == "title":
            continue
        if tag == "sec" or tag == "boxed-text":
            t = child.find("title")
            if t is not None and _clean(_inline(t)):
                out.append("#" * min(depth + 1, 4) + " " + _clean(_inline(t)))
            _blocks(child, depth + 1, out)
        elif tag == "p":
            txt = _clean(_inline(child))
            if txt:
                out.append(txt)
            for sub in child.iter():
                if sub.tag == "fig":
                    out.append(_caption(sub, "Figure"))
                elif sub.tag == "table-wrap":
                    out.append(_caption(sub, "Table"))
        elif tag == "fig":
            out.append(_caption(child, "Figure"))
        elif tag == "table-wrap":
            out.append(_caption(child, "Table"))
        elif tag == "list":
            for item in child.findall("list-item"):
                txt = _clean(_inline(item))
                if txt:
                    out.append("- " + txt)
        elif tag in ("disp-quote", "statement"):
            txt = _clean(_inline(child))
            if txt:
                out.append(txt)


def jats_to_text(xml_text: str) -> JatsDoc | None:
    """Convert a JATS article (also inside a wrapper element such as Springer's <response>) to plain text. None if no article is present."""
    try:
        root = _strip_ns(ET.fromstring(xml_text))
    except ET.ParseError:
        return None
    article = root if root.tag == "article" else root.find(".//article")
    if article is None:
        return None
    title_el = article.find(".//article-meta/title-group/article-title")
    title = _clean("".join(title_el.itertext())) if title_el is not None else ""
    lic = None
    for cand in article.findall(".//permissions//license"):
        lic = cand.get("href") or next((e.get("href") for e in cand.iter("ext-link") if e.get("href")), None) or lic
        if lic and "creativecommons" in lic:
            break
    ref = article.find(".//license_ref")
    if ref is not None and ref.text and (lic is None or "creativecommons" not in lic):
        lic = ref.text.strip()
    body = article.find("body")
    out: list[str] = []
    if body is not None:
        _blocks(body, 1, out)
    return JatsDoc(title=title, text="\n\n".join(out), licence_url=lic, has_body=body is not None and bool(out), abstract=_jats_abstract(article))


def _jats_abstract(article: ET.Element) -> str:
    """The article's own abstract, in the 'LABEL: text' form PubMed uses for structured ones."""
    for ab in article.findall(".//article-meta/abstract"):
        if ab.get("abstract-type") in ("graphical", "teaser", "toc", "key-points", "web-summary"):
            continue
        parts = []
        secs = ab.findall("sec")
        for sec in secs or [ab]:
            t = sec.find("title")
            body = " ".join(_clean(_inline(p)) for p in sec.findall("p") if _clean(_inline(p)))
            if body:
                parts.append(f"{_clean(_inline(t)).upper()}: {body}" if (secs and t is not None and _clean(_inline(t))) else body)
        text = "\n".join(parts).strip()
        if text:
            return text
    return ""


# --------------------------------------------------------------------------- abstract recovery from PDF text
_FRONT_CHARS = 15000   # about the first three pages
_STOP = re.compile(r"^\s*(?:key\s*words?\b|keywords?\b|abbreviations?\b|(?:\d+\.?\s*|[ivx]+\.\s*)?introduction\s*$|(?:1\.?\s+)introduction\b|correspond|"
                   r"(?:received|accepted|published|revised)\b\s*(?:on\b|online\b|date\b|:|\d)|\u00a9|copyright|https?://|doi\b|figure\s+\d|table\s+\d)", re.I)
_ABSTRACT_HEAD = re.compile(r"^\s*(abstract|summary|synopsis)\s*(?:[:.\u2014\u2013-]\s*(.*)|$)", re.I)
_ABSTRACT_RUN_IN = re.compile(r"^\s*((?i:abstract))\s+(?=[A-Z])(.*)$")
_STRUCT_START = re.compile(r"^\s*(?:background|objectives?|aims?|purpose)\b\s*[:.\u2014\u2013-]?\s*\S", re.I)
_STRUCT_LATER = re.compile(r"^\s*(?:methods?|results?|conclusions?|findings|interpretation)\b\s*[:.\u2014\u2013-]?\s*\S", re.I)
_KEYWORDS_LINE = re.compile(r"^\s*(?:key\s*words?|keywords?)\b\s*[:\-]?", re.I)
_BACK_STOP = re.compile(r"^\s*(?:received\b|accepted\b|published\b|citation\b|copyright|\u00a9|open access\b|edited by\b|reviewed by\b|\*?correspondence\b|"
                        r"type\s+\w+(?:\s+\w+)?\s*$|\d+\s*[A-Z][^.]{0,120}(?:university|hospital|institute|department|faculty|school|college|cent(?:er|re)|laborator)|[^\s]+@[^\s]+)", re.I)
_INSTITUTION = re.compile(r"\b(?:university|hospital|institute|department|faculty|school of|college|cent(?:er|re)|laborator|china|usa|italy|germany|india|spain|france|japan|denmark|brazil)\b", re.I)
_INTRO_HEAD = re.compile(r"^\s*(?:\d+\.?\s*|[ivx]+\.\s*)?introduction\s*$", re.I)
MIN_ABSTRACT_WORDS = 40


def _join_lines(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln          # a word hyphenated at the end of a line
        else:
            out = f"{out} {ln}" if out else ln
    return re.sub(r"\s+", " ", out).strip()


def _collect(lines: list[str], start: int, first: str = "", *, max_chars: int = 3000, paragraphs: int | None = None) -> str:
    got = [first] if first else []
    paras = 0
    size = len(first)
    for ln in lines[start:]:
        if _STOP.match(ln) and (got or paras):
            break
        if not ln.strip():
            if paragraphs is not None and got:
                paras += 1
                if paras >= paragraphs:
                    break
            continue
        got.append(ln)
        size += len(ln) + 1
        if size > max_chars:
            break
    return _join_lines(got)


def extract_abstract(text: str) -> tuple[str, str] | None:
    """Find an abstract in the first pages of a paper's text. Returns (abstract, kind) or None.

    Order: an 'Abstract' heading; a 'Summary' heading; a structured Background/Methods/Results/Conclusions block with no heading; the opening of the
    Introduction as a last resort. Layout varies a lot between journals, so this is a heuristic for the rare paper with no abstract anywhere else, and
    anything under 40 words is rejected rather than guessed.
    """
    lines = text[:_FRONT_CHARS].splitlines()
    for kind, pattern in (("pdf_abstract", "abstract"), ("pdf_summary", "summary")):
        for i, ln in enumerate(lines):
            m = _ABSTRACT_HEAD.match(ln) or (_ABSTRACT_RUN_IN.match(ln) if pattern == "abstract" else None)
            if not m or m.group(1).lower() != pattern:
                continue
            first = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
            if pattern == "summary" and not first and ln.strip().lower() != "summary":
                continue
            body = _collect(lines, i + 1, first)
            if len(body.split()) >= MIN_ABSTRACT_WORDS:
                return body, kind
    for i, ln in enumerate(lines):             # structured abstract without an 'Abstract' heading
        if _STRUCT_START.match(ln) and any(_STRUCT_LATER.match(x) for x in lines[i + 1:i + 40]):
            body = _collect(lines, i + 1, ln.strip())
            if len(body.split()) >= MIN_ABSTRACT_WORDS:
                return body, "pdf_structured"
    for i, ln in enumerate(lines):             # no heading: the paragraph block just before the Keywords line
        if _KEYWORDS_LINE.match(ln) and i > 3:
            block: list[str] = []
            for back in reversed(lines[max(0, i - 80):i]):
                if _BACK_STOP.match(back) or (back.strip() and len(back.strip()) <= 2):
                    break
                block.append(back)
                if sum(len(x) for x in block) > 3500:
                    break
            block.reverse()
            while block and (not block[0].strip() or (len(block[0].strip()) < 70 and not block[0].rstrip().endswith((".", ":"))) or
                             (_INSTITUTION.search(block[0]) and len(block[0].strip()) < 110)):
                block.pop(0)               # leftovers of the address block that sits above the abstract
            body = _join_lines(block)
            if len(body.split()) >= MIN_ABSTRACT_WORDS:
                return body, "pdf_abstract"
    for i, ln in enumerate(lines):             # last resort: the first paragraphs of the Introduction
        if _INTRO_HEAD.match(ln):
            body = _collect(lines, i + 1, max_chars=1400, paragraphs=2)
            if len(body.split()) >= MIN_ABSTRACT_WORDS:
                return body, "pdf_intro"
    return None


# --------------------------------------------------------------------------- does this text belong to this paper?
def _words(text: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if len(w) > 3]


def text_matches_paper(text: str, title: str, doi: str | None, *, front_chars: int = 9000, threshold: float = 0.6) -> bool:
    """True if the text plausibly is the paper: its DOI appears in the first pages, or most of the title's words do. A link supplied by an index can point at the
    wrong article (OpenAlex did, in testing). When the title is too short to judge and the DOI is absent, the text is given the benefit of the doubt."""
    front = text[:front_chars]
    if doi and doi.lower() in front.lower().replace("doi:", "").replace("https://doi.org/", "").replace(" ", ""):
        return True
    words = set(_words(title or ""))
    if len(words) < 3:
        return True
    have = set(_words(front))
    return len(words & have) / len(words) >= threshold
