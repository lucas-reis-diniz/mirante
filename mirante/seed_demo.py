"""Dados sintéticos para desenvolvimento.

Problema real que isto resolve: sem ele, qualquer pessoa que queira mexer na
interface precisa primeiro baixar dezenas de GB do TSE. Isso inviabiliza
contribuição e torna impossível rodar teste de integração em CI.

Os dados aqui são INVENTADOS e desenhados para serem obviamente falsos: nomes
de pessoas fictícias, CNPJs de teste, a palavra DEMONSTRAÇÃO no nome do órgão
de origem. Nenhum valor daqui descreve pessoa ou empresa real, e a URL de
proveniência aponta para `exemplo.invalid`, domínio reservado que nunca
resolve — então é impossível confundir com coleta de verdade.

Mesmo sendo sintético, passa pelos MESMOS caminhos de código que a coleta
real: a camada de proveniência, o EntityResolver e, depois, as regras. É isso
que o torna útil para verificação.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from .sources.base import EntityResolver

SOURCE_SLUG = "demo"
MARKER = "DEMONSTRAÇÃO — dados sintéticos, não correspondem a ninguém"

# CPFs e CNPJs com dígito verificador válido, para passar pela normalização.
# São números de teste, não pertencem a pessoa ou empresa alguma.
PEOPLE = [
    ("11144477735", "100000000001", "ANA PAULA FICTÍCIA DOS SANTOS"),
    ("12345678909", "100000000002", "BRUNO EXEMPLO DE ALMEIDA"),
    ("11122233396", "100000000003", "CARLA HIPOTÉTICA NOGUEIRA"),
]

CANDIDATES = [
    # (sequential_id, nome, cargo, uf, partido, resultado, cpf)
    ("900000000001", "ANA PAULA FICTÍCIA DOS SANTOS", "DEPUTADO FEDERAL", "SP", "PXX", "ELEITO", "11144477735"),
    ("900000000002", "BRUNO EXEMPLO DE ALMEIDA", "DEPUTADO ESTADUAL", "RJ", "PYY", "NAO ELEITO", "12345678909"),
    ("900000000003", "CARLA HIPOTÉTICA NOGUEIRA", "SENADOR", "MG", "PZZ", "ELEITO", "11122233396"),
]

# CNPJs com dígito verificador VÁLIDO. Isto não é detalhe: `nz.cnpj()` recusa
# CNPJ com DV inválido, e a recusa é silenciosa — o resolver devolve None e o
# vínculo desaparece sem erro. A primeira versão destes dados usava números
# inventados à mão, todos recusados, e o resultado foi uma base que parecia
# carregada e não tinha vínculo nenhum. Daí o `_assert_linked` no fim.
CAMPAIGN_CNPJS = ["11222333000181", "11444777000161", "11777888000190"]

# Empresas de apoio. A primeira é sancionada E doadora E fornecedora da cota —
# é o que dá à regra de contraparte sancionada algo para encontrar.
COMPANIES = [
    ("22333444000181", "GRÁFICA INVENTADA LTDA (DEMO)"),
    ("33444555000181", "EVENTOS IMAGINÁRIOS ME (DEMO)"),
    ("44555666000181", "CONSULTORIA FABULADA EIRELI (DEMO)"),
]

# Despesas desenhadas para exercitar a regra de despesa desproporcional:
# muitas canetas baratas estabelecem a mediana, e uma fora da curva vira sinal.
PEN_EXPENSES = [1_500, 2_000, 1_800, 2_200, 1_900, 2_500, 1_700, 2_100, 2_300, 1_600,
                2_400, 1_950, 2_050, 1_850, 2_150, 2_250, 1_750, 2_350, 1_650, 2_450]
PEN_OUTLIER = 9_500_000  # R$ 95.000,00 em canetas — 40x+ a mediana


def run(conn) -> dict:
    """Carrega o conjunto sintético. Rewrite-only, como os crawlers de verdade."""
    report: dict = {"synthetic": True, "marker": MARKER}

    with conn.cursor() as cur:
        cur.execute(
            """
            TRUNCATE campaign_expense_payment, campaign_expense, campaign_donation,
                     campaign_org, declared_asset, social_media, parliamentary_expense,
                     sanction, politician_history, mandate, earmark,
                     company_partner, company_registry, companies, people,
                     rejected_cpf, candidate_supplier_partner,
                     signal_evidence, signal_actor, signal, rule_run,
                     parse, collection_file, collection, source
            RESTART IDENTITY CASCADE
            """
        )

        # -- proveniência sintética, marcada como tal --------------------------
        cur.execute(
            """
            INSERT INTO source (slug, name, base_url, legal_basis)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (
                SOURCE_SLUG,
                f"Fonte de demonstração — {MARKER}",
                "https://exemplo.invalid/",
                "Nenhuma: dados inventados para desenvolvimento",
            ),
        )
        source_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO collection (source_id, crawler, crawler_version, started_at,
                                    finished_at, status, args, note)
            VALUES (%s, 'seed-demo', '1.0', %s, %s, 'ok', %s, %s) RETURNING id
            """,
            (
                source_id,
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
                json.dumps({"synthetic": True}),
                MARKER,
            ),
        )
        collection_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO collection_file (collection_id, url, accessed_at, http_status,
                                         byte_size, sha256, media_type, note)
            VALUES (%s, %s, %s, 200, 4096, %s, 'application/zip', %s) RETURNING id
            """,
            (
                collection_id,
                "https://exemplo.invalid/demo/dados_sinteticos.zip",
                datetime.now(timezone.utc),
                "0" * 64,  # hash nulo: sinaliza que não há arquivo real por trás
                MARKER,
            ),
        )
        file_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO parse (collection_file_id, inner_path, inner_sha256, parser,
                               reference_year, rows_read, rows_written)
            VALUES (%s, 'demo_2026.csv', %s, 'demo.v1', 2026, 0, 0) RETURNING id
            """,
            (file_id, "0" * 64),
        )
        pid = cur.fetchone()["id"]

    conn.commit()

    resolver = EntityResolver(conn)
    resolver.warm_cache()

    # -- pessoas e empresas, pelo resolver de verdade -------------------------
    for cpf, voter, name in PEOPLE:
        resolver.person(pid, name=name, cpf=cpf, voter_id=voter)

    company_ids = [resolver.company(pid, c, n, kind="supplier") for c, n in COMPANIES]
    if any(cid is None for cid in company_ids):
        # Falha cedo e alto. Um None aqui significa CNPJ recusado pela
        # normalização, e seguir adiante produz base silenciosamente vazia de
        # vínculos — exatamente o modo de falha que este projeto não tolera.
        raise RuntimeError(
            "CNPJ de demonstração recusado pela normalização (dígito "
            "verificador inválido). Corrija COMPANIES em seed_demo.py."
        )
    conn.commit()

    # -- candidaturas ---------------------------------------------------------
    history_ids: list[int] = []
    with conn.cursor() as cur:
        for seq, name, office, uf, party, result, cpf in CANDIDATES:
            cur.execute("SELECT id FROM people WHERE cpf = %s", (cpf,))
            row = cur.fetchone()
            cur.execute(
                """
                INSERT INTO politician_history
                    (person_id, election_year, election_round, sequential_id, ballot_name,
                     full_name, party_number, party_acronym, coalition, office, uf,
                     municipality, registration_status, result, birth_date, education,
                     occupation, provenance_id)
                VALUES (%s, 2026, 1, %s, %s, %s, 99, %s, 'COLIGAÇÃO DEMONSTRATIVA',
                        %s, %s, %s, 'DEFERIDO', %s, %s, 'SUPERIOR COMPLETO',
                        'OCUPAÇÃO FICTÍCIA', %s)
                RETURNING id
                """,
                (
                    row["id"] if row else None,
                    seq,
                    name.split()[0],
                    name,
                    party,
                    office,
                    uf,
                    uf,
                    result,
                    date(1980, 1, 1),
                    pid,
                ),
            )
            history_ids.append(cur.fetchone()["id"])

        # -- CNPJ de campanha ------------------------------------------------
        org_ids: list[int] = []
        for i, (hid, cnpj_value) in enumerate(zip(history_ids, CAMPAIGN_CNPJS)):
            cur.execute(
                """
                INSERT INTO companies (cnpj, legal_name, kind, provenance_id)
                VALUES (%s, %s, ARRAY['campaign'], %s) RETURNING id
                """,
                (cnpj_value, f"CAMPANHA DEMONSTRATIVA {i + 1}", pid),
            )
            cid = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO campaign_org (politician_history_id, company_id, election_year, provenance_id)
                VALUES (%s, %s, 2026, %s) RETURNING id
                """,
                (hid, cid, pid),
            )
            org_ids.append(cur.fetchone()["id"])

        # -- bens declarados --------------------------------------------------
        for hid, value in zip(history_ids, (45_000_000, 12_000_000, 230_000_000)):
            cur.execute(
                """
                INSERT INTO declared_asset
                    (politician_history_id, election_year, asset_kind, description,
                     value_cents, provenance_id)
                VALUES (%s, 2026, 'IMÓVEL', 'Bem declarado fictício', %s, %s)
                """,
                (hid, value, pid),
            )

        # -- doações ----------------------------------------------------------
        # Itera pareado. A versão anterior usava company_ids.index(company_id)
        # para recuperar o CNPJ, que com None devolve sempre o índice 0 — e foi
        # o que fez as três doações saírem com o mesmo doador sem ninguém notar.
        for org_id, company_id, (donor_cnpj, donor_name) in zip(
            org_ids, company_ids, COMPANIES
        ):
            cur.execute(
                """
                INSERT INTO campaign_donation
                    (campaign_org_id, election_year, donor_cpf_cnpj, donor_name,
                     donor_company_id, amount_cents, donated_on, resource_origin,
                     resource_kind, provenance_id)
                VALUES (%s, 2026, %s, %s, %s, %s, %s, 'FUNDO ELEITORAL',
                        'Doação fictícia', %s)
                """,
                (
                    org_id,
                    donor_cnpj,
                    donor_name,
                    company_id,
                    5_000_000,
                    date(2026, 8, 15),
                    pid,
                ),
            )

        # Doação circular: a primeira empresa doa e recebe da mesma campanha.
        cur.execute(
            """
            INSERT INTO campaign_expense
                (campaign_org_id, election_year, supplier_cpf_cnpj, supplier_name,
                 supplier_company_id, amount_cents, contracted_on, description,
                 expense_kind, provenance_id)
            VALUES (%s, 2026, %s, %s, %s, %s, %s,
                    'SERVIÇO FICTÍCIO DE DEMONSTRAÇÃO', 'SERVIÇOS', %s)
            """,
            (
                org_ids[0],
                COMPANIES[0][0],
                COMPANIES[0][1],
                company_ids[0],
                3_200_000,
                date(2026, 9, 1),
                pid,
            ),
        )

        # -- despesas de caneta: estabelecem a mediana e uma fora da curva ----
        for amount in PEN_EXPENSES:
            cur.execute(
                """
                INSERT INTO campaign_expense
                    (campaign_org_id, election_year, supplier_cpf_cnpj, supplier_name,
                     supplier_company_id, amount_cents, contracted_on, description,
                     expense_kind, provenance_id)
                VALUES (%s, 2026, %s, %s, %s, %s, %s,
                        'CANETA ESFEROGRAFICA PERSONALIZADA', 'MATERIAL', %s)
                """,
                (org_ids[1], COMPANIES[1][0], COMPANIES[1][1], company_ids[1],
                 amount, date(2026, 8, 20), pid),
            )

        cur.execute(
            """
            INSERT INTO campaign_expense
                (campaign_org_id, election_year, supplier_cpf_cnpj, supplier_name,
                 supplier_company_id, amount_cents, contracted_on, description,
                 expense_kind, provenance_id)
            VALUES (%s, 2026, %s, %s, %s, %s, %s,
                    'CANETA ESFEROGRAFICA PERSONALIZADA - LOTE', 'MATERIAL', %s)
            """,
            (org_ids[2], COMPANIES[2][0], COMPANIES[2][1], company_ids[2],
             PEN_OUTLIER, date(2026, 8, 25), pid),
        )

        # -- cota parlamentar -------------------------------------------------
        for month in range(1, 7):
            cur.execute(
                """
                INSERT INTO parliamentary_expense
                    (house, external_id, full_name, party_acronym, uf, reference_year,
                     reference_month, category, supplier_name, supplier_cpf_cnpj,
                     supplier_company_id, amount_cents, issued_on, provenance_id)
                VALUES ('camara', '999001', %s, 'PXX', 'SP', 2026, %s,
                        'DIVULGACAO DA ATIVIDADE PARLAMENTAR', %s, %s, %s,
                        %s, %s, %s)
                """,
                (CANDIDATES[0][1], month, COMPANIES[0][1], COMPANIES[0][0],
                 company_ids[0], 1_200_000 + month * 10_000,
                 date(2026, month, 10), pid),
            )

        # -- sanção: vigente desde antes dos pagamentos acima -----------------
        cur.execute(
            """
            INSERT INTO sanction
                (registry, cpf_cnpj, company_id, sanctioned_name, sanction_kind,
                 legal_basis, started_on, ended_on, sanctioning_body, process_number,
                 provenance_id)
            VALUES ('CEIS', %s, %s, %s, 'IMPEDIMENTO FICTÍCIO',
                    'Fundamentação de demonstração', %s, %s,
                    'ÓRGÃO DEMONSTRATIVO', 'PROC-DEMO-0001', %s)
            """,
            (COMPANIES[0][0], company_ids[0], COMPANIES[0][1],
             date(2026, 1, 1), date(2027, 12, 31), pid),
        )

    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM politician_history)    AS candidaturas,
              (SELECT count(*) FROM campaign_donation)     AS doacoes,
              (SELECT count(*) FROM campaign_expense)      AS despesas,
              (SELECT count(*) FROM parliamentary_expense) AS cota,
              (SELECT count(*) FROM sanction)              AS sancoes
            """
        )
        report["counts"] = dict(cur.fetchone())

    return report
