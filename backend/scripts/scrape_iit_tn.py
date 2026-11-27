"""Official polite web scraper for Institut International de Technologie (`https://iit.tn/`).

Component type: **deterministic code** (HTTP client, `robots.txt` validator, HTML-to-Markdown
cleaner, canonical URL deduplicator, and SHA-256 corpus manifest builder) — NOT an AI agent.

Strict behavioral & governance guarantees:
1. Checks `https://iit.tn/robots.txt` via `urllib.robotparser` BEFORE fetching any page.
2. Never solves, bypasses, waits out, or circumvents any CAPTCHA, Cloudflare Turnstile, or
   anti-bot challenge: aborts immediately if a challenge marker or 403/429 block is encountered.
3. Rate-limits politely (`--delay 0.6` seconds between requests by default).
4. Uses `beautifulsoup4` when installed (Docker runtime) with a deterministic stdlib
   `html.parser` DOM fallback so host environments without `bs4` produce identical output.
5. Deduplicates WordPress `-2` revisions, `<link rel="canonical">` aliases, and identical
   content bodies while excluding WordPress placeholder/junk slugs (`/page-d-exemple/`, etc.).
6. Preserves unchanged `.md` files idempotently across re-runs when `body_sha256` has not
   changed, preventing spurious timestamp churn in Git and `SA-56` sync jobs.

Usage:
    python backend/scripts/scrape_iit_tn.py
    docker compose exec backend python scripts/scrape_iit_tn.py
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx

try:
    from bs4 import BeautifulSoup, Comment, NavigableString, Tag

    HAS_BS4 = True
except ImportError:  # pragma: no cover - exercised on host Python without bs4
    BeautifulSoup = None  # type: ignore[assignment,misc]
    Comment = None  # type: ignore[assignment,misc]
    NavigableString = None  # type: ignore[assignment,misc]
    Tag = None  # type: ignore[assignment,misc]
    HAS_BS4 = False

logger = logging.getLogger("scrape_iit_tn")

IIT_BASE_URL = "https://iit.tn"
IIT_ROBOTS_URL = "https://iit.tn/robots.txt"
IIT_PAGE_SITEMAP_URL = "https://iit.tn/page-sitemap.xml"
DEFAULT_USER_AGENT = "SmartCampusCorpusBot/1.0 (+https://iit.tn; academic-rag-indexing)"

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "corpus" / "iit"
MIN_BODY_CHARS = 95

# Curated official IIT pages linked from https://iit.tn/formation/ and navigation menus
CURATED_SEED_URLS: tuple[str, ...] = (
    "https://iit.tn/",
    "https://iit.tn/formation/",
    "https://iit.tn/prepa/",
    "https://iit.tn/filieres-ingenieur/",
    "https://iit.tn/filieres-ingenieur/genie-informatique-2/",
    "https://iit.tn/filieres-ingenieur/genie-civil/",
    "https://iit.tn/filieres-ingenieur/genie-industriel-2/",
    "https://iit.tn/filieres-ingenieur/genie-mecanique-2/",
    "https://iit.tn/genie-des-procedes/",
    "https://iit.tn/architecture-2/",
    "https://iit.tn/licences/",
    "https://iit.tn/genie-logiciel-systemes-intelligents/",
    "https://iit.tn/big-data-et-analyse-de-donnees/",
    "https://iit.tn/reseaux-et-cybersecurite/",
    "https://iit.tn/systemes-embarques-internet-des-objets-iot/",
    "https://iit.tn/licence-en-mecatronique-systemes-intelligents/",
    "https://iit.tn/licence-en-systemes-electriques-intelligents-energies-renouvelables/",
    "https://iit.tn/licences/licence-en-management-des-systemes-industriels/",
    "https://iit.tn/mastere/",
    "https://iit.tn/formation-continue/",
    "https://iit.tn/candidature/",
    "https://iit.tn/test/inscription/",
    "https://iit.tn/test/procedure-et-frais-dinscription/",
    "https://iit.tn/test/bourse/",
    "https://iit.tn/test/guide-etudiants-reglement-interieur/",
    "https://iit.tn/a-propos/",
    "https://iit.tn/a-propos/accreditations/",
    "https://iit.tn/a-propos/missions-visions/",
    "https://iit.tn/a-propos/politique-qualite/",
    "https://iit.tn/a-propos/valeurs-de-linstitut/",
    "https://iit.tn/groupe-nau/",
    "https://iit.tn/campus/",
    "https://iit.tn/international-iit/",
    "https://iit.tn/international-iit/mobilite-internationale/",
    "https://iit.tn/international-iit/partenaires/",
    "https://iit.tn/stage/",
    "https://iit.tn/recherche/",
    "https://iit.tn/vie-estudiantine-2/",
    "https://iit.tn/cellule-decoute/",
    "https://iit.tn/alumni/",
    "https://iit.tn/contact-3/",
)

# WordPress placeholder, admin, template, or duplicate draft slugs excluded from RAG indexing.
# Note: /test/ (exact) is an empty menu container, whereas /test/inscription/, /test/bourse/,
# /test/procedure-et-frais-dinscription/, and /test/guide-etudiants-reglement-interieur/ are
# genuine official IIT admission & fee pages and remain INCLUDED.
EXCLUDED_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/page-d-exemple/",
        "/3256-2/",
        "/sleh/",
        "/about-me/",
        "/footer/",
        "/intranet/",
        "/acceuil/",
        "/acceuil-2/",
        "/test/",
        "/campus-3d/",
        # Unedited Avantage WordPress theme demo page (London/Toronto dummy offices)
        "/candidature/",
        "/wp-job-portal-jobseeker-controlpanel/",
        "/candidature-pour-la-competition-du-meilleur-licencie-iit/",
        "/international-iit/a-propos-2/",
        "/international-iit/projet/",
    }
)

# Superseded WordPress v1 slugs mapped to their active navigation-menu v2 counterparts.
SUPERSEDED_BY_ACTIVE_PATH: dict[str, str] = {
    "/filieres-ingenieur/genie-informatique/": "/filieres-ingenieur/genie-informatique-2/",
    "/filieres-ingenieur/genie-mecanique/": "/filieres-ingenieur/genie-mecanique-2/",
    "/filieres-ingenieur/genie-industriel/": "/filieres-ingenieur/genie-industriel-2/",
    "/filieres-ingenieur/ingenierie-genie-civil/": "/filieres-ingenieur/genie-civil/",
    "/filieres-ingenieur/genie-des-procedes/": "/genie-des-procedes/",
    "/filieres-ingenieur/cycle-preparatoire/": "/prepa/",
    "/architecture/": "/architecture-2/",
    "/masters/": "/mastere/",
    "/vie-estudiantine/": "/vie-estudiantine-2/",
    "/licence-genie-logiciel-et-systheme-dinformation/": (
        "/genie-logiciel-systemes-intelligents/"
    ),
    "/licences/genie-logiciel-et-systeme-dinformation/": (
        "/genie-logiciel-systemes-intelligents/"
    ),
    "/licences/management-des-systemes-industriels/": (
        "/licences/licence-en-management-des-systemes-industriels/"
    ),
}

ANTI_BOT_MARKERS: tuple[str, ...] = (
    "cf-turnstile",
    "challenge-platform",
    "verify you are human",
    "g-recaptcha",
    "hcaptcha",
    "just a moment...",
    "attention required! | cloudflare",
)

VOID_HTML_TAGS: frozenset[str] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

STRIP_TAGS: frozenset[str] = frozenset(
    {
        "head",
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "video",
        "audio",
        "nav",
        "footer",
        "aside",
        "form",
        "button",
        "select",
        "textarea",
        "input",
    }
)

_BOILERPLATE_ATTR_RE = re.compile(
    r"(^|[\s_-])("
    r"site-header|site-footer|btsiteheader|btsitefooter|mainheader|"
    r"ast-mobile-popup|ast-header|ast-footer|bt_bb_headline_superheadline|"
    r"elementor-location-header|elementor-location-footer|elementor-nav-menu|"
    r"menu-item|sub-menu|wpadminbar|cookie|gdpr|cmplz|moove-gdpr|"
    r"share-buttons|social-icons|breadcrumb|elementor-hidden-desktop"
    r")($|[\s_-])",
    re.IGNORECASE,
)

_NOISY_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^copyright\s+by\s+iit\b.*$", re.IGNORECASE),
    re.compile(r"^all\s+rights\s+reserved\.?$", re.IGNORECASE),
    re.compile(r"^(lire\s+la\s+suite|en\s+savoir\s+plus|read\s+more)\s*[»>→]*$", re.IGNORECASE),
    re.compile(r"^(menu|fermer|close|search|rechercher)$", re.IGNORECASE),
)

# Maps canonical IIT URL paths to core.program.code when a 1-to-1 seed program exists.
PATH_TO_PROGRAM_CODE: dict[str, str] = {
    "/prepa/": "IIT-PREPA",
    "/filieres-ingenieur/genie-civil/": "IIT-ING-CIVIL",
    "/filieres-ingenieur/genie-industriel-2/": "IIT-ING-INDUS",
    "/filieres-ingenieur/genie-mecanique-2/": "IIT-ING-MECA",
    "/genie-logiciel-systemes-intelligents/": "IIT-LIC-INFO",
}


class AntiBotGateDetectedError(RuntimeError):
    """Raised immediately if an anti-bot challenge or rate-limit block is encountered."""


@dataclass
class DomNode:
    """Parser-agnostic HTML node used by both BeautifulSoup and stdlib HTMLParser paths."""

    tag: str | None  # None represents a text node
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[DomNode] = field(default_factory=list)
    text: str = ""


@dataclass
class ScrapedCorpusEntry:
    """Metadata record persisted in `manifest.json` for each scraped IIT page."""

    code: str
    slug: str
    file_name: str
    title: str
    source_url: str
    scraped_at: str
    lastmod: str | None
    school: str
    doc_type: str
    target_program_code: str | None
    body_sha256: str
    sha256: str
    char_count: int
    word_count: int
    pdf_links: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# URL Normalization, Classification & Sitemap Discovery
# ---------------------------------------------------------------------------


def normalize_iit_url(raw_url: str, *, base_url: str = IIT_BASE_URL) -> str | None:
    """Normalize an `iit.tn` URL to canonical `https://iit.tn/<path>/` form."""
    cleaned = raw_url.strip()
    if not cleaned or cleaned.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    resolved = urljoin(base_url, cleaned)
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "iit.tn":
        return None

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    lower_path = path.lower()
    if any(
        lower_path.endswith(ext)
        for ext in (
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".svg",
            ".mp4",
            ".mp3",
            ".zip",
            ".doc",
            ".docx",
            ".css",
            ".js",
            ".xml",
            ".json",
        )
    ):
        return None
    if any(
        seg in lower_path
        for seg in (
            "/wp-content/",
            "/wp-includes/",
            "/wp-admin/",
            "/wp-json/",
            "/feed/",
            "/gsm_block/",
        )
    ):
        return None
    if not path.endswith("/"):
        path = f"{path}/"
    return urlunparse(("https", "iit.tn", path, "", "", ""))


def is_excluded_iit_url(url: str) -> bool:
    """Return True if the URL is a WordPress placeholder, admin stub, or superseded draft."""
    norm = normalize_iit_url(url)
    if norm is None:
        return True
    path = urlparse(norm).path.lower()
    if path in EXCLUDED_EXACT_PATHS:
        return True
    if path in SUPERSEDED_BY_ACTIVE_PATH:
        return True
    return False


def detect_anti_bot_gate(status_code: int, html_text: str) -> str | None:
    """Return a reason string if the response indicates a CAPTCHA, Turnstile, or WAF block."""
    if status_code in {403, 429, 503}:
        return f"HTTP status {status_code}"
    lower = html_text[:60000].lower()
    for marker in ANTI_BOT_MARKERS:
        if marker in lower:
            return f"anti-bot marker '{marker}'"
    return None


def parse_sitemap_xml(xml_text: str) -> dict[str, str | None]:
    """Parse Yoast `<url><loc>...</loc><lastmod>...</lastmod></url>` entries."""
    discovered: dict[str, str | None] = {}
    for block in re.findall(r"<url\b[^>]*>(.*?)</url>", xml_text, flags=re.I | re.S):
        loc_m = re.search(r"<loc>\s*(.*?)\s*</loc>", block, flags=re.I | re.S)
        if not loc_m:
            continue
        norm_url = normalize_iit_url(html_lib.unescape(loc_m.group(1)))
        if norm_url is None:
            continue
        lastmod_m = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, flags=re.I | re.S)
        lastmod = html_lib.unescape(lastmod_m.group(1)).strip() if lastmod_m else None
        discovered[norm_url] = lastmod

    if not discovered:
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text, flags=re.I | re.S):
            norm_url = normalize_iit_url(html_lib.unescape(loc))
            if norm_url is not None:
                discovered[norm_url] = None
    return discovered


def classify_iit_doc_type(url: str) -> str:
    """Map an IIT URL to a valid `CorpusDocument.category` (`DocumentCategoryEnum`)."""
    path = urlparse(url).path.lower().strip("/")
    if any(k in path for k in ("procedure-et-frais", "frais", "bourse")):
        return "fees_schedule"
    if any(k in path for k in ("inscription", "candidature", "admission")):
        return "admission_rules"
    if any(k in path for k in ("reglement", "guide-etudiants")):
        return "reglement"
    if path in {"formation", "licences", "filieres-ingenieur", "prepa", "mastere", "masters"} or (
        any(
            k in path
            for k in (
                "filieres-ingenieur",
                "licence",
                "genie-",
                "big-data",
                "reseaux-et-cybersecurite",
                "systemes-embarques",
                "architecture",
                "formation-continue",
                "prepa",
                "master",
            )
        )
    ):
        return "programme"
    return "general_brochure"


def build_slug_and_code(url: str) -> tuple[str, str]:
    """Derive a deterministic filesystem slug and `<= 64`-char `CorpusDocument.code`."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "accueil-iit", "IIT-WEB-ACCUEIL"

    clean_parts = []
    for part in path.split("/"):
        sanitized = re.sub(r"[^a-z0-9-]+", "-", part.lower()).strip("-")
        if sanitized:
            clean_parts.append(sanitized)

    slug = "__".join(clean_parts) or "accueil-iit"

    # Strip leading "test__" menu prefix in document code while keeping slug traceable
    code_parts = [p for p in clean_parts if p != "test"] or clean_parts
    raw_code = "IIT-WEB-" + "-".join(code_parts).upper()
    raw_code = re.sub(r"-{2,}", "-", raw_code).strip("-")
    if len(raw_code) <= 64:
        return slug[:120], raw_code

    short_hash = hashlib.sha256(path.encode("utf-8")).hexdigest()[:6].upper()
    truncated = raw_code[: 64 - 7].rstrip("-")
    return slug[:120], f"{truncated}-{short_hash}"


