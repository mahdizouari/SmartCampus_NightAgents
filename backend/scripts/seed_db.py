"""
SA-18 — Seed script: Alimenter la base avec des données de démonstration.

Données initiales conformes aux spécifications du campus NAU :
  - Établissements :
      * IIT : Institut International de Technologie
      * ISB : International School of Business
      * ISA : Institut Supérieur d'Architecture
  - Spécialités d'ingénierie IIT :
      * Génie Logiciel & Informatique Décisionnelle (GLID)
      * Administration Réseaux & Sécurité Informatique (ARSI)
      * Data Science & Intelligence Artificielle (DSIA)
      * Génie Mécanique, Génie Industriel, Génie Énergétique, Génie Civil
      * Classes Préparatoires Intégrées & Licence en Informatique
  - Formations Architecture ISA :
      * Diplôme du Premier Cycle d'Études en Architecture
      * Diplôme National d'Architecte (Deuxième Cycle)
      * Licence en Architecture d'Intérieur & Design d'Espace
  - Formations Gestion & Business ISB :
      * Licences Administration des Affaires, Finance, Marketing Digital
      * Master Management Stratégique & International
  - Grilles tarifaires 2026-2027 et échéanciers de paiement
  - Prérequis d'admission Gatekeeper (bac, prépa, licence, passerelle)
  - Comptes responsables de chaque filière (IIT, ISB, ISA), RH, Admin et Candidat
  - Candidat de test avec profil Bac et matières/notes déclarées
  - Créneaux d'entretien (slots) pour les responsables des 3 écoles

IDEMPOTENT : exécutable à volonté sans créer de doublons.

Usage :
    docker exec smartcampus_backend python scripts/seed_db.py
"""

import asyncio
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Configuration & Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("seed")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://smartcampus_app:smartcampus_app_secret@localhost:5433/smartcampus_db",
)


# ---------------------------------------------------------------------------
# 1. Établissements — core.school
#    Codes contraints : 'iit', 'isb', 'isa'
# ---------------------------------------------------------------------------
SCHOOLS = [
    {
        "code": "iit",
        "name": "Institut International de Technologie",
        "is_extension": False,
    },
    {
        "code": "isb",
        "name": "International School of Business",
        "is_extension": False,
    },
    {
        "code": "isa",
        "name": "Institut Supérieur d'Architecture",
        "is_extension": True,
    },
]

# ---------------------------------------------------------------------------
# 2. Rôles — core.roles
#    Codes contraints : 'student', 'rh', 'responsable', 'direction', 'admin'
# ---------------------------------------------------------------------------
ROLES = [
    {
        "code": "admin",
        "label": "Administrateur Plateforme",
        "description": "Accès total à la plateforme SmartCampus AI.",
    },
    {
        "code": "responsable",
        "label": "Responsable de Filière",
        "description": "Responsable pédagogique d'une école (IIT, ISB ou ISA).",
    },
    {
        "code": "rh",
        "label": "Chargé RH / Admissions",
        "description": "Gère les dossiers de candidature et les entretiens.",
    },
    {
        "code": "direction",
        "label": "Direction NAU",
        "description": "Direction générale du groupe NAU — accès vue stratégique.",
    },
    {
        "code": "student",
        "label": "Candidat / Étudiant",
        "description": "Candidat en cours d'orientation, préinscription et admission.",
    },
]

