"""Testes do parser de votações da Câmara. Sem rede e sem banco.

Os exemplos reproduzem o formato real dos arquivos anuais e da API (campos e
aninhamento conferidos em votacoes-2026.json, votacoesObjetos-2026.json,
votacoesOrientacoes-2026.json e /api/v2/votacoes/{id}/votos).

O ponto mais delicado é `match_party_bloc`: é ele que permite dizer "votou
diferente da orientação do próprio partido". Uma correspondência errada aqui
vira uma afirmação falsa sobre uma pessoa real, então o comportamento na
dúvida é NÃO afirmar.
"""

from datetime import date, datetime, timezone

from mirante.sources.camara_votes import (
    approved,
    bloc_members,
    brasilia_to_utc,
    file_url,
    flatten,
    match_party_bloc,
    orientation_row,
    records,
    session_row,
    subject_row,
    vote_row,
)

SESSION = {
    "id": "2611313-31",
    "uri": "https://dadosabertos.camara.leg.br/api/v2/votacoes/2611313-31",
    "data": "2026-09-03",
    "dataHoraRegistro": "2026-09-03T17:28:34",
    "idOrgao": 180,
    "siglaOrgao": "PLEN",
    "aprovacao": 1,
    "votosSim": 346,
    "votosNao": 46,
    "votosOutros": 3,
    "descricao": "Aprovada a Subemenda Substitutiva ao Projeto de Lei Complementar nº 74, de 2026.",
    "ultimaAberturaVotacao": {"dataHoraRegistro": "", "descricao": ""},
}

SUBJECT = {
    "idVotacao": "2555417-29",
    "data": "2026-02-02",
    "descricao": "Aprovada a Medida Provisória nº 1.312, de 2025.",
    "proposicao_": {
        "id": 2592069,
        "ementa": "Abre crédito extraordinário em favor do Ministério da Agricultura.",
        "codTipo": 187,
        "siglaTipo": "PAR",
        "numero": 25,
        "ano": 2025,
        "titulo": "PAR 25/2025 => MPV 1312/2025",
    },
}

ORIENTATION = {
    "idVotacao": "2638483-34",
    "siglaOrgao": "PLEN",
    "siglaBancada": "Fdr PT-PCdoB-PV",
    "uriBancada": "",
    "orientacao": "Sim",
}

VOTE_API = {
    "idVotacao": "2611313-31",
    "tipoVoto": "Sim",
    "dataRegistroVoto": "2026-09-03T17:28:19",
    "deputado_": {
        "id": 160559,
        "nome": "Alceu Moreira",
        "siglaPartido": "MDB",
        "siglaUf": "RS",
        "idLegislatura": 57,
        "urlFoto": "https://www.camara.leg.br/internet/deputado/bandep/160559.jpg",
    },
}


class TestFlatten:
    def test_sufixo_underscore_vira_prefixo_do_csv(self):
        flat = flatten(VOTE_API)
        assert flat["deputado_id"] == 160559
        assert flat["deputado_siglaPartido"] == "MDB"

    def test_objeto_sem_sufixo_ganha_underscore(self):
        flat = flatten(SESSION)
        assert "ultimaAberturaVotacao_descricao" in flat

    def test_records_aceita_envelope_dados(self):
        assert records({"dados": [SUBJECT]})[0]["proposicao_titulo"] == "PAR 25/2025 => MPV 1312/2025"
        assert records([SUBJECT])[0]["idVotacao"] == "2555417-29"


class TestSession:
    def test_campos_principais(self):
        row = session_row(flatten(SESSION))
        assert row["external_id"] == "2611313-31"
        assert row["voted_on"] == date(2026, 9, 3)
        assert row["body_acronym"] == "PLEN"
        assert row["approved"] is True
        assert (row["yes_count"], row["no_count"], row["other_count"]) == (346, 46, 3)

    def test_sem_descricao_e_descartada(self):
        assert session_row(flatten({**SESSION, "descricao": ""})) is None

    def test_aprovacao_vazia_nao_vira_rejeitada(self):
        assert approved("") is None
        assert approved(0) is False
        assert approved("1") is True