# ---------------------------------------------------------------------------
# Dual-Engine HTML Parsing (BeautifulSoup primary + stdlib HTMLParser fallback)
# ---------------------------------------------------------------------------


class _StdlibDomBuilder(HTMLParser):
    """Lightweight stdlib HTML-to-DomNode tree builder matching BeautifulSoup structure."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = DomNode(tag="document")
        self._stack: list[DomNode] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs if k}
        node = DomNode(tag=tag_l, attrs=attr_map)
        self._stack[-1].children.append(node)
        if tag_l not in VOID_HTML_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs if k}
        self._stack[-1].children.append(DomNode(tag=tag_l, attrs=attr_map))

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in VOID_HTML_TAGS:
            return
        for idx in range(len(self._stack) - 1, 0, -1):
            if self._stack[idx].tag == tag_l:
                del self._stack[idx:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(DomNode(tag=None, text=data))


def _bs4_to_dom(soup_node: Any) -> DomNode:
    root = DomNode(tag="document")

    def _convert(bs_el: Any) -> DomNode | None:
        if Comment is not None and isinstance(bs_el, Comment):
            return None
        if NavigableString is not None and isinstance(bs_el, NavigableString):
            txt = str(bs_el)
            return DomNode(tag=None, text=txt) if txt else None
        if Tag is not None and isinstance(bs_el, Tag):
            attrs: dict[str, str] = {}
            for k, v in (bs_el.attrs or {}).items():
                key = str(k).lower()
                if isinstance(v, list):
                    attrs[key] = " ".join(str(x) for x in v)
                else:
                    attrs[key] = str(v or "")
            node = DomNode(tag=bs_el.name.lower(), attrs=attrs)
            for child in bs_el.children:
                conv = _convert(child)
                if conv is not None:
                    node.children.append(conv)
            return node
        return None

    for child in soup_node.children:
        conv = _convert(child)
        if conv is not None:
            root.children.append(conv)
    return root


def parse_html_to_dom(html_text: str, *, force_stdlib: bool = False) -> DomNode:
    """Parse raw HTML into a `DomNode` tree using BeautifulSoup (or stdlib fallback)."""
    if HAS_BS4 and not force_stdlib and BeautifulSoup is not None:
        soup = BeautifulSoup(html_text, "html.parser")
        return _bs4_to_dom(soup)
    builder = _StdlibDomBuilder()
    builder.feed(html_text)
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# Metadata Extraction & Clean Markdown Rendering
# ---------------------------------------------------------------------------


def _find_nodes(node: DomNode, tag_name: str) -> list[DomNode]:
    found: list[DomNode] = []
    if node.tag == tag_name:
        found.append(node)
    for child in node.children:
        found.extend(_find_nodes(child, tag_name))
    return found


def _plain_text(node: DomNode) -> str:
    if node.tag is None:
        return node.text
    if node.tag == "br":
        return "\n"
    return "".join(_plain_text(c) for c in node.children)


def _normalize_inline_ws(text: str) -> str:
    cleaned = html_lib.unescape(text).replace("\xa0", " ").replace("\u200b", "")
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    return cleaned.strip()


def _extract_embedded_pdf_url(node: DomNode, *, page_url: str) -> str | None:
    """Extract an official `.pdf` URL from an EmbedPress `<iframe>`, `<embed>`, or `<object>`."""
    for attr_name in ("data-emsrc", "data-pdf-src", "data", "src"):
        raw = node.attrs.get(attr_name, "").strip()
        if not raw:
            continue
        resolved = urljoin(page_url, html_lib.unescape(raw))
        parsed = urlparse(resolved)
        if parsed.scheme in {"http", "https"} and parsed.path.lower().endswith(".pdf"):
            return resolved
        if parsed.query:
            qs = parse_qs(parsed.query)
            for vals in qs.values():
                for val in vals:
                    cand = urljoin(page_url, unquote(val))
                    cp = urlparse(cand)
                    if cp.scheme in {"http", "https"} and cp.path.lower().endswith(".pdf"):
                        return cand
    return None


def extract_html_metadata(
    html_text: str,
    *,
    page_url: str,
    force_stdlib: bool = False,
) -> tuple[str, str | None, list[str]]:
    """Extract `(title, canonical_url, pdf_links)` from raw HTML."""
    dom = parse_html_to_dom(html_text, force_stdlib=force_stdlib)

    title = ""
    for t_node in _find_nodes(dom, "title"):
        cand = _normalize_inline_ws(_plain_text(t_node))
        if cand:
            title = cand
            break

    canonical_url: str | None = None
    head_nodes = _find_nodes(dom, "head")
    link_search_roots = head_nodes if head_nodes else [dom]
    for root_node in link_search_roots:
        for link_node in _find_nodes(root_node, "link"):
            rel = link_node.attrs.get("rel", "").lower()
            href = link_node.attrs.get("href", "").strip()
            if "canonical" in rel.split() and href:
                canonical_url = normalize_iit_url(href, base_url=page_url)
                break
        if canonical_url is not None:
            break

    pdf_links: set[str] = set()
    for a_node in _find_nodes(dom, "a"):
        href = a_node.attrs.get("href", "").strip()
        if not href:
            continue
        resolved = urljoin(page_url, href)
        parsed = urlparse(resolved)
        if parsed.scheme in {"http", "https"} and parsed.path.lower().endswith(".pdf"):
            pdf_links.add(resolved)

    for tag_name in ("iframe", "embed", "object"):
        for emb_node in _find_nodes(dom, tag_name):
            pdf_u = _extract_embedded_pdf_url(emb_node, page_url=page_url)
            if pdf_u:
                pdf_links.add(pdf_u)

    return title, canonical_url, sorted(pdf_links)


def _is_boilerplate_node(node: DomNode) -> bool:
    if node.tag in STRIP_TAGS:
        return True
    cls_str = node.attrs.get("class", "")
    # Strip <header> unless it is a BoldThemes section headline (<header class="bt_bb_headline">)
    if node.tag == "header" and "bt_bb_headline" not in cls_str.lower():
        return True
    el_type = node.attrs.get("data-elementor-type", "").strip().lower()
    if el_type in {"header", "footer", "popup"}:
        return True
    role = node.attrs.get("role", "").strip().lower()
    if role in {"navigation", "banner", "contentinfo", "dialog"}:
        return True
    aria_hidden = node.attrs.get("aria-hidden", "").strip().lower()
    if aria_hidden == "true":
        return True
    cls_and_id = f"{cls_str} {node.attrs.get('id', '')}".strip()
    if cls_and_id and _BOILERPLATE_ATTR_RE.search(cls_and_id):
        return True
    return False


def _render_inline(node: DomNode, *, page_url: str) -> str:
    if node.tag is None:
        return node.text
    if _is_boilerplate_node(node):
        return ""
    if node.tag == "br":
        return " "

    inner = "".join(_render_inline(c, page_url=page_url) for c in node.children)
    if node.tag in {"strong", "b"}:
        stripped = _normalize_inline_ws(inner)
        return f" **{stripped}** " if stripped else ""
    if node.tag in {"em", "i"}:
        stripped = _normalize_inline_ws(inner)
        return f" *{stripped}* " if stripped else ""
    if node.tag == "a":
        label = _normalize_inline_ws(inner)
        href = node.attrs.get("href", "").strip()
        if not label:
            return ""
        if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
            resolved = urljoin(page_url, href)
            parsed = urlparse(resolved)
            if parsed.scheme in {"http", "https"}:
                return f" [{label}]({resolved}) "
        return f" {label} "
    return inner


def _format_inline_block(node: DomNode, *, page_url: str) -> str:
    raw = _render_inline(node, page_url=page_url)
    cleaned = _normalize_inline_ws(raw)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    return cleaned


def _render_table(table_node: DomNode, *, page_url: str) -> str:
    rows: list[list[str]] = []
    for tr in _find_nodes(table_node, "tr"):
        cells: list[str] = []
        for child in tr.children:
            if child.tag in {"th", "td"}:
                cell_txt = _format_inline_block(child, page_url=page_url).replace("|", "\\|")
                cells.append(cell_txt)
        if any(cells):
            rows.append(cells)

    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    padded = [r + [""] * (col_count - len(r)) for r in rows]
    header = "| " + " | ".join(padded[0]) + " |"
    sep = "| " + " | ".join(["---"] * col_count) + " |"
    body_lines = ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join([header, sep, *body_lines])


def _is_noisy_line(text: str) -> bool:
    stripped = text.strip(" -–—•|\t")
    if not stripped:
        return True
    for pat in _NOISY_LINE_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def _dedupe_signature(block: str) -> str:
    lowered = block.lower()
    lowered = re.sub(r"^#+\s*", "", lowered)
    lowered = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", lowered)
    lowered = re.sub(r"[^\w\u0600-\u06FF]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def html_to_clean_markdown(
    html_text: str,
    *,
    page_url: str,
    fallback_title: str = "",
    force_stdlib: bool = False,
) -> tuple[str, str]:
    """Convert raw `iit.tn` HTML into `(resolved_title, clean_markdown_body)`."""
    dom = parse_html_to_dom(html_text, force_stdlib=force_stdlib)

    meta_title = ""
    for t_node in _find_nodes(dom, "title"):
        cand = _normalize_inline_ws(_plain_text(t_node))
        if cand:
            meta_title = cand
            break

    body_nodes = _find_nodes(dom, "body")
    start_node = body_nodes[0] if body_nodes else dom

    blocks: list[str] = []
    first_h1: str = ""

    def _walk(node: DomNode) -> None:
        nonlocal first_h1
        if node.tag is None or _is_boilerplate_node(node):
            return

        tag = node.tag
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            txt = _format_inline_block(node, page_url=page_url)
            if txt and not _is_noisy_line(txt):
                level = int(tag[1])
                if level == 1 and not first_h1:
                    first_h1 = txt
                md_level = min(level, 4)
                blocks.append(f"{'#' * md_level} {txt}")
            return

        if tag == "table":
            tbl = _render_table(node, page_url=page_url)
            if tbl:
                blocks.append(tbl)
            return

        if tag in {"ul", "ol"}:
            # If list contains nested sublists, render direct li items cleanly
            items: list[str] = []
            ordered = tag == "ol"
            idx = 1
            for child in node.children:
                if child.tag != "li" or _is_boilerplate_node(child):
                    continue
                # Extract direct inline content excluding nested ul/ol
                li_clone = DomNode(
                    tag="li",
                    attrs=child.attrs,
                    children=[c for c in child.children if c.tag not in {"ul", "ol"}],
                )
                item_txt = _format_inline_block(li_clone, page_url=page_url)
                if item_txt and not _is_noisy_line(item_txt):
                    prefix = f"{idx}." if ordered else "-"
                    items.append(f"{prefix} {item_txt}")
                    idx += 1
                for sub in child.children:
                    if sub.tag in {"ul", "ol"}:
                        _walk(sub)
            if items:
                blocks.append("\n".join(items))
            return

        if tag in {"iframe", "embed", "object"}:
            pdf_u = _extract_embedded_pdf_url(node, page_url=page_url)
            if pdf_u:
                label = _normalize_inline_ws(node.attrs.get("title", "")) or Path(
                    urlparse(pdf_u).path
                ).name
                blocks.append(f"- **Document officiel (PDF) :** [{label}]({pdf_u})")
            return

        if tag == "div":
            cls_l = node.attrs.get("class", "").lower()
            if "bt_bb_headline_subheadline" in cls_l:
                sub_txt = _format_inline_block(node, page_url=page_url)
                if sub_txt and not _is_noisy_line(sub_txt):
                    blocks.append(sub_txt)
                return
            if "bt_bb_service_content" in cls_l and "bt_bb_service_content_" not in cls_l:
                svc_title = ""
                svc_text = ""
                for ch in node.children:
                    ch_cls = ch.attrs.get("class", "").lower()
                    if "bt_bb_service_content_title" in ch_cls:
                        svc_title = _format_inline_block(ch, page_url=page_url)
                    elif "bt_bb_service_content_text" in ch_cls:
                        svc_text = _format_inline_block(ch, page_url=page_url)
                if svc_title and svc_text:
                    blocks.append(f"- **{svc_title} :** {svc_text}")
                elif svc_text:
                    blocks.append(svc_text)
                return

        if tag in {"p", "blockquote"}:
            txt = _format_inline_block(node, page_url=page_url)
            if txt and not _is_noisy_line(txt):
                if txt.startswith(("– ", "- ", "• ")):
                    txt = "- " + txt.lstrip("–-• ").strip()
                elif tag == "blockquote":
                    txt = f"> {txt}"
                blocks.append(txt)
            return

        for child in node.children:
            _walk(child)

    _walk(start_node)

    resolved_title = meta_title or first_h1 or fallback_title or page_url
    # Clean common HTML entity leftovers in title
    resolved_title = _normalize_inline_ws(resolved_title)

    # Deduplicate consecutive / responsive-clone Elementor blocks
    deduped_blocks: list[str] = []
    seen_prose_sigs: set[str] = set()
    last_sig = ""
    for blk in blocks:
        sig = _dedupe_signature(blk)
        if not sig:
            continue
        if sig == last_sig:
            continue
        # Deduplicate long non-heading Elementor responsive clones anywhere on the same page
        if not blk.startswith("#") and len(sig) >= 60:
            if sig in seen_prose_sigs:
                continue
            seen_prose_sigs.add(sig)
        deduped_blocks.append(blk)
        last_sig = sig

    has_top_h1 = bool(deduped_blocks and deduped_blocks[0].startswith("# "))
    if not has_top_h1 and resolved_title:
        deduped_blocks.insert(0, f"# {resolved_title}")

    body_md = "\n\n".join(deduped_blocks).strip() + "\n"
    return resolved_title, body_md


def format_markdown_document(
    *,
    code: str,
    title: str,
    source_url: str,
    scraped_at: str,
    lastmod: str | None,
    school: str,
    doc_type: str,
    target_program_code: str | None,
    body_sha256: str,
    body_markdown: str,
) -> str:
    """Build full LF-normalized UTF-8 Markdown file with explicit YAML metadata header."""
    escaped_title = title.replace('"', '\\"')
    lines = [
        "---",
        f"code: {code}",
        f'title: "{escaped_title}"',
        f'source_url: "{source_url}"',
        f'scraped_at: "{scraped_at}"',
    ]
    if lastmod:
        lines.append(f'lastmod: "{lastmod}"')
    lines.extend(
        [
            f"school: {school}",
            f"doc_type: {doc_type}",
        ]
    )
    if target_program_code:
        lines.append(f"target_program_code: {target_program_code}")
    lines.extend(
        [
            f"sha256: {body_sha256}",
            "---",
            "",
            body_markdown.strip(),
            "",
        ]
    )
    return "\n".join(lines).replace("\r\n", "\n").replace("\r", "\n")


def compute_sha256_hex(text_or_bytes: str | bytes) -> str:
    """Compute lowercase SHA-256 hex digest over LF-normalized UTF-8 bytes."""
    if isinstance(text_or_bytes, str):
        raw = text_or_bytes.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    else:
        raw = text_or_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Polite Robots-Aware Crawler
# ---------------------------------------------------------------------------


def load_robots_parser(
    client: httpx.Client, *, robots_url: str = IIT_ROBOTS_URL
) -> RobotFileParser:
    """Fetch and parse `https://iit.tn/robots.txt` cleanly before crawling."""
    resp = client.get(robots_url)
    gate = detect_anti_bot_gate(resp.status_code, resp.text)
    if gate:
        raise AntiBotGateDetectedError(
            f"Aborting immediately on robots.txt ({robots_url}): {gate}."
        )
    parser = RobotFileParser()
    parser.set_url(robots_url)
    if resp.status_code == 200:
        parser.parse(resp.text.splitlines())
    else:
        parser.allow_all = True
    return parser


def load_existing_manifest(output_dir: Path) -> dict[str, dict[str, Any]]:
    """Load existing `manifest.json` keyed by `source_url` to preserve unchanged timestamps."""
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        docs = payload.get("documents") or []
        return {
            str(item["source_url"]): item
            for item in docs
            if isinstance(item, dict) and item.get("source_url")
        }
    except Exception:
        return {}


def scrape_iit_website(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    delay_seconds: float = 0.6,
    max_pages: int = 80,
    user_agent: str = DEFAULT_USER_AGENT,
    http_client: httpx.Client | None = None,
) -> list[ScrapedCorpusEntry]:
    """Crawl `https://iit.tn/` politely and write clean `.md` corpus files + `manifest.json`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    prior_by_url = load_existing_manifest(output_dir)

    owns_client = http_client is None
    client = http_client or httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8,en;q=0.7"},
        timeout=20.0,
        follow_redirects=True,
    )

    try:
        logger.info("Checking robots.txt at %s ...", IIT_ROBOTS_URL)
        robots = load_robots_parser(client)

        sitemap_lastmod: dict[str, str | None] = {}
        if robots.can_fetch(user_agent, IIT_PAGE_SITEMAP_URL):
            logger.info("Fetching sitemap %s ...", IIT_PAGE_SITEMAP_URL)
            sm_resp = client.get(IIT_PAGE_SITEMAP_URL)
            gate = detect_anti_bot_gate(sm_resp.status_code, sm_resp.text)
            if gate:
                raise AntiBotGateDetectedError(
                    f"Aborting immediately on sitemap ({IIT_PAGE_SITEMAP_URL}): {gate}."
                )
            if sm_resp.status_code == 200:
                sitemap_lastmod = parse_sitemap_xml(sm_resp.text)
                logger.info("Discovered %d URLs in page-sitemap.xml", len(sitemap_lastmod))

        ordered_candidates: list[str] = []
        seen_candidates: set[str] = set()
        for raw_u in [*CURATED_SEED_URLS, *sorted(sitemap_lastmod.keys())]:
            norm = normalize_iit_url(raw_u)
            if norm is None or norm in seen_candidates or is_excluded_iit_url(norm):
                continue
            seen_candidates.add(norm)
            ordered_candidates.append(norm)

        entries: list[ScrapedCorpusEntry] = []
        seen_canonical_urls: set[str] = set()
        seen_body_hashes: set[str] = set()
        kept_filenames: set[str] = set()

        for idx, candidate_url in enumerate(ordered_candidates[:max_pages]):
            if not robots.can_fetch(user_agent, candidate_url):
                logger.info("Skipping URL disallowed by robots.txt: %s", candidate_url)
                continue

            if idx > 0 and delay_seconds > 0:
                time.sleep(delay_seconds)

            try:
                resp = client.get(candidate_url)
            except httpx.HTTPError as exc:
                logger.warning("HTTP transport error on %s: %s", candidate_url, exc)
                continue

            gate = detect_anti_bot_gate(resp.status_code, resp.text)
            if gate:
                raise AntiBotGateDetectedError(
                    f"Stopping scraper immediately on {candidate_url}: {gate}."
                )

            if resp.status_code != 200:
                logger.info("Skipping %s (HTTP %d)", candidate_url, resp.status_code)
                continue

            content_type = resp.headers.get("content-type", "").lower()
            if "html" not in content_type:
                continue

            final_url = normalize_iit_url(str(resp.url)) or candidate_url
            meta_title, canonical_tag_url, pdf_links = extract_html_metadata(
                resp.text, page_url=final_url
            )
            effective_url = canonical_tag_url or final_url

            if is_excluded_iit_url(effective_url):
                logger.info("Skipping excluded canonical URL: %s", effective_url)
                continue
            if effective_url in seen_canonical_urls:
                logger.info("Skipping duplicate canonical URL: %s", effective_url)
                continue

            title, body_md = html_to_clean_markdown(
                resp.text,
                page_url=effective_url,
                fallback_title=meta_title,
            )

            # Exclude empty/stub pages with almost no textual substance beyond the H1 title
            prose_without_h1 = re.sub(r"^#\s+.*$", "", body_md, count=1, flags=re.M).strip()
            if len(prose_without_h1) < MIN_BODY_CHARS:
                logger.info(
                    "Skipping stub page %s (%d prose chars < %d)",
                    effective_url,
                    len(prose_without_h1),
                    MIN_BODY_CHARS,
                )
                continue

            body_sha256 = compute_sha256_hex(body_md)
            if body_sha256 in seen_body_hashes:
                logger.info("Skipping duplicate body SHA-256 on %s", effective_url)
                continue

            seen_canonical_urls.add(effective_url)
            seen_body_hashes.add(body_sha256)

            slug, doc_code = build_slug_and_code(effective_url)
            file_name = f"{slug}.md"
            doc_type = classify_iit_doc_type(effective_url)
            path_key = urlparse(effective_url).path.lower()
            target_program_code = PATH_TO_PROGRAM_CODE.get(path_key)
            lastmod = sitemap_lastmod.get(effective_url) or sitemap_lastmod.get(candidate_url)

            prior = prior_by_url.get(effective_url)
            existing_file = output_dir / file_name
            if (
                prior is not None
                and existing_file.exists()
                and prior.get("body_sha256") == body_sha256
                and prior.get("code") == doc_code
                and prior.get("doc_type") == doc_type
                and prior.get("title") == title
            ):
                scraped_at = str(prior["scraped_at"])
            else:
                scraped_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

            full_md = format_markdown_document(
                code=doc_code,
                title=title,
                source_url=effective_url,
                scraped_at=scraped_at,
                lastmod=lastmod,
                school="iit",
                doc_type=doc_type,
                target_program_code=target_program_code,
                body_sha256=body_sha256,
                body_markdown=body_md,
            )
            md_bytes = full_md.encode("utf-8")
            file_sha256 = compute_sha256_hex(md_bytes)
            existing_file.write_bytes(md_bytes)
            kept_filenames.add(file_name)

            word_count = len(re.findall(r"\S+", prose_without_h1))
            entry = ScrapedCorpusEntry(
                code=doc_code,
                slug=slug,
                file_name=file_name,
                title=title,
                source_url=effective_url,
                scraped_at=scraped_at,
                lastmod=lastmod,
                school="iit",
                doc_type=doc_type,
                target_program_code=target_program_code,
                body_sha256=body_sha256,
                sha256=file_sha256,
                char_count=len(prose_without_h1),
                word_count=word_count,
                pdf_links=pdf_links,
            )
            entries.append(entry)
            logger.info(
                "[%02d] Saved %s (%s, %d words) <- %s",
                len(entries),
                file_name,
                doc_type,
                word_count,
                effective_url,
            )

        # Remove stale .md files no longer present in the scraped set
        for old_md in output_dir.glob("*.md"):
            if old_md.name not in kept_filenames:
                old_md.unlink()

        entries.sort(key=lambda e: e.slug)
        manifest_payload = {
            "institution": "Institut International de Technologie (IIT Sfax — Groupe NAU)",
            "base_url": IIT_BASE_URL,
            "robots_url": IIT_ROBOTS_URL,
            "sitemap_url": IIT_PAGE_SITEMAP_URL,
            "parser_backend": "beautifulsoup4" if HAS_BS4 else "html.parser",
            "total_documents": len(entries),
            "documents": [asdict(e) for e in entries],
        }
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "Completed IIT corpus scrape: %d official documents written to %s",
            len(entries),
            output_dir,
        )
        return entries
    finally:
        if owns_client:
            client.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Polite robots.txt-compliant scraper for official https://iit.tn/ RAG corpus."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Destination directory for manifest.json and .md files.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Polite delay in seconds between HTTP GET requests (default: 0.6s).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=80,
        help="Maximum candidate URLs to inspect (default: 80).",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    args = _build_arg_parser().parse_args()
    entries = scrape_iit_website(
        output_dir=args.output_dir,
        delay_seconds=args.delay,
        max_pages=args.max_pages,
    )
    if not entries:
        logger.error("No pages scraped from https://iit.tn/.")
        sys.exit(1)


if __name__ == "__main__":
    main()