# ---------------------------------------------------------------------------
# 3. Programmes — core.program
#    degree_level : 'licence', 'prepa', 'cycle_ingenieur', 'master'
# ---------------------------------------------------------------------------
PROGRAMS = [
    # ── IIT : Génie Informatique & Technologies ──────────────────────────────
    {
        "code": "IIT-ING-GLID",
        "school_code": "iit",
        "title": "Cycle Ingénieur — Génie Logiciel & Informatique Décisionnelle",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Ingénieur en Génie Logiciel & Informatique Décisionnelle (GLID)",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Formation d'ingénieurs concepteurs de solutions logicielles avancées, "
            "architectures microservices, DevOps et systèmes décisionnels (BI)."
        ),
        "source_document": "TODO(corpus): IIT_catalogue_ingenieur_2026.pdf",
    },
    {
        "code": "IIT-ING-ARSI",
        "school_code": "iit",
        "title": "Cycle Ingénieur — Administration Réseaux & Sécurité Informatique",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Ingénieur en Réseaux & Sécurité Informatique (ARSI)",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Spécialité dédiée à la cyber-sécurité, aux infrastructures cloud, "
            "à la sécurisation des réseaux d'entreprise et à l'administration système."
        ),
        "source_document": "TODO(corpus): IIT_catalogue_ingenieur_2026.pdf",
    },
    {
        "code": "IIT-ING-DSIA",
        "school_code": "iit",
        "title": "Cycle Ingénieur — Data Science & Intelligence Artificielle",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Ingénieur en Data Science & Intelligence Artificielle (DSIA)",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Formation d'excellence en apprentissage automatique (Machine Learning), "
            "Deep Learning, traitement du langage naturel (NLP) et Big Data."
        ),
        "source_document": "TODO(corpus): IIT_catalogue_ingenieur_2026.pdf",
    },
    # ── IIT : Autres Filières d'Ingénierie ────────────────────────────────────
    {
        "code": "IIT-ING-MECA",
        "school_code": "iit",
        "title": "Cycle Ingénieur — Génie Mécanique",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Ingénieur en Génie Mécanique",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Formation en conception mécanique assistée par ordinateur (CAO), "
            "mécatronique, robotique industrielle et fabrication avancée."
        ),
        "source_document": "TODO(corpus): IIT_catalogue_ingenieur_2026.pdf",
    },
    {
        "code": "IIT-ING-INDUS",
        "school_code": "iit",
        "title": "Cycle Ingénieur — Génie Industriel",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Ingénieur en Génie Industriel & Supply Chain",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Optimisation des processus industriels, logistique, chaîne "
            "d'approvisionnement (Supply Chain), lean manufacturing et industrie 4.0."
        ),
        "source_document": "TODO(corpus): IIT_catalogue_ingenieur_2026.pdf",
    },
    {
        "code": "IIT-ING-ENER",
        "school_code": "iit",
        "title": "Cycle Ingénieur — Génie Énergétique & Environnement",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Ingénieur en Génie Énergétique",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Efficacité énergétique, énergies renouvelables (solaire, éolien), "
            "systèmes thermiques, transition écologique et bilans carbone."
        ),
        "source_document": "TODO(corpus): IIT_catalogue_ingenieur_2026.pdf",
    },
    {
        "code": "IIT-ING-CIVIL",
        "school_code": "iit",
        "title": "Cycle Ingénieur — Génie Civil",
        "degree_level": "cycle_ingenieur",
        "degree_title": "Ingénieur en Génie Civil & Bâtiment",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Calcul des structures béton armé et métallique, géotechnique, "
            "conduite de chantiers de BTP, modélisation BIM et infrastructures."
        ),
        "source_document": "TODO(corpus): IIT_catalogue_ingenieur_2026.pdf",
    },
    # ── IIT : Cycle Préparatoire & Licence ────────────────────────────────────
    {
        "code": "IIT-PREPA",
        "school_code": "iit",
        "title": "Classes Préparatoires Intégrées",
        "degree_level": "prepa",
        "degree_title": "Cycle Préparatoire Scientifique et Technologique",
        "duration_years": 2,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Deux années intensives en mathématiques, physique appliquée et "
            "algorithmique préparant à l'intégration directe en cycle ingénieur IIT."
        ),
        "source_document": "TODO(corpus): IIT_brochure_prepa_2026.pdf",
    },
    {
        "code": "IIT-LIC-INFO",
        "school_code": "iit",
        "title": "Licence en Sciences de l'Informatique",
        "degree_level": "licence",
        "degree_title": "Licence en Sciences Informatiques",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL", "DISTANCIEL"],
        "description": (
            "Formation fondamentale en développement logiciel, bases de données, "
            "systèmes d'exploitation, réseaux et applications web/mobiles."
        ),
        "source_document": "TODO(corpus): IIT_brochure_licence_2026.pdf",
    },
    # ── ISB : Management, Finance & Marketing ────────────────────────────────
    {
        "code": "ISB-LIC-BUSINESS",
        "school_code": "isb",
        "title": "Licence Administration des Affaires",
        "degree_level": "licence",
        "degree_title": "Bachelor en Administration des Affaires",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Management international, entrepreneuriat, stratégie commerciale "
            "et leadership d'entreprise dispensés en français et anglais."
        ),
        "source_document": "TODO(corpus): ISB_catalogue_2026.pdf",
    },
    {
        "code": "ISB-LIC-FINANCE",
        "school_code": "isb",
        "title": "Licence Finance & Comptabilité",
        "degree_level": "licence",
        "degree_title": "Bachelor en Finance et Comptabilité",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Analyse financière, finance d'entreprise, comptabilité générale, "
            "fiscalité, marchés boursiers et audit comptable."
        ),
        "source_document": "TODO(corpus): ISB_catalogue_2026.pdf",
    },
    {
        "code": "ISB-LIC-MARKETING",
        "school_code": "isb",
        "title": "Licence Marketing & Communication Digitale",
        "degree_level": "licence",
        "degree_title": "Bachelor en Marketing Digital",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Stratégie digitale, brand content, communication multicanale, "
            "SEO/SEM, réseaux sociaux et data analytics pour le commerce."
        ),
        "source_document": "TODO(corpus): ISB_catalogue_2026.pdf",
    },
    {
        "code": "ISB-MASTER-MGMT",
        "school_code": "isb",
        "title": "Master Management Stratégique & International",
        "degree_level": "master",
        "degree_title": "Master en Management Stratégique",
        "duration_years": 2,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Direction d'entreprises, pilotage de la transformation numérique, "
            "négociation internationale et management des organisations."
        ),
        "source_document": "TODO(corpus): ISB_catalogue_2026.pdf",
    },
    # ── ISA : Institut Supérieur d'Architecture ──────────────────────────────
    {
        "code": "ISA-ARCHI-1",
        "school_code": "isa",
        "title": "Diplôme du Premier Cycle d'Études en Architecture",
        "degree_level": "licence",
        "degree_title": "Diplôme du Premier Cycle en Architecture",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Cycle initial d'études en architecture : initiation au projet architectural, "
            "théorie de l'espace, dessin technique, géométrie et histoire de l'architecture."
        ),
        "source_document": "TODO(corpus): ISA_catalogue_architecture_2026.pdf",
    },
    {
        "code": "ISA-ARCHI-2",
        "school_code": "isa",
        "title": "Deuxième Cycle — Diplôme National d'Architecte",
        "degree_level": "master",
        "degree_title": "Diplôme National d'Architecte (DNA)",
        "duration_years": 2,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Cycle supérieur menant au titre officiel d'architecte : urbanisme, "
            "conception bioclimatique, sauvegarde du patrimoine et projet de fin d'études."
        ),
        "source_document": "TODO(corpus): ISA_catalogue_architecture_2026.pdf",
    },
    {
        "code": "ISA-LIC-DESIGN",
        "school_code": "isa",
        "title": "Licence en Architecture d'Intérieur & Design d'Espace",
        "degree_level": "licence",
        "degree_title": "Bachelor en Architecture d'Intérieur",
        "duration_years": 3,
        "study_modes": ["PRESENTIEL"],
        "description": (
            "Formation en aménagement d'espaces résidentiels et professionnels, "
            "design d'environnement, mobilier, matériaux innovants et rendu 3D."
        ),
        "source_document": "TODO(corpus): ISA_catalogue_design_2026.pdf",
    },
]

