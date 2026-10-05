"""Registro de proveniência: source -> collection -> collection_file -> parse.

Nenhum crawler escreve dado sem antes passar por aqui. O `provenance_id` que
o Collector devolve é obrigatório em toda tabela de fato, por constraint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from .fetch import FetchedFile, download

MANIFEST_PATH = Path("manifest.json")

# Fontes conhecidas. Cadastradas uma vez, estáveis.
SOURCES: dict[str, dict[str, str]] = {
    "tse": {
        "name": "Tribunal Superior Eleitoral — Dados Abertos",
        "base_url": "https://dadosabertos.tse.jus.br/",
        "legal_basis": "Lei 9.504/1997 art. 28 §4; Res. TSE 23.607/2019",
    },
    "portal_transparencia": {
        "name": "Portal da Transparência — CGU",
        "base_url": "https://portaldatransparencia.gov.br/",
        "legal_basis": "Lei 12.527/2011 (LAI); Lei 12.846/2013 art. 22-23",
    },
    "camara": {
        "name": "Câmara dos Deputados — Dados Abertos",
        "base_url": "https://dadosabertos.camara.leg.br/",
        "legal_basis": "Lei 12.527/2011 (LAI); Ato da Mesa 45/2012",
    },
    "senado": {
        "name": "Senado Federal — Dados Abertos",
        "base_url": "https://legis.senado.leg.br/dadosabertos/",
        "legal_basis": "Lei 12.527/2011 (LAI)",
    },
    "receita": {
        "name": "Receita Federal (via BrasilAPI)",
        "base_url": "https://brasilapi.com.br/",
        "legal_basis": "Dados cadastrais de CNPJ são públicos (IN RFB 2.119/2022)",
    },
}


@dataclass
class ParseHandle:
    """Referência a uma leitura em andamento. Vira o provenance_id das linhas."""

    id: int
    inner_path: str
    parser: str
    reference_year: int | None
    rows_read: int = 0
    rows_written: int = 0
    rows_rejected: int = 0


class Collector:
    """Dono do ciclo de vida de uma execução de crawler.

    Uso:
        with Collector(conn, "tse", "tse-candidates", "1.0") as col:
            f = col.fetch("https://.../consulta_cand_2022.zip")
            with col.parse(f, "consulta_cand_2022_SP.csv", "tse.cand.v1", 2022) as p:
                ...  # escreve linhas com provenance_id = p.id
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        source_slug: str,
        crawler: str,
        crawler_version: str,
        args: dict[str, Any] | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self.conn = conn
        self.source_slug = source_slug
        self.crawler = crawler
        self.crawler_version = crawler_version
        self.args = args or {}
        self.work_dir = work_dir or Path("dados_tmp")
        self.collection_id: int | None = None
        self.source_id: int | None = None
        self._files: list[FetchedFile] = []
        self._parses: list[ParseHandle] = []

    # -- ciclo de vida ------------------------------------------------------

    def __enter__(self) -> "Collector":
        self.source_id = self._ensure_source()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection (source_id, crawler, crawler_version, started_at, args)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.source_id,
                    self.crawler,
                    self.crawler_version,
                    datetime.now(timezone.utc),
                    json.dumps(self.args),
                ),
            )
            self.collection_id = cur.fetchone()["id"]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        status = "ok" if exc_type is None else "failed"
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE collection SET finished_at = %s, status = %s WHERE id = %s",
                (datetime.now(timezone.utc), status, self.collection_id),
            )
        if exc_type is None:
            self.conn.commit()
            self.write_manifest()

    def _ensure_source(self) -> int:
        meta = SOURCES.get(self.source_slug)
        if meta is None:
            raise ValueError(f"fonte desconhecida: {self.source_slug}")
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source (slug, name, base_url, legal_basis)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (self.source_slug, meta["name"], meta["base_url"], meta["legal_basis"]),
            )
            return cur.fetchone()["id"]

    # -- coleta -------------------------------------------------------------

    def fetch(self, url: str, filename: str | None = None) -> FetchedFile:
        """Baixa e registra o arquivo. Devolve o objeto com o hash."""
        fetched = download(url, self.work_dir, filename=filename)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_file
                    (collection_id, url, accessed_at, http_status, byte_size, sha256, media_type, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.collection_id,
                    fetched.url,
                    fetched.accessed_at,
                    fetched.http_status,
                    fetched.byte_size,
                    fetched.sha256,
                    fetched.media_type,
                    "reaproveitado de dados_tmp" if fetched.from_cache else None,
                ),
            )
            fetched_id = cur.fetchone()["id"]
        setattr(fetched, "db_id", fetched_id)
        self._files.append(fetched)
        print(
            f"  baixado  {fetched.url}\n"
            f"           {fetched.byte_size / 1e6:,.1f} MB  sha256={fetched.sha256[:16]}..."
            + ("  (cache local)" if fetched.from_cache else "")
        )
        return fetched

    def parse(
        self,
        fetched: FetchedFile,
        inner_path: str,
        parser: str,
        reference_year: int | None = None,
    ) -> "ParseContext":
        return ParseContext(self, fetched, inner_path, parser, reference_year)

    def _open_parse(
        self,
        fetched: FetchedFile,
        inner_path: str,
        parser: str,
        reference_year: int | None,
    ) -> ParseHandle:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO parse
                    (collection_file_id, inner_path, inner_sha256, parser, reference_year)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    getattr(fetched, "db_id"),
                    inner_path,
                    fetched.inner_hashes.get(inner_path),
                    parser,
                    reference_year,
                ),
            )
            handle = ParseHandle(
                id=cur.fetchone()["id"],
                inner_path=inner_path,
                parser=parser,
                reference_year=reference_year,
            )
        self._parses.append(handle)
        return handle

    def _close_parse(self, handle: ParseHandle) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE parse
                SET rows_read = %s, rows_written = %s, rows_rejected = %s
                WHERE id = %s
                """,
                (handle.rows_read, handle.rows_written, handle.rows_rejected, handle.id),
            )

    # -- rewrite-only -------------------------------------------------------

    def truncate(self, *tables: str) -> None:
        """Apaga as tabelas que este crawler possui, antes de reconstruir.

        Rewrite-only é a garantia contra "editar o passado": o banco é
        reconstruído do zero a partir das fontes, e o manifest.json commitado
        prova o que foi coletado e quando.
        """
        with self.conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")  # type: ignore[arg-type]
        print(f"  rewrite-only: {', '.join(tables)} zeradas")

    # -- manifesto ----------------------------------------------------------

    def write_manifest(self, path: Path = MANIFEST_PATH) -> None:
        """Grava/atualiza o manifest.json — a âncora de não repúdio.

        Este arquivo vai para o git. O histórico público do repositório passa
        a provar o que foi coletado, de onde e quando.
        """
        manifest: dict[str, Any] = {}
        if path.exists():
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}

        entries = manifest.setdefault("collections", {})
        entries[f"{self.crawler}@{self.collection_id}"] = {
            "source": self.source_slug,
            "crawler": self.crawler,
            "crawler_version": self.crawler_version,
            "args": self.args,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "files": [f.manifest_entry() for f in self._files],
            "parses": [
                {
                    "inner_path": p.inner_path,
                    "parser": p.parser,
                    "reference_year": p.reference_year,
                    "rows_read": p.rows_read,
                    "rows_written": p.rows_written,
                    "rows_rejected": p.rows_rejected,
                }
                for p in self._parses
            ],
        }
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  manifesto atualizado: {path}")


class ParseContext:
    """Context manager que abre/fecha um `parse` e conta as linhas."""

    def __init__(
        self,
        collector: Collector,
        fetched: FetchedFile,
        inner_path: str,
        parser: str,
        reference_year: int | None,
    ) -> None:
        self._collector = collector
        self._fetched = fetched
        self._inner_path = inner_path
        self._parser = parser
        self._year = reference_year
        self.handle: ParseHandle | None = None

    def __enter__(self) -> ParseHandle:
        self.handle = self._collector._open_parse(
            self._fetched, self._inner_path, self._parser, self._year
        )
        return self.handle

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle is not None:
            self._collector._close_parse(self.handle)
            h = self.handle
            print(
                f"    {h.inner_path}: {h.rows_read:,} lidas, "
                f"{h.rows_written:,} gravadas, {h.rows_rejected:,} descartadas"
            )
