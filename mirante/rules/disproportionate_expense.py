"""Regra: despesa desproporcional.

Item tipicamente barato (caneta, adesivo, crachá) contratado por valor muito
acima do normal PARA AQUELA CATEGORIA. A comparação é contra a mediana
histórica da própria categoria, não contra um valor absoluto: R$ 50 mil em
palanque é rotina, R$ 50 mil em caneta não é.

Limitação que precisa estar escrita, não escondida: o TSE não publica
QUANTIDADE neste arquivo, só o valor total da despesa. A regra não calcula
preço unitário. Um valor alto pode ser lote grande, item não detalhado na
descrição, ou erro de digitação. Por isso é indício, não prova.
"""

from __future__ import annotations

from .base import Actor, Evidence, RuleRun, Signal, brl

VERSION = "1.0"
RULE = "disproportionate_expense"

# Categorias de item tipicamente barato. A chave vira o rótulo do sinal;
# os termos são casados contra description com ILIKE.
CHEAP_CATEGORIES: dict[str, list[str]] = {
    "caneta": ["caneta", "esferografica"],
    "adesivo": ["adesivo", "adesivagem"],
    "cracha": ["cracha", "crachá"],
    "panfleto": ["panfleto", "santinho", "volante"],
    "camiseta": ["camiseta", "camisa"],
    "bone": ["bone", "boné"],
    "chaveiro": ["chaveiro"],
    "cafe": ["cafe", "café", "lanche", "coffee"],
    "agua": ["agua mineral", "água mineral", "galao de agua"],
    "copo": ["copo descartavel", "copo plastico"],
    "bandeira": ["bandeira", "bandeirinha"],
    "faixa": ["faixa", "banner"],
    "leque": ["leque"],
    "sacola": ["sacola", "ecobag"],
    "impressao": ["impressao", "impressão", "xerox", "copia"],
    "combustivel": ["combustivel", "gasolina", "etanol", "diesel"],
    "correio": ["correio", "postagem", "selo"],
    "papelaria": ["papelaria", "material de escritorio"],
}

# Múltiplos da mediana da categoria.
MEDIUM_FACTOR = 15
HIGH_FACTOR = 30
# Piso absoluto: abaixo disso não vira sinal mesmo sendo 50x a mediana.
# Sem o piso, uma categoria de mediana R$ 2,00 gera ruído infinito.
FLOOR_CENTS = 100_000  # R$ 1.000,00
# Categorias com poucas observações não têm mediana confiável; usam piso fixo.
MIN_SAMPLE = 20
SPARSE_MEDIUM_CENTS = 500_000     # R$ 5.000,00
SPARSE_HIGH_CENTS = 5_000_000     # R$ 50.000,00


def _category_clause(terms: list[str]) -> tuple[str, list[str]]:
    clause = " OR ".join(["unaccent(lower(e.description)) LIKE %s"] * len(terms))
    params = [f"%{t.lower()}%" for t in terms]
    return clause, params


MEDIAN_SQL = """
SELECT
    percentile_cont(0.5) WITHIN GROUP (ORDER BY e.amount_cents) AS median_cents,
    count(*) AS sample_size
FROM campaign_expense e
WHERE ({clause}) AND e.amount_cents > 0
"""

OUTLIER_SQL = """
SELECT
    e.id, e.amount_cents, e.description, e.election_year, e.provenance_id,
    e.supplier_name, e.supplier_company_id, e.supplier_person_id,
    h.id AS politician_history_id, h.full_name, h.office, h.uf, h.party_acronym
FROM campaign_expense e
JOIN campaign_org o ON o.id = e.campaign_org_id
LEFT JOIN politician_history h ON h.id = o.politician_history_id
WHERE ({clause}) AND e.amount_cents >= %s
ORDER BY e.amount_cents DESC
"""


def run(conn, min_factor: int = MEDIUM_FACTOR) -> dict:
    summary: dict = {"categories": {}}

    with RuleRun(
        conn,
        RULE,
        VERSION,
        params={
            "medium_factor": MEDIUM_FACTOR,
            "high_factor": HIGH_FACTOR,
            "floor_cents": FLOOR_CENTS,
            "categories": list(CHEAP_CATEGORIES),
        },
    ) as run_ctx:

        for category, terms in CHEAP_CATEGORIES.items():
            clause, params = _category_clause(terms)

            with conn.cursor() as cur:
                cur.execute(MEDIAN_SQL.format(clause=clause), params)  # type: ignore[arg-type]
                stats = cur.fetchone()

            sample = stats["sample_size"] or 0
            median = int(stats["median_cents"] or 0)
            if sample == 0:
                continue

            if sample < MIN_SAMPLE or median == 0:
                # Amostra pequena: mediana não é confiável, usa piso fixo.
                medium_threshold = SPARSE_MEDIUM_CENTS
                high_threshold = SPARSE_HIGH_CENTS
                basis = "piso_fixo_amostra_pequena"
            else:
                medium_threshold = max(median * MEDIUM_FACTOR, FLOOR_CENTS)
                high_threshold = max(median * HIGH_FACTOR, FLOOR_CENTS)
                basis = "mediana_da_categoria"

            with conn.cursor() as cur:
                cur.execute(
                    OUTLIER_SQL.format(clause=clause),
                    params + [medium_threshold],  # type: ignore[arg-type]
                )
                outliers = cur.fetchall()

            run_ctx.rows_scanned += sample
            emitted = 0

            for row in outliers:
                amount = row["amount_cents"]
                severity = "high" if amount >= high_threshold else "medium"
                ratio = round(amount / median, 1) if median else None

                actors = []
                if row["full_name"]:
                    actors.append(
                        Actor(
                            role="candidate",
                            display_name=row["full_name"],
                            politician_history_id=row["politician_history_id"],
                        )
                    )
                if row["supplier_name"]:
                    actors.append(
                        Actor(
                            role="supplier",
                            display_name=row["supplier_name"],
                            company_id=row["supplier_company_id"],
                            person_id=row["supplier_person_id"],
                        )
                    )

                headline = (
                    f"Despesa de {brl(amount)} na categoria '{category}'"
                    + (f", {ratio}x a mediana da categoria" if ratio else "")
                )

                run_ctx.emit(
                    Signal(
                        severity=severity,
                        headline=headline,
                        amount_cents=amount,
                        reference_year=row["election_year"],
                        detail={
                            "category": category,
                            "median_cents": median,
                            "sample_size": sample,
                            "ratio_to_median": ratio,
                            "threshold_basis": basis,
                            "description": row["description"],
                            "office": row["office"],
                            "uf": row["uf"],
                            "party": row["party_acronym"],
                            "caveat": (
                                "O TSE não publica quantidade neste arquivo. "
                                "Pode ser lote grande, item não detalhado na "
                                "descrição ou erro de digitação."
                            ),
                        },
                        actors=actors,
                        evidence=[
                            Evidence(
                                table_name="campaign_expense",
                                row_id=row["id"],
                                provenance_id=row["provenance_id"],
                                note="despesa contratada sinalizada",
                            )
                        ],
                    )
                )
                emitted += 1

            summary["categories"][category] = {
                "sample_size": sample,
                "median_cents": median,
                "threshold_basis": basis,
                "signals": emitted,
            }
            if emitted:
                print(f"  {category:14s} mediana {brl(median):>16s}  -> {emitted:,} sinais")

        summary["signals_total"] = run_ctx.signals_emitted + len(run_ctx._buffer)

    return summary
