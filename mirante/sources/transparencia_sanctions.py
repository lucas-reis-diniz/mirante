"""Crawler: CEIS e CNEP (Portal da Transparência / CGU).

CEIS = empresas e pessoas impedidas de contratar com o poder público.
CNEP = punidas pela Lei Anticorrupção.

Sozinhos valem pouco. O valor aparece no cruzamento: uma empresa sancionada
que também é doadora de campanha ou fornecedora de cota parlamentar. Esse
cruzamento é um JOIN depois que as duas pontas estão no banco.

O portal publica snapshot diário em /download-de-dados/<registro>/AAAAMMDD.
Snapshot é estado, não histórico: rodar hoje substitui o de ontem.
"""

from __future__ import annotations

from datetime import date, timedelta

from ..fetch import iter_zip_text
from ..provenance import Collector
from .. import normalize as nz
from .base import EntityResolver, read_csv

VERSION = "1.0"
URL = "https://portaldatransparencia.gov.br/download-de-dados/{registry}/{stamp}"
REGISTRIES = ("ceis", "cnep")

OWNED_TABLES = ("sanction",)


def run(conn, snapshot: date | None = None, lookback_days: int = 7) -> dict:
    """Coleta o snapshot mais recente disponível.

    O portal às vezes atrasa a publicação do dia. Em vez de falhar, andamos
    para trás até achar um snapshot que exista — e registramos no manifesto
    qual data foi de fato usada, que é o que importa para reprodutibilidade.
    """
    resolver = EntityResolver(conn)
    resolver.warm_cache()
    start = snapshot or date.today()
    report: dict = {"registries": {}}

    with Collector(
        conn,
        "portal_transparencia",
        "transparencia-sanctions",
        VERSION,
        args={"snapshot": start.isoformat()},
    ) as collector:
        collector.truncate(*OWNED_TABLES)

        for registry in REGISTRIES:
            fetched, used = _fetch_latest(collector, registry, start, lookback_days)
            print(f"\n[{registry.upper()}] snapshot de {used.isoformat()}")

            written = 0
            for inner_name, stream in iter_zip_text(fetched.path, pattern=".csv"):
                parser = f"transparencia.{registry}.v1"
                with collector.parse(fetched, inner_name, parser, used.year) as parse:
                    _load(conn, resolver, stream, parse, registry.upper())
                    written += parse.rows_written

            report["registries"][registry] = {"rows": written, "snapshot": used.isoformat()}

    report["identity"] = resolver.report()
    return report


def _fetch_latest(collector, registry: str, start: date, lookback_days: int):
    last_error: Exception | None = None
    for offset in range(lookback_days + 1):
        day = start - timedelta(days=offset)
        stamp = day.strftime("%Y%m%d")
        try:
            fetched = collector.fetch(
                URL.format(registry=registry, stamp=stamp),
                filename=f"{registry}_{stamp}.zip",
            )
            return fetched, day
        except Exception as exc:  # noqa: BLE001 - snapshot do dia pode não existir
            last_error = exc
    raise RuntimeError(
        f"nenhum snapshot de {registry} nos últimos {lookback_days} dias"
    ) from last_error


INSERT_SQL = """
INSERT INTO sanction (
    registry, cpf_cnpj, person_id, company_id, sanctioned_name, sanction_kind,
    legal_basis, started_on, ended_on, sanctioning_body, process_number, provenance_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _name(raw: dict) -> str | None:
    return nz.clean(
        raw.get("NOME INFORMADO PELO ÓRGÃO SANCIONADOR")
        or raw.get("RAZÃO SOCIAL - CADASTRO RECEITA")
        or raw.get("NOME FANTASIA - CADASTRO RECEITA")
    )


def _load(conn, resolver: EntityResolver, stream, parse, registry: str) -> None:
    rows: list[tuple] = []

    # Duas passadas: pessoas e empresas do arquivo inteiro resolvidas em lote,
    # depois as sanções. Ver EntityResolver.prefetch_companies.
    raws = list(read_csv(stream))
    docs = [(nz.cpf_or_cnpj(raw.get("CPF OU CNPJ DO SANCIONADO")), _name(raw)) for raw in raws]
    resolver.prefetch_people_by_cpf(parse.id, ((cpf, name) for (cpf, _), name in docs if cpf))
    resolver.prefetch_companies(
        parse.id, ((cnpj, name) for (_, cnpj), name in docs if cnpj), kind="sanctioned"
    )
    conn.commit()

    for raw in raws:
        parse.rows_read += 1

        name = nz.clean(
            raw.get("NOME INFORMADO PELO ÓRGÃO SANCIONADOR")
            or raw.get("RAZÃO SOCIAL - CADASTRO RECEITA")
            or raw.get("NOME FANTASIA - CADASTRO RECEITA")
        )
        if not name:
            parse.rows_rejected += 1
            continue

        doc_cpf, doc_cnpj = nz.cpf_or_cnpj(raw.get("CPF OU CNPJ DO SANCIONADO"))

        rows.append(
            (
                registry,
                doc_cpf or doc_cnpj,
                resolver.person(parse.id, name, cpf=doc_cpf) if doc_cpf else None,
                resolver.company(parse.id, doc_cnpj, name, kind="sanctioned") if doc_cnpj else None,
                name,
                nz.clean(raw.get("TIPO SANÇÃO") or raw.get("CATEGORIA DA SANÇÃO")),
                nz.clean(raw.get("FUNDAMENTAÇÃO LEGAL")),
                nz.parse_date(raw.get("DATA INÍCIO SANÇÃO")),
                nz.parse_date(raw.get("DATA FINAL SANÇÃO")),
                nz.clean(raw.get("ÓRGÃO SANCIONADOR")),
                nz.clean(raw.get("NÚMERO DO PROCESSO")),
                parse.id,
            )
        )

        if len(rows) >= 2_000:
            parse.rows_written += _flush(conn, rows)
            rows = []

    if rows:
        parse.rows_written += _flush(conn, rows)


def _flush(conn, rows: list[tuple]) -> int:
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, rows)
    conn.commit()
    return len(rows)
