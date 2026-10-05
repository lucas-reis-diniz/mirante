"""Infra compartilhada entre crawlers: leitura de CSV e resolução de identidade.

A parte delicada aqui é `EntityResolver`. Identidade é afirmação nossa, não
dado da fonte. Quando o CPF é ambíguo (mesmo CPF aparecendo sob títulos
eleitorais diferentes), NÃO afirmamos nada: descartamos o CPF e registramos
o motivo em `rejected_cpf`. O que não afirmamos faz parte da prova.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from typing import Iterable, Iterator

import psycopg

from .. import normalize as nz

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

CSV_DIALECT = {"delimiter": ";", "quotechar": '"'}
BATCH = 5_000


def read_csv(stream, delimiter: str = ";") -> Iterator[dict[str, str]]:
    """DictReader tolerante: normaliza cabeçalho e ignora BOM."""
    reader = csv.DictReader(stream, delimiter=delimiter, quotechar='"')
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lstrip("﻿").upper() for f in reader.fieldnames]
    for row in reader:
        yield {k: v for k, v in row.items() if k is not None}


def chunked(rows: Iterable, size: int = BATCH) -> Iterator[list]:
    buf: list = []
    for row in rows:
        buf.append(row)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


@dataclass
class ResolverStats:
    people_created: int = 0
    people_matched: int = 0
    cpf_rejected: int = 0
    companies_created: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


class EntityResolver:
    """Cache em memória de pessoas e empresas, com escrita preguiçosa.

    O cache importa: um arquivo de doações tem milhões de linhas e poucos
    milhares de doadores distintos. Sem cache, é um SELECT por linha.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self._by_cpf: dict[str, int] = {}
        self._by_voter: dict[str, int] = {}
        self._by_cnpj: dict[str, int] = {}
        # (company_id, kind) já gravados. Sem isto, cada linha de um arquivo
        # com 200 mil despesas fazia um UPDATE de `kind` no banco.
        self._kinds: set[tuple[int, str]] = set()
        self._cpf_voter_seen: dict[str, str] = {}
        self._rejected: set[str] = set()
        self.stats = ResolverStats()

    def warm_cache(self) -> None:
        """Carrega identidades já afirmadas. Chamar uma vez por crawler."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, cpf, voter_id FROM people")
            for row in cur:
                if row["cpf"]:
                    self._by_cpf[row["cpf"]] = row["id"]
                if row["voter_id"]:
                    self._by_voter[row["voter_id"]] = row["id"]
            cur.execute("SELECT id, cnpj, kind FROM companies")
            for row in cur:
                self._by_cnpj[row["cnpj"]] = row["id"]
                for k in row["kind"] or []:
                    self._kinds.add((row["id"], k))
            cur.execute("SELECT DISTINCT cpf FROM rejected_cpf")
            self._rejected = {r["cpf"] for r in cur}

    # -- pessoas ------------------------------------------------------------

    def person(
        self,
        provenance_id: int,
        name: str | None,
        cpf: str | None = None,
        voter_id: str | None = None,
    ) -> int | None:
        """Devolve o person_id, ou None quando não dá para afirmar identidade.

        Hierarquia: título eleitoral é mais confiável que CPF (o TSE mascarou
        CPF em 2024 por LGPD e reverteu em 2026, então a série histórica só
        reconcilia por título).
        """
        cpf = nz.cpf(cpf)
        voter_id = nz.digits(voter_id)
        canonical = nz.canonical_name(name)

        if cpf and cpf in self._rejected:
            cpf = None
        if not cpf and not voter_id:
            return None

        # Detecta CPF ambíguo: mesmo CPF sob títulos eleitorais diferentes.
        if cpf and voter_id:
            previous = self._cpf_voter_seen.get(cpf)
            if previous and previous != voter_id:
                self._reject_cpf(
                    cpf,
                    provenance_id,
                    "cpf_in_multiple_voter_ids",
                    {"voter_ids": [previous, voter_id]},
                )
                cpf = None
            else:
                self._cpf_voter_seen[cpf] = voter_id

        existing = (self._by_voter.get(voter_id) if voter_id else None) or (
            self._by_cpf.get(cpf) if cpf else None
        )
        if existing is not None:
            self.stats.people_matched += 1
            if voter_id and cpf and self._by_voter.get(voter_id) and not self._by_cpf.get(cpf):
                self._attach_cpf(existing, cpf)
            return existing

        if canonical is None:
            return None

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO people (cpf, voter_id, canonical_name, provenance_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (cpf, voter_id, canonical, provenance_id),
            )
            row = cur.fetchone()
            if row is None:
                # Corrida com outro identificador do mesmo registro.
                cur.execute(
                    "SELECT id FROM people WHERE cpf = %s OR voter_id = %s LIMIT 1",
                    (cpf, voter_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
            person_id = row["id"]

        if cpf:
            self._by_cpf[cpf] = person_id
        if voter_id:
            self._by_voter[voter_id] = person_id
        self.stats.people_created += 1
        return person_id

    def _attach_cpf(self, person_id: int, cpf: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE people SET cpf = %s WHERE id = %s AND cpf IS NULL",
                (cpf, person_id),
            )
        self._by_cpf[cpf] = person_id

    def _reject_cpf(self, cpf: str, provenance_id: int, reason: str, detail: dict) -> None:
        import json

        if cpf in self._rejected:
            return
        self._rejected.add(cpf)
        self.stats.cpf_rejected += 1
        self.stats.reasons[reason] = self.stats.reasons.get(reason, 0) + 1
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rejected_cpf (cpf, reason, detail, provenance_id) VALUES (%s, %s, %s, %s)",
                (cpf, reason, json.dumps(detail), provenance_id),
            )
            # Uma vez ambíguo, o CPF some de people: a afirmação anterior
            # deixa de ser sustentável.
            cur.execute("UPDATE people SET cpf = NULL WHERE cpf = %s", (cpf,))
        self._by_cpf.pop(cpf, None)

    # -- empresas -----------------------------------------------------------

    def company(
        self,
        provenance_id: int,
        cnpj: str | None,
        legal_name: str | None = None,
        kind: str | None = None,
    ) -> int | None:
        cnpj = nz.cnpj(cnpj)
        if cnpj is None:
            return None

        existing = self._by_cnpj.get(cnpj)
        if existing is not None:
            if kind and (existing, kind) not in self._kinds:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE companies SET kind = array(SELECT DISTINCT unnest(kind || %s::text[])) WHERE id = %s",
                        ([kind], existing),
                    )
                self._kinds.add((existing, kind))
            return existing

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO companies (cnpj, legal_name, kind, provenance_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (cnpj) DO UPDATE SET
                    legal_name = COALESCE(companies.legal_name, EXCLUDED.legal_name)
                RETURNING id
                """,
                (cnpj, nz.clean(legal_name), [kind] if kind else [], provenance_id),
            )
            company_id = cur.fetchone()["id"]

        self._by_cnpj[cnpj] = company_id
        if kind:
            self._kinds.add((company_id, kind))
        self.stats.companies_created += 1
        return company_id

    # -- em lote ------------------------------------------------------------
    #
    # O pipeline roda longe do banco (GitHub nos EUA, Supabase em São Paulo):
    # cada ida e volta custa ~150 ms. Resolver empresa a empresa, linha a
    # linha, transformava a cota parlamentar de um ano em horas de espera.
    # Os métodos abaixo resolvem um arquivo inteiro em poucas consultas; depois
    # deles, `company()` e `person()` só batem no cache.

    def prefetch_companies(
        self, provenance_id: int, items: Iterable[tuple[str | None, str | None]], kind: str | None
    ) -> None:
        """Cria de uma vez as empresas que faltam e marca o `kind` de todas."""
        names: dict[str, str | None] = {}
        for raw_cnpj, name in items:
            c = nz.cnpj(raw_cnpj)
            if c and c not in names:
                names[c] = nz.clean(name)
        if not names:
            return
        missing = [c for c in names if c not in self._by_cnpj]
        with self.conn.cursor() as cur:
            for chunk in chunked(missing, BATCH):
                cur.executemany(
                    """
                    INSERT INTO companies (cnpj, legal_name, kind, provenance_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (cnpj) DO NOTHING
                    """,
                    [(c, names[c], [kind] if kind else [], provenance_id) for c in chunk],
                )
                cur.execute(
                    "SELECT id, cnpj FROM companies WHERE cnpj = ANY(%s)", (list(chunk),)
                )
                for row in cur.fetchall():
                    self._by_cnpj[row["cnpj"]] = row["id"]
            self.stats.companies_created += len(missing)

            if kind:
                ids = [self._by_cnpj[c] for c in names if (self._by_cnpj[c], kind) not in self._kinds]
                for chunk in chunked(ids, BATCH):
                    cur.execute(
                        """
                        UPDATE companies
                        SET kind = array(SELECT DISTINCT unnest(kind || %s::text[]))
                        WHERE id = ANY(%s) AND NOT (kind @> %s::text[])
                        """,
                        ([kind], list(chunk), [kind]),
                    )
                self._kinds.update((i, kind) for i in ids)

    def prefetch_people_by_cpf(
        self, provenance_id: int, items: Iterable[tuple[str | None, str | None]]
    ) -> None:
        """Cria de uma vez as pessoas que só têm CPF (sanções, por exemplo).

        Mesma disciplina de `person()`: CPF inválido ou já descartado por
        ambiguidade não vira pessoa, e nome vazio também não.
        """
        names: dict[str, str] = {}
        for raw_cpf, name in items:
            c = nz.cpf(raw_cpf)
            canonical = nz.canonical_name(name)
            if c and canonical and c not in self._rejected and c not in names:
                names[c] = canonical
        missing = [c for c in names if c not in self._by_cpf]
        if not missing:
            return
        with self.conn.cursor() as cur:
            for chunk in chunked(missing, BATCH):
                cur.executemany(
                    """
                    INSERT INTO people (cpf, canonical_name, provenance_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [(c, names[c], provenance_id) for c in chunk],
                )
                cur.execute("SELECT id, cpf FROM people WHERE cpf = ANY(%s)", (list(chunk),))
                for row in cur.fetchall():
                    self._by_cpf[row["cpf"]] = row["id"]
        self.stats.people_created += len(missing)

    def report(self) -> dict:
        return {
            "people_created": self.stats.people_created,
            "people_matched": self.stats.people_matched,
            "cpf_rejected": self.stats.cpf_rejected,
            "companies_created": self.stats.companies_created,
            "rejection_reasons": self.stats.reasons,
        }
