"""Crawler: prestação de contas eleitorais (CNPJ de campanha, receitas, despesas).

É a etapa mais longa e a mais valiosa: é daqui que sai o grafo do dinheiro —
quem doou para quem, e para quem a campanha pagou. As regras de detecção
rodam quase todas sobre estas tabelas.

Distinção que importa: `campaign_expense` é despesa CONTRATADA (competência) e
`campaign_expense_payment` é pagamento (caixa). Confundir as duas infla os
valores, porque uma despesa parcelada aparece nas duas tabelas.
"""

from __future__ import annotations

from ..fetch import iter_zip_text
from ..provenance import Collector
from .. import normalize as nz
from .base import EntityResolver, read_csv

VERSION = "1.0"
URL = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/"
    "prestacao_de_contas_eleitorais_candidatos_{year}.zip"
)
SUPPORTED_YEARS = [2014, 2016, 2018, 2020, 2022, 2024, 2026]

OWNED_TABLES = (
    "campaign_expense_payment",
    "campaign_expense",
    "campaign_donation",
    "campaign_org",
)


def run(conn, years: list[int] | None = None, uf: str | None = None) -> dict:
    years = sorted(years or SUPPORTED_YEARS)
    resolver = EntityResolver(conn)
    resolver.warm_cache()
    report: dict = {"years": {}}

    with Collector(
        conn, "tse", "tse-accounts", VERSION, args={"years": years, "uf": uf}
    ) as collector:
        collector.truncate(*OWNED_TABLES)

        for year in years:
            print(f"\n[{year}] prestação de contas")
            fetched = collector.fetch(
                URL.format(year=year), filename=f"prestacao_contas_{year}.zip"
            )
            counts = {"orgs": 0, "donations": 0, "expenses": 0, "payments": 0}

            # Ordem importa: o CNPJ de campanha precisa existir antes das
            # doações e despesas que o referenciam.
            for stage, pattern, loader in (
                ("orgs", "receitas_candidatos_", _load_orgs),
                ("donations", "receitas_candidatos_", _load_donations),
                ("expenses", "despesas_contratadas_candidatos_", _load_expenses),
                ("payments", "despesas_pagas_candidatos_", _load_payments),
            ):
                for inner_name, stream in iter_zip_text(fetched.path, pattern=pattern):
                    if uf and f"_{uf.upper()}." not in inner_name.upper():
                        continue
                    parser = f"tse.{stage}.v1"
                    with collector.parse(fetched, inner_name, parser, year) as parse:
                        loader(conn, resolver, stream, parse, year)
                        counts[stage] += parse.rows_written

            report["years"][year] = counts

    report["identity"] = resolver.report()
    return report


# ---------------------------------------------------------------------------
# etapa 1 — CNPJ de campanha
# ---------------------------------------------------------------------------

def _campaign_org_index(conn, year: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.cnpj, o.id
            FROM campaign_org o JOIN companies c ON c.id = o.company_id
            WHERE o.election_year = %s
            """,
            (year,),
        )
        return {r["cnpj"]: r["id"] for r in cur}


def _history_index(conn, year: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sequential_id, id FROM politician_history WHERE election_year = %s",
            (year,),
        )
        return {r["sequential_id"]: r["id"] for r in cur}


def _load_orgs(conn, resolver: EntityResolver, stream, parse, year: int) -> None:
    """Uma linha por CNPJ de campanha distinto encontrado no arquivo de receitas."""
    history = _history_index(conn, year)
    seen: set[str] = set()

    for raw in read_csv(stream):
        parse.rows_read += 1
        cnpj = nz.cnpj(raw.get("NR_CNPJ_PRESTADOR_CONTA"))
        if cnpj is None or cnpj in seen:
            continue
        seen.add(cnpj)

        company_id = resolver.company(
            parse.id, cnpj, nz.clean(raw.get("NM_CANDIDATO")), kind="campaign"
        )
        if company_id is None:
            parse.rows_rejected += 1
            continue

        sequential_id = nz.clean(raw.get("SQ_CANDIDATO"))
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaign_org
                    (politician_history_id, company_id, election_year, provenance_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (company_id, election_year) DO NOTHING
                """,
                (history.get(sequential_id), company_id, year, parse.id),
            )
        parse.rows_written += 1
    conn.commit()


# ---------------------------------------------------------------------------
# etapa 2 — doações
# ---------------------------------------------------------------------------