# ---------------------------------------------------------------------------
# 4. Comptes de démonstration — core.accounts + core.account_roles
# ---------------------------------------------------------------------------
DEMO_ACCOUNTS = [
    {
        "email": "admin@smartcampus.tn",
        "full_name": "Admin SmartCampus",
        "auth_provider": "EMAIL_OTP",
        "department": "Direction des Systèmes d'Information",
        "role_code": "admin",
        "school_scope": None,
    },
    {
        "email": "responsable.iit@smartcampus.tn",
        "full_name": "Dr. Mohamed Ben Salah (Resp. IIT)",
        "auth_provider": "EMAIL_OTP",
        "department": "Département Ingénierie & Technologies IIT",
        "role_code": "responsable",
        "school_scope": "iit",
    },
    {
        "email": "responsable.isb@smartcampus.tn",
        "full_name": "Dr. Leila Trabelsi (Resp. ISB)",
        "auth_provider": "EMAIL_OTP",
        "department": "Département Gestion, Finance & Marketing ISB",
        "role_code": "responsable",
        "school_scope": "isb",
    },
    {
        "email": "responsable.isa@smartcampus.tn",
        "full_name": "Arch. Karim Mansour (Resp. ISA)",
        "auth_provider": "EMAIL_OTP",
        "department": "Département Architecture & Urbanisme ISA",
        "role_code": "responsable",
        "school_scope": "isa",
    },
    {
        "email": "rh@smartcampus.tn",
        "full_name": "Sonia Khemir (Chargée Admissions)",
        "auth_provider": "EMAIL_OTP",
        "department": "Service Recrutement & Admissions NAU",
        "role_code": "rh",
        "school_scope": None,
    },
    {
        "email": "direction@smartcampus.tn",
        "full_name": "Direction Générale NAU",
        "auth_provider": "SSO",
        "department": "Direction Générale",
        "role_code": "direction",
        "school_scope": None,
    },
    {
        "email": "candidat.test@gmail.com",
        "full_name": "Ahmed Ben Ahmed",
        "auth_provider": "GOOGLE_OAUTH",
        "department": None,
        "role_code": "student",
        "school_scope": None,
    },
]


