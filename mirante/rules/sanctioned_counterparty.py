"""Regra: contraparte sancionada.

Empresa inscrita no CEIS ou CNEP que também aparece como doadora de campanha,
fornecedora de campanha ou fornecedora de cota parlamentar.

Esta é a regra que justifica coletar fontes diferentes. Cada base sozinha é
inócua: o CEIS é uma lista de nomes, a prestação de contas é uma pilha de
notas. Cruzadas por CNPJ, viram pergunta concreta — por que dinheiro público
ou eleitoral foi para quem o próprio governo declarou impedido.

Nuance temporal que a regra registra e não esconde: a sanção pode ser
POSTERIOR ao pagamento. Pagar em 2022 para empresa sancionada em 2025 não é
irregularidade nenhuma. O campo `sanction_after_payment` separa os dois casos,
e só o pagamento durante sanção vigente chega a severidade alta.
"""

from __future__ import annotations

from .base import Actor, Evidence, RuleRun, Signal, brl

VERSION = "1.0"
RULE = "sanctioned_counterparty"

MIN_AMOUNT_CENTS = 100_000  # R$ 1.000,00

QUERY = """
WITH counterparty AS (
    SELECT 'campaign_donation' AS table_name, d.id AS row_id, d.provenance_id,
           d.donor_company_id AS company_id, d.amount_cents, d.donated_on AS occurred_on,
           d.election_year AS reference_year, 'doação a campanha' AS flow,
           h.full_name AS politician_name, h.id AS politician_history_id
    FROM campaign_donation d
    JOIN campaign_org o ON o.id = d.campaign_org_id
    LEFT JOIN politician_history h ON h.id = o.politician_history_id
    WHERE d.donor_company_id IS NOT NULL AND d.amount_cents >= %(min_amount)s

    UNION ALL

    SELECT 'campaign_expense', e.id, e.provenance_id,
           e.supplier_company_id, e.amount_cents, e.contracted_on,
           e.election_year, 'pagamento de campanha',
           h.full_name, h.id
    FROM campaign_expense e
    JOIN campaign_org o ON o.id = e.campaign_org_id
    LEFT JOIN politician_history h ON h.id = o.politician_history_id
    WHERE e.supplier_company_id IS NOT NULL AND e.amount_cents >= %(min_amount)s

    UNION ALL

    SELECT 'parliamentary_expense', p.id, p.provenance_id,
           p.supplier_company_id, p.amount_cents, p.issued_on,
           p.reference_year, 'cota parlamentar',
           p.full_name, NULL
    FROM parliamentary_expense p
    WHERE p.supplier_company_id IS NOT NULL AND p.amount_cents >= %(min_amount)s
)
SELECT c.*, s.id AS sanction_id, s.registry, s.sanctioned_name, s.sanction_kind,
       s.started_on AS sanction_started_on, s.ended_on AS sanction_ended_on,
       s.sanctioning_body, s.provenance_id AS sanction_provenance_id,
       co.cnpj, co.legal_name
FROM counterparty c
JOIN sanction s  ON s.company_id = c.company_id
JOIN companies co ON co.id = c.company_id
ORDER BY c.amount_cents DESC
"""


def run(conn, min_amount: int = MIN_AMOUNT_CENTS) -> dict:
    summary = {"matches": 0, "during_sanction": 0, "before_sanction": 0}

    with RuleRun(conn, RULE, VERSION, params={"min_amount_cents": min_amount}) as run_ctx:
        with conn.cursor() as cur:
            cur.execute(QUERY, {"min_amount": min_amount})
            rows = cur.fetchall()

        run_ctx.rows_scanned = len(rows)

        for row in rows:
            summary["matches"] += 1

            occurred = row["occurred_on"]
            started = row["sanction_started_on"]
            ended = row["sanction_ended_on"]

            during = bool(
                occurred and started and occurred >= started and (ended is None or occurred <= ended)
            )
            after = bool(occurred and started and occurred < started)

            if during:
                severity = "high"
                summary["during_sanction"] += 1
            elif after:
                severity = "low"
                summary["before_sanction"] += 1
            else:
                # Sem data em um dos lados não dá para ordenar os fatos.
                severity = "medium"

            headline = (
                f"{row['flow'].capitalize()} de {brl(row['amount_cents'])} envolvendo "
                f"empresa inscrita no {row['registry']}"
                + (" durante a vigência da sanção" if during else "")
            )

            actors = [
                Actor(
                    role="sanctioned_company",
                    display_name=row["legal_name"] or row["sanctioned_name"],
                    company_id=row["company_id"],
                )
            ]
            if row["politician_name"]:
                actors.append(
                    Actor(
                        role="candidate",
                        display_name=row["politician_name"],
                        politician_history_id=row["politician_history_id"],
                    )
                )

            run_ctx.emit(
                Signal(
                    severity=severity,
                    headline=headline,
                    amount_cents=row["amount_cents"],
                    reference_year=row["reference_year"],
                    detail={
                        "flow": row["flow"],
                        "cnpj": row["cnpj"],
                        "registry": row["registry"],
                        "sanction_kind": row["sanction_kind"],
                        "sanctioning_body": row["sanctioning_body"],
                        "occurred_on": occurred,
                        "sanction_started_on": started,
                        "sanction_ended_on": ended,
                        "paid_during_sanction": during,
                        "sanction_after_payment": after,
                        "caveat": (
                            "Sanção posterior ao pagamento não indica "
                            "irregularidade no pagamento."
                        ),
                    },
                    actors=actors,
                    evidence=[
                        Evidence(
                            table_name=row["table_name"],
                            row_id=row["row_id"],
                            provenance_id=row["provenance_id"],
                            note="movimentação financeira",
                        ),
                        Evidence(
                            table_name="sanction",
                            row_id=row["sanction_id"],
                            provenance_id=row["sanction_provenance_id"],
                            note=f"inscrição no {row['registry']}",
                        ),
                    ],
                )
            )

    print(
        f"  {summary['matches']:,} cruzamentos; "
        f"{summary['during_sanction']:,} durante sanção vigente"
    )
    return summary
