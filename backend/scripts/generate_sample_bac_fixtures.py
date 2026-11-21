"""Generate anonymized Tunisian Baccalaureate (Sfax) sample PDF & PNG fixtures (Bonus-1 / SA-42).

Strictly INPDP-compliant (Law 2004-63): uses synthetic anonymized student identities while
faithfully reproducing the official Tunisian Ministère de l'Éducation transcript layout,
subject lists, and official section coefficients for Sfax lycées.
"""

from __future__ import annotations

from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "data" / "sample_bac"

MATH_SFAX_LINES = [
    "REPUBLIQUE TUNISIENNE - MINISTERE DE L'EDUCATION",
    "RELEVE DE NOTES - EXAMEN DU BACCALAUREAT",
    "Session Principale : 2026",
    "Candidat : Foulen Ben Foulen (Anonymise INPDP)",
    "Numero d'inscription : 20260001 | CIN : 00000000",
    "Etablissement : Lycee Pilote de Sfax",
    "Gouvernorat : Sfax",
    "Section : Mathematiques",
    "------------------------------------------------------------",
    "Matiere | Coeff | Note",
    "Mathematiques | 4 | 18.25",
    "Sciences Physiques | 4 | 17.50",
    "Sciences de la Vie et de la Terre | 1 | 15.00",
    "Francais | 1 | 15.50",
    "Anglais | 1 | 16.50",
    "Philosophie | 1 | 13.75",
    "Arabe | 1 | 14.25",
    "Informatique | 1 | 18.00",
    "Education Physique | 1 | 16.00",
    "------------------------------------------------------------",
    "Moyenne Generale : 16.80",
    "Mention : Tres Bien",
    "Decision du jury : Admis",
]

INFO_SFAX_LINES = [
    "REPUBLIQUE TUNISIENNE - MINISTERE DE L'EDUCATION",
    "RELEVE DE NOTES - EXAMEN DU BACCALAUREAT",
    "Session Principale : 2026",
    "Candidat : Amine Ben Salah (Anonymise INPDP)",
    "Numero d'inscription : 20260042 | CIN : 00000000",
    "Etablissement : Lycee Hedi Chaker Sfax",
    "Gouvernorat : Sfax",
    "Section : Sciences de l'Informatique",
    "------------------------------------------------------------",
    "Matiere | Coeff | Note",
    "Algorithmique et Programmation | 3 | 17.50",
    "Mathematiques | 3 | 15.50",
    "Sciences de la Technologie | 3 | 16.25",
    "Sciences Physiques | 2 | 14.00",
    "Francais | 1 | 14.50",
    "Anglais | 1 | 16.00",
    "Philosophie | 1 | 12.50",
    "Arabe | 1 | 13.00",
    "Education Physique | 1 | 15.50",
    "------------------------------------------------------------",
    "Moyenne Generale : 15.45",
    "Mention : Bien",
    "Decision du jury : Admis",
]


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_text_pdf(path: Path, lines: list[str]) -> None:
    """Write a valid single-page PDF 1.4 with an embedded Helvetica text layer."""
    stream_cmds = ["BT", "/F1 11 Tf", "50 780 Td", "18 TL"]
    for idx, line in enumerate(lines):
        escaped = _pdf_escape(line)
        if idx == 0:
            stream_cmds.append(f"({escaped}) Tj")
        else:
            stream_cmds.append(f"T* ({escaped}) Tj")
    stream_cmds.append("ET")
    stream_bytes = "\n".join(stream_cmds).encode("latin-1")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        (
            f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        ),
    ]

    header = b"%PDF-1.4\n"
    offsets: list[int] = []
    body = bytearray(header)
    for obj in objects:
        offsets.append(len(body))
        body.extend(obj)

    xref_pos = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for off in offsets:
        body.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("ascii")
    body.extend(trailer)
    path.write_bytes(bytes(body))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_simple_text_pdf(OUT_DIR / "releve_bac_math_sfax_2026.pdf", MATH_SFAX_LINES)
    write_simple_text_pdf(OUT_DIR / "releve_bac_info_sfax_2026.pdf", INFO_SFAX_LINES)
    print(f"[OK] Generated 2 anonymized Sfax Tunisian Bac PDF fixtures in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