# ===========================================================================
# Seed functions (strictement idempotentes)
# ===========================================================================


async def seed_schools(session: AsyncSession) -> None:
    """Insère ou met à jour les 3 écoles (IIT, ISB, ISA) — idempotent."""
    log.info("── Seeding core.school …")
    for s in SCHOOLS:
        await session.execute(
            text(
                """
                INSERT INTO core.school (code, name, is_extension)
                VALUES (:code, :name, :is_extension)
                ON CONFLICT (code) DO UPDATE
                SET name = EXCLUDED.name,
                    is_extension = EXCLUDED.is_extension,
                    updated_at = NOW()
                """
            ),
            s,
        )
    await session.commit()
    log.info("   ✓ core.school : %d établissements vérifiés", len(SCHOOLS))


async def seed_roles(session: AsyncSession) -> None:
    """Insère les 5 rôles applicatifs — idempotent."""
    log.info("── Seeding core.roles …")
    for r in ROLES:
        await session.execute(
            text(
                """
                INSERT INTO core.roles (code, label, description)
                VALUES (:code, :label, :description)
                ON CONFLICT (code) DO UPDATE
                SET label = EXCLUDED.label,
                    description = EXCLUDED.description
                """
            ),
            r,
        )
    await session.commit()
    log.info("   ✓ core.roles : %d rôles vérifiés", len(ROLES))


async def seed_programs(session: AsyncSession) -> dict[str, str]:
    """Insère les programmes pédagogiques. Retourne {code -> uuid}."""
    log.info("── Seeding core.program …")
    program_ids: dict[str, str] = {}

    for p in PROGRAMS:
        result = await session.execute(
            text(
                """
                INSERT INTO core.program
                    (code, school_code, title, degree_level, degree_title,
                     duration_years, study_modes, description, source_document)
                VALUES
                    (:code, :school_code, :title, :degree_level, :degree_title,
                     :duration_years, :study_modes, :description, :source_document)
                ON CONFLICT (code) DO UPDATE
                SET title = EXCLUDED.title,
                    degree_level = EXCLUDED.degree_level,
                    degree_title = EXCLUDED.degree_title,
                    duration_years = EXCLUDED.duration_years,
                    study_modes = EXCLUDED.study_modes,
                    description = EXCLUDED.description,
                    source_document = EXCLUDED.source_document,
                    updated_at = NOW()
                RETURNING id
                """
            ),
            {**p, "study_modes": p["study_modes"]},
        )
        row = result.fetchone()
        if row:
            program_ids[p["code"]] = str(row[0])
        else:
            existing = await session.execute(
                text("SELECT id FROM core.program WHERE code = :code"),
                {"code": p["code"]},
            )
            program_ids[p["code"]] = str(existing.scalar_one())

    await session.commit()
    log.info("   ✓ core.program : %d programmes prêts", len(program_ids))
    return program_ids


