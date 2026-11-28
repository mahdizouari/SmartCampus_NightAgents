"""Offline unit & corpus-integrity tests for Pillar 3 (`scrape_iit_tn.py` & `sync_iit_corpus.py`).

Covers:
1. URL normalization, WordPress placeholder/demo exclusion (`/page-d-exemple/`, `/candidature/`,
   `/gsm_block/`), valid `/test/...` admission/fee page inclusion, `DocumentCategoryEnum` mapping,
   and `<= 64`-char `CorpusDocument.code` generation.
2. Dual-engine HTML-to-Markdown cleaning (`beautifulsoup4` + `force_stdlib=True` parity), stripping
   `<style>`, `<script>`, BoldThemes/Elementor headers & footers, and responsive clones while
   preserving headings, BoldThemes subheadlines/service blocks, lists, tables, and EmbedPress PDFs.
3. Polite `robots.txt` compliance, `<link rel="canonical">` deduplication, immediate abort on
   anti-bot/rate-limit gates (`AntiBotGateDetectedError`), and idempotent re-runs via
   `httpx.MockTransport` (zero live network calls during `pytest`).
4. Integrity audit of the real committed `backend/data/corpus/iit/` corpus and `seed_db.py` IIT
   `source_document` URLs.
5. Idempotent `SA-56` PostgreSQL synchronization (`sync_iit_corpus_to_db`): `NEW_DOCUMENT` (`v1` +
   `PENDING INGEST`), `UNCHANGED` no-op skip on Run 2, and `NEW_VERSION` (`v2` + `REINDEX`) on
   content update.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.deps import CurrentActor
from app.schemas.knowledge import (
    ChangeTypeEnum,
    DocumentCategoryEnum,
    DocumentStatusEnum,
    SyncActionTypeEnum,
    SyncJobStatusEnum,
)
from scripts.scrape_iit_tn import (
    AntiBotGateDetectedError,
    build_slug_and_code,
    classify_iit_doc_type,
    compute_sha256_hex,
    extract_html_metadata,
    format_markdown_document,
    html_to_clean_markdown,
    is_excluded_iit_url,
    normalize_iit_url,
    parse_sitemap_xml,
    scrape_iit_website,
)
from scripts.seed_db import DEMO_ACCOUNTS, PROGRAMS, ROLES, SCHOOLS
from scripts.sync_iit_corpus import DEFAULT_CORPUS_DIR, sync_iit_corpus_to_db
from tests.test_document_sync import PROGRAM_ID, RESP_IIT_ID, SyncFakeAsyncSession


class _CorpusSyncFakeSession(SyncFakeAsyncSession):
    """Extends SA-56 `SyncFakeAsyncSession` with `(Program.code, Program.id)` tuple support."""

    async def execute(self, statement: Any) -> Any:
        compiled = statement.compile()
        sql_lower = str(compiled).lower()
        if "select core.program.code, core.program.id" in sql_lower:

            class _TupleResult:
                def all(self) -> list[tuple[str, uuid.UUID]]:
                    return [("IIT-PREPA", PROGRAM_ID)]

            return _TupleResult()
        return await super().execute(statement)


# ---------------------------------------------------------------------------
# 1. URL Normalization, Junk Filtering & Classification
# ---------------------------------------------------------------------------


def test_normalize_and_filter_iit_urls() -> None:
    assert normalize_iit_url("http://www.iit.tn/formation") == "https://iit.tn/formation/"
    assert normalize_iit_url("/prepa", base_url="https://iit.tn/formation/") == (
        "https://iit.tn/prepa/"
    )
    assert normalize_iit_url("https://isb.tn/licences/") is None
    assert normalize_iit_url("mailto:info@iit.tn") is None
    assert normalize_iit_url("https://iit.tn/wp-content/uploads/2026/10/plan.pdf") is None
    assert normalize_iit_url("https://iit.tn/gsm_block/core___social-links/") is None

    # Excluded WordPress placeholders, demo pages, and superseded v1 drafts
    for junk in (
        "https://iit.tn/page-d-exemple/",
        "https://iit.tn/3256-2/",
        "https://iit.tn/sleh/",
        "https://iit.tn/about-me/",
        "https://iit.tn/candidature/",
        "https://iit.tn/test/",
        "https://iit.tn/filieres-ingenieur/genie-informatique/",
    ):
        assert is_excluded_iit_url(junk) is True, f"Expected {junk} to be excluded"

    # Official IIT admission, fee, scholarship, and regulation pages under /test/... MUST stay kept
    for valid_url in (
        "https://iit.tn/formation/",
        "https://iit.tn/filieres-ingenieur/genie-informatique-2/",
        "https://iit.tn/test/inscription/",
        "https://iit.tn/test/procedure-et-frais-dinscription/",
        "https://iit.tn/test/bourse/",
        "https://iit.tn/test/guide-etudiants-reglement-interieur/",
    ):
        assert is_excluded_iit_url(valid_url) is False, f"Expected {valid_url} to be included"


def test_classify_doc_type_and_slug_code_constraints() -> None:
    valid_categories = {e.value for e in DocumentCategoryEnum}

    cases = {
        "https://iit.tn/test/procedure-et-frais-dinscription/": "fees_schedule",
        "https://iit.tn/test/bourse/": "fees_schedule",
        "https://iit.tn/test/inscription/": "admission_rules",
        "https://iit.tn/test/guide-etudiants-reglement-interieur/": "reglement",
        "https://iit.tn/filieres-ingenieur/genie-informatique-2/": "programme",
        "https://iit.tn/licence-en-systemes-electriques-intelligents-energies-renouvelables/": (
            "programme"
        ),
        "https://iit.tn/a-propos/accreditations/": "general_brochure",
    }
    code_re = re.compile(r"^[A-Za-z0-9._-]+$")
    for url, expected_cat in cases.items():
        cat = classify_iit_doc_type(url)
        assert cat == expected_cat
        assert cat in valid_categories
        slug, code = build_slug_and_code(url)
        assert slug
        assert len(code) <= 64
        assert code_re.match(code), f"Invalid CorpusDocument.code: {code}"


def test_parse_sitemap_xml_extracts_locs_and_lastmods() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://iit.tn/prepa/</loc>
        <lastmod>2026-11-21T08:15:34+00:00</lastmod>
      </url>
      <url>
        <loc>https://iit.tn/wp-content/uploads/logo.png</loc>
      </url>
    </urlset>
    """
    parsed = parse_sitemap_xml(xml)
    assert parsed == {"https://iit.tn/prepa/": "2026-11-21T08:15:34+00:00"}


