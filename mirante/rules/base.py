"""Infra das regras de detecção.

Separação que não pode ser frouxa: as tabelas de fato são AFIRMAÇÕES DA FONTE.
As tabelas de sinal são AFIRMAÇÕES NOSSAS. Elas têm proveniência própria —
qual regra, qual versão, sobre quais linhas — e vocabulário deliberadamente
contido: severidade é low/medium/high, nunca "confirmado", nunca "fraude".

Um sinal diz "isto está fora do padrão e aqui estão as linhas". Quem conclui
é promotor, auditor ou jornalista, com apuração. Não este código.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg

SEVERITIES = ("low", "medium", "high")


@dataclass
class Evidence:
    """Ponteiro para uma linha real. Sem isso, o sinal é opinião."""

    table_name: str
    row_id: int
    provenance_id: int
    note: str | None = None


@dataclass
class Actor:
    role: str
    display_name: str
    person_id: int | None = None
    company_id: int | None = None
    politician_history_id: int | None = None


@dataclass
class Signal:
    severity: str
    headline: str
    amount_cents: int | None = None
    reference_year: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    actors: list[Actor] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severidade inválida: {self.severity}")
        if not self.evidence:
            raise ValueError(
                "sinal sem evidência é opinião, não indício — toda evidência "
                "precisa apontar para uma linha real"
            )


class RuleRun:
    """Context manager de uma execução de regra. Grava em lote."""

    def __init__(
        self,
        conn: psycopg.Connection,
        rule: str,
        rule_version: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.conn = conn
        self.rule = rule
        self.rule_version = rule_version
        self.params = params or {}
        self.id: int | None = None
        self.rows_scanned = 0
        self.signals_emitted = 0
        self._buffer: list[Signal] = []

    def __enter__(self) -> "RuleRun":
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rule_run (rule, rule_version, params, started_at)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (self.rule, self.rule_version, json.dumps(self.params), datetime.now(timezone.utc)),
            )
            self.id = cur.fetchone()["id"]
        # Rewrite-only também vale para sinais: uma regra substitui a
        # produção anterior dela mesma, nunca acumula duplicata.
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM signal WHERE rule = %s AND rule_run_id <> %s",
                (self.rule, self.id),
            )
        self.conn.commit()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._buffer:
            self.flush()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rule_run
                SET finished_at = %s, rows_scanned = %s, signals_emitted = %s, status = %s
                WHERE id = %s
                """,
                (
                    datetime.now(timezone.utc),
                    self.rows_scanned,
                    self.signals_emitted,
                    "ok" if exc_type is None else "failed",
                    self.id,
                ),
            )
        self.conn.commit()

    def emit(self, signal: Signal) -> None:
        self._buffer.append(signal)
        if len(self._buffer) >= 500:
            self.flush()

    def flush(self) -> None:
        """Grava o buffer em quatro consultas, não em três por sinal.

        Os ids são reservados de uma vez na sequência para que atores e
        evidências possam ser gravados em lote logo em seguida. Com o banco
        longe do pipeline (~150 ms por ida e volta), gravar sinal a sinal
        transformava alguns milhares de sinais em vários minutos.
        """
        if not self._buffer:
            return
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT nextval(pg_get_serial_sequence('signal', 'id')) AS id "
                "FROM generate_series(1, %s)",
                (len(self._buffer),),
            )
            ids = [r["id"] for r in cur.fetchall()]

            cur.executemany(
                """
                INSERT INTO signal
                    (id, rule_run_id, rule, severity, headline, amount_cents, reference_year, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        signal_id,
                        self.id,
                        self.rule,
                        sig.severity,
                        sig.headline,
                        sig.amount_cents,
                        sig.reference_year,
                        json.dumps(sig.detail, default=str),
                    )
                    for signal_id, sig in zip(ids, self._buffer)
                ],
            )
            actors = [
                (signal_id, a.role, a.person_id, a.company_id, a.politician_history_id, a.display_name)
                for signal_id, sig in zip(ids, self._buffer)
                for a in sig.actors
            ]
            if actors:
                cur.executemany(
                    """
                    INSERT INTO signal_actor
                        (signal_id, role, person_id, company_id, politician_history_id, display_name)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    actors,
                )
            evidence = [
                (signal_id, e.table_name, e.row_id, e.provenance_id, e.note)
                for signal_id, sig in zip(ids, self._buffer)
                for e in sig.evidence
            ]
            if evidence:
                cur.executemany(
                    """
                    INSERT INTO signal_evidence (signal_id, table_name, row_id, provenance_id, note)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    evidence,
                )
        self.conn.commit()
        self.signals_emitted += len(self._buffer)
        self._buffer = []


def brl(cents: int | None) -> str:
    """Formata centavos como moeda, só para a headline legível."""
    if cents is None:
        return "R$ 0,00"
    return f"R$ {cents / 100:,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")