async def seed_program_fees(
    session: AsyncSession, program_ids: dict[str, str]
) -> None:
    """Insère les grilles tarifaires annuelles par programme — idempotent."""
    log.info("── Seeding core.program_fee …")

    fees_by_degree = {
        "cycle_ingenieur": 7500.00,
        "prepa": 5500.00,
        "licence": 4800.00,
        "master": 8200.00,
    }

    count = 0
    for prog in PROGRAMS:
        prog_id = program_ids.get(prog["code"])
        if not prog_id:
            continue

        amount = fees_by_degree.get(prog["degree_level"], 5000.00)
        # ISA Architecture a une grille spécialisée
        if "ARCHI" in prog["code"]:
            amount = 8900.00 if prog["degree_level"] == "master" else 6800.00

        await session.execute(
            text(
                """
                INSERT INTO core.program_fee
                    (program_id, academic_year, tuition_amount, currency,
                     payment_schedule, effective_from, source_document)
                VALUES
                    (:program_id, '2026-2027', :tuition_amount, 'TND',
                     :payment_schedule, :effective_from, :source_document)
                ON CONFLICT (program_id, academic_year, effective_from) DO UPDATE
                SET tuition_amount = EXCLUDED.tuition_amount,
                    payment_schedule = EXCLUDED.payment_schedule
                """
            ),
            {
                "program_id": prog_id,
                "tuition_amount": amount,
                "payment_schedule": (
                    "Paiement en 3 tranches (Septembre 40%, Janvier 30%, Avril 30%)"
                ),
                "effective_from": date(2026, 9, 1),
                "source_document": prog["source_document"],
            },
        )
        count += 1

    await session.commit()
    log.info("   ✓ core.program_fee : %d grilles tarifaires configurées", count)


async def seed_program_prerequisites(
    session: AsyncSession, program_ids: dict[str, str]
) -> None:
    """Insère les règles d'éligibilité Gatekeeper pour chaque filière — idempotent."""
    log.info("── Seeding core.program_prerequisite …")

    prereqs = [
        # IIT Génie Info (GLID, ARSI, DSIA)
        ("IIT-ING-GLID", "bac", "Baccalauréat Mathématiques / Sciences / Info", 12.00),
        ("IIT-ING-GLID", "prepa", "Concours National aux Écoles d'Ingénieurs", 10.00),
        ("IIT-ING-GLID", "passerelle", "Licence Informatique ou équivalent (Bac+3)", 12.00),
        ("IIT-ING-ARSI", "bac", "Baccalauréat Mathématiques / Sciences / Technique", 11.50),
        ("IIT-ING-ARSI", "prepa", "Prépa Intégrée ou Concours National", 10.00),
        ("IIT-ING-DSIA", "bac", "Baccalauréat Mathématiques ou Sciences", 12.50),
        ("IIT-ING-DSIA", "prepa", "Concours National aux Écoles d'Ingénieurs", 10.00),
        ("IIT-ING-DSIA", "passerelle", "Licence Data / Info / Mathématiques appliquées", 12.50),
        # IIT Autres Génies (Mécanique, Industriel, Énergétique, Civil)
        ("IIT-ING-MECA", "bac", "Baccalauréat Technique, Maths ou Sciences", 11.50),
        ("IIT-ING-MECA", "prepa", "Prépa MP / PC / PT ou Concours National", 10.00),
        ("IIT-ING-INDUS", "bac", "Baccalauréat Scientifique ou Technique", 11.50),
        ("IIT-ING-INDUS", "prepa", "Prépa Scientifique ou Concours National", 10.00),
        ("IIT-ING-ENER", "bac", "Baccalauréat Sciences, Maths ou Technique", 11.50),
        ("IIT-ING-CIVIL", "bac", "Baccalauréat Sciences, Maths ou Technique", 12.00),
        ("IIT-ING-CIVIL", "prepa", "Prépa MP / PC / PT ou Concours National", 10.00),
        # IIT Prépa & Licence
        ("IIT-PREPA", "bac", "Baccalauréat Sciences, Maths ou Technique", 12.50),
        ("IIT-LIC-INFO", "bac", "Baccalauréat toutes filières scientifiques", 10.00),
        # ISB Business & Management
        ("ISB-LIC-BUSINESS", "bac", "Baccalauréat Économie / Gestion ou Général", 11.00),
        ("ISB-LIC-FINANCE", "bac", "Baccalauréat Économie / Gestion ou Maths", 11.50),
        ("ISB-LIC-MARKETING", "bac", "Baccalauréat toutes filières", 10.00),
        ("ISB-MASTER-MGMT", "licence", "Licence en Sciences de Gestion / Économie", 12.50),
        ("ISB-MASTER-MGMT", "passerelle", "Diplôme Bac+3 équivalent avec entretien", 12.00),
        # ISA Architecture
        ("ISA-ARCHI-1", "bac", "Baccalauréat Sciences, Maths ou Technique", 11.50),
        ("ISA-ARCHI-2", "passerelle", "Diplôme 1er Cycle Architecture (Bac+3)", 12.00),
        ("ISA-LIC-DESIGN", "bac", "Baccalauréat toutes filières + entretien artistique", 10.00),
    ]

    for prog_code, track, diploma, min_avg in prereqs:
        prog_id = program_ids.get(prog_code)
        if not prog_id:
            continue

        existing = await session.execute(
            text(
                """
                SELECT 1 FROM core.program_prerequisite
                WHERE program_id = :program_id
                  AND entry_track = :entry_track
                  AND required_diploma = :required_diploma
                """
            ),
            {
                "program_id": prog_id,
                "entry_track": track,
                "required_diploma": diploma,
            },
        )
        if not existing.fetchone():
            await session.execute(
                text(
                    """
                    INSERT INTO core.program_prerequisite
                        (program_id, entry_track, required_diploma, min_average,
                         notes, source_document)
                    VALUES
                        (:program_id, :entry_track, :required_diploma, :min_average,
                         '', :source_document)
                    """
                ),
                {
                    "program_id": prog_id,
                    "entry_track": track,
                    "required_diploma": diploma,
                    "min_average": min_avg,
                    "source_document": "TODO(corpus): grille_admission_NAU_2026.pdf",
                },
            )

    await session.commit()
    log.info("   ✓ core.program_prerequisite : %d prérequis vérifiés", len(prereqs))