# ---------------------------------------------------------------------------
# 2. Dual-Engine HTML Cleaner & Metadata Extractor
# ---------------------------------------------------------------------------


SAMPLE_IIT_HTML = """<!DOCTYPE html>
<html lang="fr-FR">
<head>
  <title>Cycle Ingénieur Génie Informatique | IIT</title>
  <link rel="canonical" href="https://iit.tn/gsm_block/core___social-links/" />
  <style>.elementor-123 { color: red; font-size: 99px; }</style>
  <script>window.analytics = true;</script>
</head>
<body>
  <header class="mainHeader btClear">
    <nav><a href="/">Accueil</a><a href="/formation/">Formations</a></nav>
  </header>
  <div class="btContent">
    <header class="bt_bb_headline bt_bb_subheadline">
      <h1><span class="bt_bb_headline_superheadline">Home FORMATION</span>Génie Informatique</h1>
      <div class="bt_bb_headline_subheadline">Programme accrédité ASIIN — Label Euro-Inf.</div>
    </header>
    <p>Le diplôme d'ingénieur propose trois parcours complémentaires d'excellence à Sfax.</p>
    <div class="elementor-hidden-desktop">
      <p>Le diplôme d'ingénieur propose trois parcours complémentaires d'excellence à Sfax.</p>
    </div>
    <ul>
      <li><strong>GLID</strong> : Génie Logiciel et Informatique Décisionnelle</li>
      <li><strong>ARSI</strong> : Administration Réseau et Sécurité Informatique</li>
      <li><strong>SDIA</strong> : Science des Données et Intelligence Artificielle</li>
    </ul>
    <table>
      <tr><th>Parcours</th><th>Durée</th></tr>
      <tr><td>Cycle Ingénieur</td><td>3 ans</td></tr>
    </table>
    <div class="bt_bb_service_content">
      <div class="bt_bb_service_content_title">Email</div>
      <div class="bt_bb_service_content_text">info@iit.tn</div>
    </div>
    <p><a href="/wp-content/uploads/2026/10/Criteres_d_admission.pdf">Critères PDF</a></p>
    <iframe
      title="Plan d'étude Génie Informatique"
      data-emsrc="https://iit.tn/wp-content/uploads/2026/09/Plan_Etude_GI_2026.pdf"
      src="https://iit.tn/wp-content/plugins/embedpress/viewer.html">
    </iframe>
    <p>Lire la suite »</p>
  </div>
  <div class="btSiteFooter">
    <p>Copyright by IIT Developers © All rights reserved.</p>
  </div>
</body>
</html>
"""