class TestTimezone:
    def test_horario_de_brasilia_vira_utc(self):
        # Setembro de 2026, sem horário de verão: UTC-3.
        assert brasilia_to_utc("2026-09-03T17:28:34") == datetime(2026, 9, 3, 20, 28, 34, tzinfo=timezone.utc)

    def test_horario_de_verao_historico(self):
        # Janeiro de 2018 tinha horário de verão em Brasília: UTC-2.
        assert brasilia_to_utc("2018-01-10T12:00:00") == datetime(2018, 1, 10, 14, 0, tzinfo=timezone.utc)

    def test_vazio_e_none(self):
        assert brasilia_to_utc("") is None
        assert brasilia_to_utc(None) is None


class TestSubjectAndOrientation:
    def test_objeto(self):
        row = subject_row(flatten(SUBJECT))
        assert row["kind"] == "PAR"
        assert row["number"] == 25
        assert row["title"] == "PAR 25/2025 => MPV 1312/2025"

    def test_ano_zero_vira_none(self):
        # A API usa ano 0 em documentos acessórios (destaques, pareceres).
        raw = flatten({**SUBJECT, "proposicao_": {**SUBJECT["proposicao_"], "ano": 0}})
        assert subject_row(raw)["year"] is None

    def test_orientacao(self):
        row = orientation_row(ORIENTATION)
        assert row == {"session": "2638483-34", "bloc": "Fdr PT-PCdoB-PV", "orientation": "Sim"}


class TestVote:
    def test_formato_da_api(self):
        row = vote_row(flatten(VOTE_API))
        assert row["deputy_id"] == "160559"
        assert row["vote"] == "Sim"
        assert row["party"] == "MDB"
        assert row["voted_at"] == datetime(2026, 9, 3, 20, 28, 19, tzinfo=timezone.utc)

    def test_formato_do_csv(self):
        csv_like = {
            "idVotacao": "2611313-31",
            "voto": "Não",
            "dataHoraVoto": "2026-09-03T17:28:19",
            "deputado_id": "160561",
            "deputado_nome": "Fulano",
            "deputado_siglaPartido": "PL",
            "deputado_siglaUf": "SP",
        }
        row = vote_row(csv_like)
        assert (row["deputy_id"], row["vote"], row["party"]) == ("160561", "Não", "PL")

    def test_sem_voto_e_descartado(self):
        assert vote_row(flatten({**VOTE_API, "tipoVoto": ""})) is None


class TestBlocMatching:
    def test_membros_de_federacao(self):
        assert bloc_members("Fdr PT-PCdoB-PV") == {"PT", "PCDOB", "PV"}

    def test_bloco_sem_separador_em_camelcase(self):
        assert bloc_members("Bl UniãoPpPsd") == {"UNIAO", "PP", "PSD"}

    def test_partido_proprio_tem_prioridade(self):
        blocs = ["PT", "Fdr PT-PCdoB-PV", "Governo"]
        assert match_party_bloc("PT", blocs) == "PT"

    def test_federacao_quando_partido_nao_orienta_sozinho(self):
        assert match_party_bloc("PCdoB", ["Fdr PT-PCdoB-PV", "PL", "Governo"]) == "Fdr PT-PCdoB-PV"

    def test_acento_e_caixa_nao_importam(self):
        assert match_party_bloc("UNIÃO", ["Bl União, PP"]) == "Bl União, PP"

    def test_psd_nao_casa_com_psdb(self):
        # Substring ingênua faria 'PSD' casar com a federação do PSDB.
        assert match_party_bloc("PSD", ["Fdr PSDB-CIDADANIA", "PL"]) is None

    def test_governo_nunca_e_orientacao_de_partido(self):
        assert match_party_bloc("GOVERNO", ["Governo"]) is None

    def test_ambiguo_nao_afirma(self):
        # Partido em duas bancadas ao mesmo tempo: na dúvida, None.
        assert match_party_bloc("PP", ["Bl PP-PSD", "Bl PP-MDB"]) is None

    def test_sem_partido(self):
        assert match_party_bloc(None, ["PL"]) is None


def test_url_dos_arquivos_anuais():
    assert file_url("votes", 2026) == (
        "https://dadosabertos.camara.leg.br/arquivos/votacoesVotos/json/votacoesVotos-2026.json"
    )