async def seed_admission_campaigns(session: AsyncSession) -> None:
    """Insère une campagne d'admission 2026-2027 ouverte par école — idempotent."""
    log.info("── Seeding core.admission_campaign …")

    now = datetime.now(UTC)
    campaigns = [
        {
            "school_code": "iit",
            "academic_year": "2026-2027",
            "phase_name": "Campagne Principale Admissions IIT 2026-2027",
            "opens_at": now - timedelta(days=10),
            "closes_at": now + timedelta(days=120),
            "source_document": "TODO(corpus): calendrier_admission_IIT_2026.pdf",
        },
        {
            "school_code": "isb",
            "academic_year": "2026-2027",
            "phase_name": "Campagne Principale Admissions ISB 2026-2027",
            "opens_at": now - timedelta(days=10),
            "closes_at": now + timedelta(days=120),
            "source_document": "TODO(corpus): calendrier_admission_ISB_2026.pdf",
        },
        {
            "school_code": "isa",
            "academic_year": "2026-2027",
            "phase_name": "Campagne Principale Admissions ISA 2026-2027",
            "opens_at": now - timedelta(days=10),
            "closes_at": now + timedelta(days=120),
            "source_document": "TODO(corpus): calendrier_admission_ISA_2026.pdf",
        },
    ]

    for c in campaigns:
        existing = await session.execute(
            text(
                """
                SELECT 1 FROM core.admission_campaign
                WHERE school_code = :school_code
                  AND academic_year = :academic_year
                  AND phase_name = :phase_name
                """
            ),
            {
                "school_code": c["school_code"],
                "academic_year": c["academic_year"],
                "phase_name": c["phase_name"],
            },
        )
        if not existing.fetchone():
            await session.execute(
                text(
                    """
                    INSERT INTO core.admission_campaign
                        (school_code, academic_year, phase_name,
                         opens_at, closes_at, is_open, source_document)
                    VALUES
                        (:school_code, :academic_year, :phase_name,
                         :opens_at, :closes_at, TRUE, :source_document)
                    """
                ),
                c,
            )

    await session.commit()
    log.info("   ✓ core.admission_campaign : 3 campagnes actives")


