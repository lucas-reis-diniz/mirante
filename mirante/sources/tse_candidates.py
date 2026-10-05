"""Crawler: candidaturas do TSE (consulta_cand).

É a espinha dorsal. Tudo o mais se pendura em politician_history.

Rewrite-only: apaga politician_history e reconstrói a partir do arquivo
oficial. Os anos presentes na tabela passam a ser exatamente os anos pedidos.
"""

from __future__ import annotations

from ..provenance import Collector
from .. import normalize as nz
from .base import EntityResolver, read_csv

VERSION = "1.0"
PARSER = "tse.consulta_cand.v1"
URL = "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_{year}.zip"

# Eleições gerais e municipais alternam. Cargos e UE mudam de significado,
# mas o layout do arquivo é o mesmo desde 2014.
SUPPORTED_YEARS = [2014, 2016, 2018, 2020, 2022, 2024, 2026]

OWNED_TABLES = ("politician_history",)


def run(conn, years: list[int] | None = None, uf: str | None = None) -> dict:
    years = sorted(years or SUPPORTED_YEARS)
    resolver = EntityResolver(conn)
    resolver.warm_cache()
    report: dict = {"years": {}, "uf_filter": uf}

    with Collector(
        conn, "tse", "tse-candidates", VERSION, args={"years": years, "uf": uf}
    ) as collector:
        collector.truncate(*OWNED_TABLES)

        for year in years:
            url = URL.format(year=year)
            print(f"\n[{year}] consulta_cand")
            fetched = collector.fetch(url, filename=f"consulta_cand_{year}.zip")
            year_rows = 0

            from ..fetch import iter_zip_text

            for inner_name, stream in iter_zip_text(fetched.path, pattern=".csv"):
                if uf and f"_{uf.upper()}." not in inner_name.upper():
                    continue
                if "BRASIL" in inner_name.upper() and uf:
                    continue

                with collector.parse(fetched, inner_name, PARSER, year) as parse:
                    _load_file(conn, resolver, stream, parse, year)
                    year_rows += parse.rows_written

            report["years"][year] = year_rows

    report["identity"] = resolver.report()
    return report


def _load_file(conn, resolver: EntityResolver, stream, parse, year: int) -> None:
    rows: list[tuple] = []

    for raw in read_csv(stream):
        parse.rows_read += 1

        sequential_id = nz.clean(raw.get("SQ_CANDIDATO"))
        full_name = nz.clean(raw.get("NM_CANDIDATO"))
        if not sequential_id or not full_name:
            parse.rows_rejected += 1
            continue

        person_id = resolver.person(
            parse.id,
            name=full_name,
            cpf=raw.get("NR_CPF_CANDIDATO"),
            voter_id=raw.get("NR_TITULO_ELEITORAL_CANDIDATO"),
        )

        rows.append(
            (
                person_id,
                nz.int_or_none(raw.get("ANO_ELEICAO")) or year,
                nz.int_or_none(raw.get("NR_TURNO")),
                sequential_id,
                nz.clean(raw.get("NM_URNA_CANDIDATO")),
                full_name,
                nz.int_or_none(raw.get("NR_PARTIDO")),
                nz.clean(raw.get("SG_PARTIDO")),
                nz.clean(raw.get("NM_COLIGACAO")),
                nz.clean(raw.get("DS_CARGO")) or "DESCONHECIDO",
                nz.clean(raw.get("SG_UF")),
                nz.clean(raw.get("NM_UE")),
                nz.clean(raw.get("DS_SITUACAO_CANDIDATURA")),
                nz.clean(raw.get("DS_SIT_TOT_TURNO")),
                nz.parse_date(raw.get("DT_NASCIMENTO")),
                nz.clean(raw.get("DS_GRAU_INSTRUCAO")),
                nz.clean(raw.get("DS_OCUPACAO")),
                parse.id,
            )
        )

        if len(rows) >= 5_000:
            parse.rows_written += _flush(conn, rows)
            rows = []

    if rows:
        parse.rows_written += _flush(conn, rows)


INSERT_SQL = """
INSERT INTO politician_history (
    person_id, election_year, election_round, sequential_id, ballot_name,
    full_name, party_number, party_acronym, coalition, office, uf, municipality,
    registration_status, result, birth_date, education, occupation, provenance_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (election_year, sequential_id) DO NOTHING
"""


def _flush(conn, rows: list[tuple]) -> int:
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, rows)
    conn.commit()
    return len(rows)
