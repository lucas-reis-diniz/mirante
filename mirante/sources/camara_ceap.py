"""Crawler: cota parlamentar (CEAP) e deputados em exercício.

Base pequena comparada ao TSE (centenas de milhares de linhas por ano, não
milhões) e com nota fiscal escaneada por trás de quase toda linha. É o melhor
ponto de partida para quem quer ver o pipeline rodar inteiro em minutos.

Diferença conceitual importante: CEAP é dinheiro do mandato, não da campanha.
Um fornecedor que aparece nas duas pontas — pago pela campanha e depois pela
cota — é exatamente o tipo de cruzamento que justifica o projeto existir.
"""

from __future__ import annotations

from ..fetch import iter_zip_csv, sniff_text
from ..provenance import Collector
from .. import normalize as nz
from .base import EntityResolver, read_csv

VERSION = "1.0"

# A Câmara publica o mesmo conteúdo em dois endereços. O segundo é o fallback
# quando o primeiro muda de layout (já mudou duas vezes desde 2019).
CEAP_URLS = [
    "https://www.camara.leg.br/cotas/Ano-{year}.csv.zip",
    "https://dadosabertos.camara.leg.br/arquivos/despesasDeputados/csv/despesasDeputados-{year}.csv",
]
DEPUTIES_URL = "https://dadosabertos.camara.leg.br/arquivos/deputados/csv/deputados.csv"

OWNED_TABLES = ("parliamentary_expense",)


def run(conn, years: list[int] | None = None) -> dict:
    from datetime import date

    years = sorted(years or [date.today().year])
    resolver = EntityResolver(conn)
    resolver.warm_cache()
    report: dict = {"years": {}}

    with Collector(conn, "camara", "camara-ceap", VERSION, args={"years": years}) as collector:
        collector.truncate(*OWNED_TABLES)

        for year in years:
            print(f"\n[{year}] cota parlamentar (CEAP)")
            fetched = None
            last_error: Exception | None = None
            for template in CEAP_URLS:
                try:
                    fetched = collector.fetch(
                        template.format(year=year),
                        filename=f"ceap_{year}{'.zip' if template.endswith('.zip') else '.csv'}",
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - fallback entre endereços
                    last_error = exc
                    print(f"  endereço indisponível, tentando o próximo: {exc}")
            if fetched is None:
                raise RuntimeError(f"CEAP {year} indisponível nos dois endereços") from last_error

            written = 0
            if fetched.path.suffix == ".zip":
                for inner_name, stream, delimiter in iter_zip_csv(fetched.path, pattern=".csv"):
                    with collector.parse(fetched, inner_name, "camara.ceap.v1", year) as parse:
                        _load(conn, resolver, stream, parse, year, delimiter)
                        written += parse.rows_written
            else:
                with fetched.path.open("rb") as fh:
                    encoding, delimiter = sniff_text(fh.read(64 * 1024))
                with fetched.path.open("r", encoding=encoding, newline="") as stream:
                    with collector.parse(
                        fetched, fetched.path.name, "camara.ceap.v1", year
                    ) as parse:
                        _load(conn, resolver, stream, parse, year, delimiter)
                        written += parse.rows_written

            report["years"][year] = written

    report["identity"] = resolver.report()
    return report


INSERT_SQL = """
INSERT INTO parliamentary_expense (
    house, external_id, full_name, party_acronym, uf, reference_year,
    reference_month, category, supplier_name, supplier_cpf_cnpj,
    supplier_company_id, document_number, issued_on, amount_cents,
    reimbursed_cents, document_url, provenance_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _load(conn, resolver: EntityResolver, stream, parse, year: int, delimiter: str = ";") -> None:
    rows: list[tuple] = []

    # Duas passadas: primeiro resolve todos os fornecedores do arquivo em lote,
    # depois grava as despesas. Ver EntityResolver.prefetch_companies.
    raws = list(read_csv(stream, delimiter=delimiter))
    resolver.prefetch_companies(
        parse.id,
        (
            (raw.get("TXTCNPJCPF") or raw.get("CNPJCPF"),
             raw.get("TXTFORNECEDOR") or raw.get("FORNECEDOR"))
            for raw in raws
        ),
        kind="contractor",
    )
    conn.commit()

    for raw in raws:
        parse.rows_read += 1

        name = nz.clean(raw.get("TXNOMEPARLAMENTAR") or raw.get("NOME"))
        amount = nz.money_cents(raw.get("VLRDOCUMENTO") or raw.get("VALORDOCUMENTO"))
        month = nz.int_or_none(raw.get("NUMMES") or raw.get("MES"))
        if not name or amount is None or month is None:
            parse.rows_rejected += 1
            continue

        sup_cpf, sup_cnpj = nz.cpf_or_cnpj(raw.get("TXTCNPJCPF") or raw.get("CNPJCPF"))
        sup_name = nz.clean(raw.get("TXTFORNECEDOR") or raw.get("FORNECEDOR"))

        rows.append(
            (
                "camara",
                nz.clean(raw.get("IDECADASTRO") or raw.get("CODIGOPARLAMENTAR")) or name,
                name,
                nz.clean(raw.get("SGPARTIDO") or raw.get("PARTIDO")),
                nz.clean(raw.get("SGUF") or raw.get("UF")),
                nz.int_or_none(raw.get("NUMANO") or raw.get("ANO")) or year,
                month,
                nz.clean(raw.get("TXTDESCRICAO") or raw.get("DESCRICAO")),
                sup_name,
                sup_cpf or sup_cnpj,
                resolver.company(parse.id, sup_cnpj, sup_name, kind="contractor") if sup_cnpj else None,
                nz.clean(raw.get("TXTNUMERO") or raw.get("NUMERODOCUMENTO")),
                nz.parse_date(raw.get("DATEMISSAO") or raw.get("DATAEMISSAO")),
                amount,
                nz.money_cents(raw.get("VLRLIQUIDO") or raw.get("VALORLIQUIDO")),
                nz.clean(raw.get("URLDOCUMENTO")),
                parse.id,
            )
        )

        if len(rows) >= 5_000:
            parse.rows_written += _flush(conn, rows)
            rows = []

    if rows:
        parse.rows_written += _flush(conn, rows)

    # Arquivo lido e nada aceito é mudança de formato da fonte, não um ano sem
    # despesa. Falhar alto é melhor que gravar "ok" com zero linhas.
    if parse.rows_read and not parse.rows_written:
        header = list(raws[0].keys())[:8] if raws else []
        raise RuntimeError(
            f"CEAP {year}: {parse.rows_read:,} linhas lidas e nenhuma aceita. "
            f"Cabeçalho lido: {header}"
        )


def _flush(conn, rows: list[tuple]) -> int:
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, rows)
    conn.commit()
    return len(rows)