async def seed_accounts_and_roles(session: AsyncSession) -> dict[str, str]:
    """Insère les comptes de démonstration et leurs rôles — idempotent.
    Retourne {email -> account_uuid}.
    """
    log.info("── Seeding core.accounts + core.account_roles …")
    account_ids: dict[str, str] = {}

    for acc in DEMO_ACCOUNTS:
        await session.execute(
            text(
                """
                INSERT INTO core.accounts
                    (email, full_name, auth_provider, department, is_active,
                     email_verified_at)
                VALUES
                    (:email, :full_name, :auth_provider, :department, TRUE, NOW())
                ON CONFLICT (email) DO UPDATE
                SET full_name = EXCLUDED.full_name,
                    department = EXCLUDED.department,
                    updated_at = NOW()
                """
            ),
            {
                "email": acc["email"],
                "full_name": acc["full_name"],
                "auth_provider": acc["auth_provider"],
                "department": acc["department"],
            },
        )

        acc_id_row = await session.execute(
            text("SELECT id FROM core.accounts WHERE email = :email"),
            {"email": acc["email"]},
        )
        account_id = str(acc_id_row.scalar_one())
        account_ids[acc["email"]] = account_id

        role_result = await session.execute(
            text("SELECT id FROM core.roles WHERE code = :code"),
            {"code": acc["role_code"]},
        )
        role_id = role_result.scalar_one()

        existing_role = await session.execute(
            text(
                """
                SELECT 1 FROM core.account_roles
                WHERE account_id = :account_id
                  AND role_id = :role_id
                  AND COALESCE(school_scope, 'GLOBAL') = COALESCE(:school_scope, 'GLOBAL')
                """
            ),
            {
                "account_id": account_id,
                "role_id": role_id,
                "school_scope": acc["school_scope"],
            },
        )
        if not existing_role.fetchone():
            await session.execute(
                text(
                    """
                    INSERT INTO core.account_roles
                        (account_id, role_id, school_scope, responsibility_scope)
                    VALUES
                        (:account_id, :role_id, :school_scope, 'ALL')
                    """
                ),
                {
                    "account_id": account_id,
                    "role_id": role_id,
                    "school_scope": acc["school_scope"],
                },
            )

    await session.commit()
    log.info("   ✓ core.accounts : %d comptes prêts", len(account_ids))
    return account_ids


async def seed_candidate_and_grades(
    session: AsyncSession, account_ids: dict[str, str]
) -> str:
    """Insère un candidat de démonstration avec profil et matières déclarées."""
    log.info("── Seeding candidate de test + matières déclarées …")

    student_account_id = account_ids.get("candidat.test@gmail.com")

    # 1. Candidat
    await session.execute(
        text(
            """
            INSERT INTO core.candidate
                (account_id, candidate_number, email, birth_date,
                 status, school_target, owner)
            VALUES
                (:account_id, 'CAND-2026-0001', 'candidat.test@gmail.com', '2005-06-15',
                 'AUTHENTICATED', 'iit', 'JARVIS')
            ON CONFLICT (candidate_number) DO UPDATE
            SET school_target = EXCLUDED.school_target,
                status = EXCLUDED.status,
                updated_at = NOW()
            """
        ),
        {"account_id": student_account_id},
    )

    cand_row = await session.execute(
        text("SELECT id FROM core.candidate WHERE candidate_number = 'CAND-2026-0001'")
    )
    candidate_id = str(cand_row.scalar_one())

    # 2. Profil
    await session.execute(
        text(
            """
            INSERT INTO core.candidate_profile
                (candidate_id, current_education_level, current_specialty,
                 declared_overall_avg, preferred_language, summary, facts)
            VALUES
                (:candidate_id, 'BAC', 'Sciences Expérimentales', 14.50, 'FR',
                 'Candidat titulaire d''un Bac Sciences avec mention bien.',
                 '{"interet": "IA", "motivation": "Devenir ingénieur en IA"}'::jsonb)
            ON CONFLICT (candidate_id) DO UPDATE
            SET current_education_level = EXCLUDED.current_education_level,
                current_specialty = EXCLUDED.current_specialty,
                declared_overall_avg = EXCLUDED.declared_overall_avg,
                summary = EXCLUDED.summary,
                updated_at = NOW()
            """
        ),
        {"candidate_id": candidate_id},
    )

    # 3. Matières déclarées (Baccalauréat)
    grades = [
        ("Mathématiques", 15.50, 20.00, 4.0),
        ("Sciences Physiques", 14.00, 20.00, 4.0),
        ("Informatique & Algorithmique", 17.50, 20.00, 3.0),
        ("Français", 13.00, 20.00, 2.0),
        ("Anglais", 16.50, 20.00, 2.0),
        ("Philosophie", 11.00, 20.00, 1.0),
    ]

    for subject, mark, scale, coeff in grades:
        existing_grade = await session.execute(
            text(
                """
                SELECT 1 FROM core.declared_grades
                WHERE candidate_id = :candidate_id
                  AND diploma_stage = 'BAC'
                  AND subject_name = :subject_name
                """
            ),
            {"candidate_id": candidate_id, "subject_name": subject},
        )
        if not existing_grade.fetchone():
            await session.execute(
                text(
                    """
                    INSERT INTO core.declared_grades
                        (candidate_id, academic_year, diploma_stage, subject_name,
                         grade_value, grade_scale, coefficient, entry_source, confidence_score)
                    VALUES
                        (:candidate_id, '2025-2026', 'BAC', :subject_name,
                         :grade_value, :grade_scale, :coefficient, 'DECLARED_CHAT', 1.0)
                    """
                ),
                {
                    "candidate_id": candidate_id,
                    "subject_name": subject,
                    "grade_value": mark,
                    "grade_scale": scale,
                    "coefficient": coeff,
                },
            )

    await session.commit()
    log.info("   ✓ core.candidate : CAND-2026-0001 (Ahmed Ben Ahmed)")
    log.info("   ✓ core.candidate_profile : profil Bac Sciences (moyenne 14.50)")
    log.info("   ✓ core.declared_grades : %d matières déclarées", len(grades))
    return candidate_id