DONATION_SQL = """
INSERT INTO campaign_donation (
    campaign_org_id, election_year, donor_cpf_cnpj, donor_name,
    donor_person_id, donor_company_id, amount_cents, donated_on,
    resource_origin, resource_kind, receipt_number, provenance_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _load_donations(conn, resolver: EntityResolver, stream, parse, year: int) -> None:
    orgs = _campaign_org_index(conn, year)
    rows: list[tuple] = []

    for raw in read_csv(stream):
        parse.rows_read += 1
        org_id = orgs.get(nz.cnpj(raw.get("NR_CNPJ_PRESTADOR_CONTA")) or "")
        amount = nz.money_cents(raw.get("VR_RECEITA"))
        if org_id is None or amount is None:
            parse.rows_rejected += 1
            continue

        donor_cpf, donor_cnpj = nz.cpf_or_cnpj(raw.get("NR_CPF_CNPJ_DOADOR"))
        donor_name = nz.clean(raw.get("NM_DOADOR")) or nz.clean(raw.get("NM_DOADOR_RFB"))

        rows.append(
            (
                org_id,
                year,
                donor_cpf or donor_cnpj,
                donor_name,
                resolver.person(parse.id, donor_name, cpf=donor_cpf) if donor_cpf else None,
                resolver.company(parse.id, donor_cnpj, donor_name, kind="donor") if donor_cnpj else None,
                amount,
                nz.parse_date(raw.get("DT_RECEITA")),
                nz.clean(raw.get("DS_ORIGEM_RECEITA")),
                nz.clean(raw.get("DS_RECEITA")),
                nz.clean(raw.get("NR_RECIBO_DOACAO")),
                parse.id,
            )
        )
        if len(rows) >= 5_000:
            parse.rows_written += _flush(conn, DONATION_SQL, rows)
            rows = []

    if rows:
        parse.rows_written += _flush(conn, DONATION_SQL, rows)


# ---------------------------------------------------------------------------
# etapa 3 — despesas contratadas
# ---------------------------------------------------------------------------

EXPENSE_SQL = """
INSERT INTO campaign_expense (
    campaign_org_id, election_year, supplier_cpf_cnpj, supplier_name,
    supplier_person_id, supplier_company_id, amount_cents, contracted_on,
    description, expense_kind, document_number, provenance_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _load_expenses(conn, resolver: EntityResolver, stream, parse, year: int) -> None:
    orgs = _campaign_org_index(conn, year)
    rows: list[tuple] = []

    for raw in read_csv(stream):
        parse.rows_read += 1
        org_id = orgs.get(nz.cnpj(raw.get("NR_CNPJ_PRESTADOR_CONTA")) or "")
        amount = nz.money_cents(
            raw.get("VR_DESPESA_CONTRATADA") or raw.get("VR_DESPESA")
        )
        if org_id is None or amount is None:
            parse.rows_rejected += 1
            continue

        sup_cpf, sup_cnpj = nz.cpf_or_cnpj(raw.get("NR_CPF_CNPJ_FORNECEDOR"))
        sup_name = nz.clean(raw.get("NM_FORNECEDOR")) or nz.clean(raw.get("NM_FORNECEDOR_RFB"))

        rows.append(
            (
                org_id,
                year,
                sup_cpf or sup_cnpj,
                sup_name,
                resolver.person(parse.id, sup_name, cpf=sup_cpf) if sup_cpf else None,
                resolver.company(parse.id, sup_cnpj, sup_name, kind="supplier") if sup_cnpj else None,
                amount,
                nz.parse_date(raw.get("DT_DESPESA")),
                nz.clean(raw.get("DS_DESPESA")),
                nz.clean(raw.get("DS_TIPO_DESPESA")),
                nz.clean(raw.get("NR_DOCUMENTO")),
                parse.id,
            )
        )
        if len(rows) >= 5_000:
            parse.rows_written += _flush(conn, EXPENSE_SQL, rows)
            rows = []

    if rows:
        parse.rows_written += _flush(conn, EXPENSE_SQL, rows)


# ---------------------------------------------------------------------------
# etapa 4 — pagamentos (regime de caixa)
# ---------------------------------------------------------------------------

PAYMENT_SQL = """
INSERT INTO campaign_expense_payment (
    campaign_expense_id, campaign_org_id, amount_cents, paid_on,
    payment_method, provenance_id
) VALUES (%s, %s, %s, %s, %s, %s)
"""


def _load_payments(conn, resolver: EntityResolver, stream, parse, year: int) -> None:
    orgs = _campaign_org_index(conn, year)
    rows: list[tuple] = []

    for raw in read_csv(stream):
        parse.rows_read += 1
        org_id = orgs.get(nz.cnpj(raw.get("NR_CNPJ_PRESTADOR_CONTA")) or "")
        amount = nz.money_cents(raw.get("VR_PAGAMENTO") or raw.get("VR_DESPESA_PAGA"))
        if org_id is None or amount is None:
            parse.rows_rejected += 1
            continue

        # O arquivo de pagos nem sempre traz a chave da despesa contratada.
        # Quando não traz, o vínculo fica NULL — e isso é honesto: não
        # inventamos a ligação por aproximação de valor e data.
        rows.append((None, org_id, amount, nz.parse_date(raw.get("DT_PAGAMENTO")),
                     nz.clean(raw.get("DS_TIPO_DOCUMENTO")), parse.id))

        if len(rows) >= 5_000:
            parse.rows_written += _flush(conn, PAYMENT_SQL, rows)
            rows = []

    if rows:
        parse.rows_written += _flush(conn, PAYMENT_SQL, rows)


def _flush(conn, sql: str, rows: list[tuple]) -> int:
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)
