"""Conexão com o Postgres e criação do schema.

O banco é remoto (Supabase, Neon, RDS, qualquer Postgres). A string de
conexão vem de MIRANTE_DATABASE_URL e nunca é commitada.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from importlib import resources
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

# Extensões necessárias antes do schema. pg_trgm sustenta a busca por nome
# (índices gin_trgm_ops), que é como se acha um doador que nunca foi candidato.
EXTENSIONS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS btree_gin",
    # unaccent é usada pelas regras que casam descrição de despesa:
    # "ADESIVAÇÃO" e "ADESIVACAO" precisam bater.
    "CREATE EXTENSION IF NOT EXISTS unaccent",
]


class ConfigError(RuntimeError):
    pass


def database_url() -> str:
    url = os.environ.get("MIRANTE_DATABASE_URL")
    if not url:
        raise ConfigError(
            "MIRANTE_DATABASE_URL não definida. Exporte a string de conexão do "
            "seu Postgres antes de rodar:\n"
            "  export MIRANTE_DATABASE_URL='postgresql://user:senha@host:5432/postgres'"
        )
    return url


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Abre conexão com row_factory=dict_row. Commita ao sair sem exceção."""
    conn = psycopg.connect(database_url(), row_factory=dict_row, autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def read_schema() -> str:
    return resources.files("mirante.db").joinpath("schema.sql").read_text(encoding="utf-8")


def init_db() -> list[str]:
    """Cria extensões e schema. Idempotente — tudo é IF NOT EXISTS."""
    created: list[str] = []
    with connect() as conn, conn.cursor() as cur:
        for stmt in EXTENSIONS:
            try:
                cur.execute(stmt)  # type: ignore[arg-type]
                created.append(stmt)
            except psycopg.errors.InsufficientPrivilege:
                # Alguns provedores gerenciados já trazem as extensões e
                # bloqueiam CREATE EXTENSION. Se já existe, seguimos.
                conn.rollback()
        cur.execute(read_schema())  # type: ignore[arg-type]
        created.append("schema.sql")
    return created


def table_counts() -> dict[str, int]:
    """Contagem por tabela. Usa estatísticas do planner (rápido em base grande)."""
    sql = """
        SELECT relname AS table_name,
               GREATEST(n_live_tup, 0) AS approx_rows
        FROM pg_stat_user_tables
        ORDER BY relname
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return {r["table_name"]: r["approx_rows"] for r in cur.fetchall()}