async def seed_slots(
    session: AsyncSession, account_ids: dict[str, str]
) -> None:
    """Insère 12 créneaux d'entretien FREE pour IIT, ISB et ISA — idempotent."""
    log.info("── Seeding core.slot …")

    responsables = [
        ("responsable.iit@smartcampus.tn", "iit", "Salle Entretiens IIT — Bâtiment A, Bureau 102"),
        ("responsable.isb@smartcampus.tn", "isb", "Salle Entretiens ISB — Bâtiment B, Bureau 204"),
        ("responsable.isa@smartcampus.tn", "isa", "Atelier Architecture ISA — Bureau 301"),
    ]

    base_date = date.today() + timedelta(days=3)

    for email, school_code, location in responsables:
        resp_id = account_ids.get(email)
        if not resp_id:
            continue

        for day_offset in range(4):
            slot_date = base_date + timedelta(days=day_offset * 2)
            start_hour = 9 if school_code == "iit" else (11 if school_code == "isa" else 14)
            start_time = datetime(
                slot_date.year, slot_date.month, slot_date.day, start_hour, 0, tzinfo=UTC
            )

            await session.execute(
                text(
                    """
                    INSERT INTO core.slot
                        (responsable_id, school_code, start_time, end_time,
                         room_or_link, status)
                    VALUES
                        (:responsable_id, :school_code, :start_time, :end_time,
                         :location, 'FREE')
                    ON CONFLICT (responsable_id, start_time) DO NOTHING
                    """
                ),
                {
                    "responsable_id": resp_id,
                    "school_code": school_code,
                    "start_time": start_time,
                    "end_time": start_time + timedelta(minutes=30),
                    "location": location,
                },
            )

    await session.commit()
    log.info("   ✓ core.slot : 12 créneaux d'entretien FREE configurés")


# ===========================================================================
# Main
# ===========================================================================


async def main() -> None:
    log.info("=" * 65)
    log.info("  SmartCampus AI — Seed de Démonstration (SA-18)")
    log.info("  DATABASE_URL : %s", DATABASE_URL.replace("smartcampus_app_secret", "***"))
    log.info("=" * 65)

    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # Vérification préalable
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'core' AND table_name = 'school'"
            )
        )
        if not result.scalar():
            log.error("❌ Tables introuvables. Appliquez d'abord les migrations.")
            sys.exit(1)

        await seed_schools(session)
        await seed_roles(session)
        program_ids = await seed_programs(session)
        await seed_program_fees(session, program_ids)
        await seed_program_prerequisites(session, program_ids)
        await seed_admission_campaigns(session)
        account_ids = await seed_accounts_and_roles(session)
        await seed_candidate_and_grades(session, account_ids)
        await seed_slots(session, account_ids)

    await engine.dispose()

    log.info("=" * 65)
    log.info("  ✅ SEED COMPLET & VALIDÉ AVEC SUCCÈS")
    log.info("  - 3 écoles NAU (IIT, ISB, ISA - Institut Supérieur d'Architecture)")
    log.info("  - 5 rôles RBAC (admin, responsable, rh, direction, student)")
    log.info("  - %d programmes pédagogiques & spécialités", len(PROGRAMS))
    log.info("  - Grilles tarifaires 2026-2027 configurées")
    log.info("  - Règles de prérequis Gatekeeper configurées")
    log.info("  - 3 campagnes d'admission actives")
    log.info("  - %d comptes utilisateurs et responsables", len(DEMO_ACCOUNTS))
    log.info("  - 1 candidat témoin avec relevé de notes")
    log.info("  - 12 créneaux d'entretien (IIT, ISB, ISA)")
    log.info("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