def test_html_cleaner_strips_noise_and_matches_stdlib_fallback() -> None:
    page_url = "https://iit.tn/filieres-ingenieur/genie-informatique-2/"
    meta_title, canonical, pdf_links = extract_html_metadata(SAMPLE_IIT_HTML, page_url=page_url)
    assert meta_title == "Cycle Ingénieur Génie Informatique | IIT"
    # Bogus /gsm_block/ canonical tag must be rejected cleanly
    assert canonical is None
    assert pdf_links == [
        "https://iit.tn/wp-content/uploads/2026/09/Plan_Etude_GI_2026.pdf",
        "https://iit.tn/wp-content/uploads/2026/10/Criteres_d_admission.pdf",
    ]

    title_default, md_default = html_to_clean_markdown(SAMPLE_IIT_HTML, page_url=page_url)
    title_stdlib, md_stdlib = html_to_clean_markdown(
        SAMPLE_IIT_HTML, page_url=page_url, force_stdlib=True
    )
    assert title_default == title_stdlib == "Cycle Ingénieur Génie Informatique | IIT"
    assert md_default == md_stdlib

    # Structure preserved
    assert "# Génie Informatique" in md_default
    assert "Home FORMATION" not in md_default
    assert "Programme accrédité ASIIN — Label Euro-Inf." in md_default
    assert "- **GLID**: Génie Logiciel et Informatique Décisionnelle" in md_default
    assert "| Parcours | Durée |" in md_default
    assert "| Cycle Ingénieur | 3 ans |" in md_default
    assert "- **Email :** info@iit.tn" in md_default
    assert "Plan_Etude_GI_2026.pdf" in md_default

    # Boilerplate & CSS/JS completely stripped
    assert "elementor-123" not in md_default
    assert "window.analytics" not in md_default
    assert "Copyright by IIT Developers" not in md_default
    assert "Lire la suite" not in md_default


# ---------------------------------------------------------------------------
# 3. Polite Crawler MockTransport Tests (robots.txt, Canonical Dedup, Anti-Bot)
# ---------------------------------------------------------------------------


