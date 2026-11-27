"""Align knowledge.chunks to BAAI/bge-m3 (1024d) and widen actor check constraints.

Revision ID: 20261127_0003
Revises: 20261116_0002
Create Date: 2026-11-27 11:30:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261127_0003"
down_revision: str | Sequence[str] | None = "20261116_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _exec_statements(*statements: str) -> None:
    """Execute one SQL command per op.execute() call (required by asyncpg prepared stmts)."""
    for stmt in statements:
        op.execute(stmt)


def upgrade() -> None:
    """Switch knowledge.chunks.embedding to vector(1024) (bge-m3) and widen actor constraints."""
    _exec_statements(
        "DROP INDEX IF EXISTS knowledge.idx_knowledge_chunks_embedding_hnsw",
        "DELETE FROM knowledge.chunks WHERE vector_dims(embedding) <> 1024",
        "ALTER TABLE knowledge.chunks ALTER COLUMN embedding TYPE vector(1024)",
        """
        CREATE INDEX idx_knowledge_chunks_embedding_hnsw
            ON knowledge.chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """,
        "ALTER TABLE core.document_sync_jobs ALTER COLUMN embedding_model SET DEFAULT 'bge-m3'",
        "ALTER TABLE core.roles DROP CONSTRAINT IF EXISTS roles_code_valid",
        """
        ALTER TABLE core.roles
            ADD CONSTRAINT roles_code_valid
            CHECK (code IN ('student', 'candidat', 'rh', 'responsable', 'direction', 'admin'))
        """,
        "ALTER TABLE core.audit_log DROP CONSTRAINT IF EXISTS audit_actor_type_valid",
        """
        ALTER TABLE core.audit_log
            ADD CONSTRAINT audit_actor_type_valid
            CHECK (
                actor_type IN (
                    'VISITEUR',
                    'CANDIDAT',
                    'STUDENT',
                    'RH',
                    'RESPONSABLE',
                    'DIRECTION',
                    'ADMIN',
                    'VISION',
                    'JARVIS',
                    'SYSTEM'
                )
            )
        """,
    )


def downgrade() -> None:
    """Revert actor check constraints and restore knowledge.chunks.embedding to vector(1536)."""
    _exec_statements(
        "ALTER TABLE core.audit_log DROP CONSTRAINT IF EXISTS audit_actor_type_valid",
        """
        UPDATE core.audit_log
            SET actor_type = 'STUDENT'
            WHERE actor_type IN ('CANDIDAT', 'VISITEUR')
        """,
        """
        ALTER TABLE core.audit_log
            ADD CONSTRAINT audit_actor_type_valid
            CHECK (
                actor_type IN (
                    'STUDENT',
                    'RH',
                    'RESPONSABLE',
                    'DIRECTION',
                    'ADMIN',
                    'VISION',
                    'JARVIS',
                    'SYSTEM'
                )
            )
        """,
        "ALTER TABLE core.roles DROP CONSTRAINT IF EXISTS roles_code_valid",
        "DELETE FROM core.roles WHERE code = 'candidat'",
        """
        ALTER TABLE core.roles
            ADD CONSTRAINT roles_code_valid
            CHECK (code IN ('student', 'rh', 'responsable', 'direction', 'admin'))
        """,
        (
            "ALTER TABLE core.document_sync_jobs "
            "ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-small'"
        ),
        "DROP INDEX IF EXISTS knowledge.idx_knowledge_chunks_embedding_hnsw",
        "DELETE FROM knowledge.chunks WHERE vector_dims(embedding) <> 1536",
        "ALTER TABLE knowledge.chunks ALTER COLUMN embedding TYPE vector(1536)",
        """
        CREATE INDEX idx_knowledge_chunks_embedding_hnsw
            ON knowledge.chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """,
    )