def test_scraper_respects_robots_canonical_dedup_and_idempotency(tmp_path: Path) -> None:
    rich_body = (
        "L'Institut International de Technologie de Sfax propose une formation d'ingénieur "
        "en Génie Civil couvrant le calcul des structures, la géotechnique, le BIM et la "
        "conduite de chantiers d'infrastructures modernes en Tunisie et à l'international."
    )
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://iit.tn/filieres-ingenieur/genie-civil/</loc></url>
      <url><loc>https://iit.tn/filieres-ingenieur/genie-civil-alias/</loc></url>
      <url><loc>https://iit.tn/private-internal/</loc></url>
      <url><loc>https://iit.tn/page-d-exemple/</loc></url>
    </urlset>
    """
    fetched_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        fetched_paths.append(path)
        if path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private-internal/\n",
                headers={"content-type": "text/plain"},
            )
        if path == "/page-sitemap.xml":
            return httpx.Response(200, text=sitemap_xml, headers={"content-type": "text/xml"})
        if path in {"/filieres-ingenieur/genie-civil/", "/filieres-ingenieur/genie-civil-alias/"}:
            html = f"""<html><head>
              <title>Génie Civil | IIT</title>
              <link rel="canonical" href="https://iit.tn/filieres-ingenieur/genie-civil/" />
            </head><body><h1>Génie Civil</h1><p>{rich_body}</p></body></html>"""
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        return httpx.Response(404, text="Not found", headers={"content-type": "text/html"})

    out_dir = tmp_path / "corpus"
    with httpx.Client(transport=httpx.MockTransport(handler)) as mock_client:
        entries_1 = scrape_iit_website(
            output_dir=out_dir,
            delay_seconds=0.0,
            http_client=mock_client,
        )
        assert len(entries_1) == 1
        assert entries_1[0].source_url == "https://iit.tn/filieres-ingenieur/genie-civil/"
        assert entries_1[0].target_program_code == "IIT-ING-CIVIL"
        assert "/private-internal/" not in fetched_paths
        assert "/page-d-exemple/" not in fetched_paths

        first_scraped_at = entries_1[0].scraped_at
        first_sha256 = entries_1[0].sha256

        # Run 2 with identical content must preserve `scraped_at` and file `sha256`
        entries_2 = scrape_iit_website(
            output_dir=out_dir,
            delay_seconds=0.0,
            http_client=mock_client,
        )
        assert len(entries_2) == 1
        assert entries_2[0].scraped_at == first_scraped_at
        assert entries_2[0].sha256 == first_sha256


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (429, "Too Many Requests"),
        (200, '<div class="cf-turnstile" data-sitekey="xyz">Verify you are human</div>'),
    ],
)
def test_scraper_aborts_immediately_on_antibot_or_rate_limit(
    tmp_path: Path, status_code: int, body: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if request.url.path == "/page-sitemap.xml":
            return httpx.Response(200, text="<urlset></urlset>")
        return httpx.Response(status_code, text=body, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as mock_client:
        with pytest.raises(AntiBotGateDetectedError):
            scrape_iit_website(
                output_dir=tmp_path / "blocked",
                delay_seconds=0.0,
                http_client=mock_client,
            )


# ---------------------------------------------------------------------------
# 4. Real Committed IIT Corpus & Seed Catalog Integrity
# ---------------------------------------------------------------------------


def test_committed_iit_corpus_manifest_and_markdown_files_integrity() -> None:
    manifest_path = DEFAULT_CORPUS_DIR / "manifest.json"
    assert manifest_path.exists(), "backend/data/corpus/iit/manifest.json must be committed"

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = payload.get("documents")
    assert isinstance(docs, list)
    assert len(docs) >= 30
    assert payload["total_documents"] == len(docs)

    valid_categories = {e.value for e in DocumentCategoryEnum}
    seen_codes: set[str] = set()
    seen_urls: set[str] = set()

    for item in docs:
        code = item["code"]
        url = item["source_url"]
        assert code not in seen_codes
        assert url not in seen_urls
        seen_codes.add(code)
        seen_urls.add(url)

        assert url.startswith("https://iit.tn/")
        assert "/gsm_block/" not in url
        assert "/page-d-exemple/" not in url
        assert "/candidature/" not in url
        assert item["school"] == "iit"
        assert item["doc_type"] in valid_categories
        assert len(code) <= 64

        md_path = DEFAULT_CORPUS_DIR / item["file_name"]
        assert md_path.exists(), f"Missing corpus file: {md_path}"
        raw_bytes = md_path.read_bytes()
        assert compute_sha256_hex(raw_bytes) == item["sha256"]

        md_text = raw_bytes.decode("utf-8")
        assert md_text.startswith("---\n")
        assert f'source_url: "{url}"' in md_text
        assert "<style" not in md_text.lower()
        assert "<script" not in md_text.lower()
        assert "avantage.co.uk" not in md_text.lower()

    required_urls = {
        "https://iit.tn/",
        "https://iit.tn/formation/",
        "https://iit.tn/prepa/",
        "https://iit.tn/filieres-ingenieur/genie-informatique-2/",
        "https://iit.tn/filieres-ingenieur/genie-civil/",
        "https://iit.tn/filieres-ingenieur/genie-industriel-2/",
        "https://iit.tn/filieres-ingenieur/genie-mecanique-2/",
        "https://iit.tn/genie-des-procedes/",
        "https://iit.tn/genie-logiciel-systemes-intelligents/",
        "https://iit.tn/test/inscription/",
        "https://iit.tn/test/procedure-et-frais-dinscription/",
        "https://iit.tn/test/bourse/",
    }
    missing = required_urls - seen_urls
    assert not missing, f"Missing expected official IIT URLs in corpus: {missing}"


def test_seed_db_iit_programs_reference_verified_iit_urls_without_breaking_counts() -> None:
    assert len(SCHOOLS) == 3
    assert len(ROLES) == 5
    assert len(PROGRAMS) == 16
    assert len(DEMO_ACCOUNTS) == 7

    iit_programs = [p for p in PROGRAMS if p["school_code"] == "iit"]
    assert len(iit_programs) == 9
    verified_iit = [p for p in iit_programs if p["code"] != "IIT-ING-ENER"]
    assert len(verified_iit) == 8
    for prog in verified_iit:
        src = prog["source_document"]
        assert src.startswith("https://iit.tn/"), (
            f"IIT program {prog['code']} still has unverified source_document: {src}"
        )
        assert "TODO(corpus)" not in src

    ener_prog = next(p for p in iit_programs if p["code"] == "IIT-ING-ENER")
    assert ener_prog["source_document"] == "TODO(corpus): official_iit_energy_url_pending"

    # ISB and ISA were not scraped; they must honestly retain TODO(corpus) markers
    non_iit_programs = [p for p in PROGRAMS if p["school_code"] != "iit"]
    assert len(non_iit_programs) == 7
    for prog in non_iit_programs:
        assert prog["source_document"].startswith("TODO(corpus):")


# ---------------------------------------------------------------------------
# 5. Idempotent SA-56 PostgreSQL Corpus Sync (`sync_iit_corpus_to_db`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_iit_corpus_to_db_idempotent_and_version_upgrade(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "iit_corpus"
    corpus_dir.mkdir(parents=True)
    storage_root = tmp_path / "storage"

    body_v1 = "# Cycle Préparatoire IIT\n\nFormation scientifique intensive de 2 ans à Sfax.\n"
    md_v1 = format_markdown_document(
        code="IIT-WEB-PREPA",
        title="Cycle Préparatoire IIT",
        source_url="https://iit.tn/prepa/",
        scraped_at="2026-11-27T18:00:00Z",
        lastmod="2026-11-21T08:15:34+00:00",
        school="iit",
        doc_type="programme",
        target_program_code="IIT-PREPA",
        body_sha256=compute_sha256_hex(body_v1),
        body_markdown=body_v1,
    )
    (corpus_dir / "prepa.md").write_text(md_v1, encoding="utf-8")
    manifest = {
        "total_documents": 1,
        "documents": [
            {
                "code": "IIT-WEB-PREPA",
                "slug": "prepa",
                "file_name": "prepa.md",
                "title": "Cycle Préparatoire IIT",
                "source_url": "https://iit.tn/prepa/",
                "scraped_at": "2026-11-27T18:00:00Z",
                "lastmod": "2026-11-21T08:15:34+00:00",
                "school": "iit",
                "doc_type": "programme",
                "target_program_code": "IIT-PREPA",
                "body_sha256": compute_sha256_hex(body_v1),
                "sha256": compute_sha256_hex(md_v1),
                "char_count": len(body_v1),
                "word_count": 10,
                "pdf_links": [],
            }
        ],
    }
    (corpus_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    fake_db = _CorpusSyncFakeSession()
    actor = CurrentActor(
        account_id=str(RESP_IIT_ID),
        role="responsable",
        school_scope="iit",
        email="responsable.iit@smartcampus.tn",
    )

    # Run 1: Creates NEW_DOCUMENT (v1, PUBLISHED, PENDING INGEST)
    sum_1 = await sync_iit_corpus_to_db(
        fake_db,  # type: ignore[arg-type]
        corpus_dir=corpus_dir,
        actor=actor,
        storage_root=storage_root,
    )
    assert sum_1.created_new == 1
    assert sum_1.skipped_unchanged == 0
    assert sum_1.jobs_enqueued == 1
    assert sum_1.outcomes[0].change_type == ChangeTypeEnum.NEW_DOCUMENT.value
    assert sum_1.outcomes[0].version_number == 1

    assert len(fake_db.documents) == 1
    saved_doc = next(iter(fake_db.documents.values()))
    assert saved_doc.status == DocumentStatusEnum.PUBLISHED.value
    assert saved_doc.target_program_id == PROGRAM_ID
    assert len(fake_db.versions) == 1
    assert len(fake_db.jobs) == 1
    first_job = next(iter(fake_db.jobs.values()))
    assert first_job.action_type == SyncActionTypeEnum.INGEST.value
    assert first_job.status == SyncJobStatusEnum.PENDING.value

    # Run 2: Identical SHA-256 -> UNCHANGED, zero new versions, zero new sync jobs
    sum_2 = await sync_iit_corpus_to_db(
        fake_db,  # type: ignore[arg-type]
        corpus_dir=corpus_dir,
        actor=actor,
        storage_root=storage_root,
    )
    assert sum_2.created_new == 0
    assert sum_2.updated_versions == 0
    assert sum_2.skipped_unchanged == 1
    assert sum_2.jobs_enqueued == 0
    assert sum_2.outcomes[0].change_type == ChangeTypeEnum.UNCHANGED.value
    assert len(fake_db.versions) == 1
    assert len(fake_db.jobs) == 1

    # Run 3: Website content updated -> creates v2 + PENDING REINDEX job
    body_v2 = body_v1 + "\nNouvelle section MP1 / MP2 ajoutée sur iit.tn.\n"
    md_v2 = format_markdown_document(
        code="IIT-WEB-PREPA",
        title="Cycle Préparatoire IIT",
        source_url="https://iit.tn/prepa/",
        scraped_at="2026-11-28T09:00:00Z",
        lastmod="2026-11-28T08:00:00+00:00",
        school="iit",
        doc_type="programme",
        target_program_code="IIT-PREPA",
        body_sha256=compute_sha256_hex(body_v2),
        body_markdown=body_v2,
    )
    (corpus_dir / "prepa.md").write_text(md_v2, encoding="utf-8")

    sum_3 = await sync_iit_corpus_to_db(
        fake_db,  # type: ignore[arg-type]
        corpus_dir=corpus_dir,
        actor=actor,
        storage_root=storage_root,
    )
    assert sum_3.created_new == 0
    assert sum_3.updated_versions == 1
    assert sum_3.skipped_unchanged == 0
    assert sum_3.jobs_enqueued == 1
    assert sum_3.outcomes[0].change_type == ChangeTypeEnum.NEW_VERSION.value
    assert sum_3.outcomes[0].version_number == 2
    assert len(fake_db.versions) == 2
    active_versions = [v for v in fake_db.versions.values() if v.is_active]
    assert len(active_versions) == 1
    assert active_versions[0].version_number == 2
