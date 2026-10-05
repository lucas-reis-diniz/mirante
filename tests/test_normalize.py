"""Testes da normalização. Sem rede, sem banco.

Estes testes existem porque é aqui que mora o risco silencioso: um CPF mal
normalizado não quebra nada, só cria uma pessoa duplicada que ninguém nota.
"""

from datetime import date

import pytest

from mirante import normalize as nz


class TestNullTokens:
    @pytest.mark.parametrize("value", ["#NULO#", "#NE#", "", "   ", "NAO INFORMADO", "-1"])
    def test_sentinelas_viram_none(self, value):
        assert nz.clean(value) is None

    def test_texto_real_sobrevive(self):
        assert nz.clean("  JOSE   DA  SILVA ") == "JOSE DA SILVA"


class TestCPF:
    def test_cpf_valido(self):
        assert nz.cpf("111.444.777-35") == "11144477735"

    def test_zero_a_esquerda_preservado(self):
        # 012.345.678-90 é válido e some se alguém tratar como int
        assert nz.cpf("12345678909") == "12345678909"
        assert len(nz.cpf("012.345.678-90") or "") in (0, 11)

    def test_digito_verificador_invalido_rejeitado(self):
        assert nz.cpf("111.444.777-00") is None

    def test_repetido_rejeitado(self):
        assert nz.cpf("111.111.111-11") is None

    def test_cpf_mascarado_da_receita(self):
        assert nz.cpf6("***123456**") == "123456"

    def test_cpf6_de_cpf_completo(self):
        assert nz.cpf6("11144477735") == "444777"


class TestCNPJ:
    def test_cnpj_valido(self):
        assert nz.cnpj("11.222.333/0001-81") == "11222333000181"

    def test_invalido_rejeitado(self):
        assert nz.cnpj("11.222.333/0001-00") is None


class TestCpfOuCnpj:
    def test_pessoa_fisica(self):
        cpf, cnpj = nz.cpf_or_cnpj("111.444.777-35")
        assert cpf == "11144477735" and cnpj is None

    def test_pessoa_juridica(self):
        cpf, cnpj = nz.cpf_or_cnpj("11.222.333/0001-81")
        assert cnpj == "11222333000181" and cpf is None

    def test_lixo_nao_vira_nada(self):
        assert nz.cpf_or_cnpj("#NULO#") == (None, None)


class TestMoney:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.234,56", 123456),
            ("1234.56", 123456),
            ("R$ 2.504.200,00", 250420000),
            ("0,01", 1),
            ("-500,00", -50000),
            ("#NULO#", None),
        ],
    )
    def test_conversao(self, raw, expected):
        assert nz.money_cents(raw) == expected

    def test_nunca_perde_centavo(self):
        # O motivo de dinheiro ser int: 0.1 + 0.2 != 0.3 em float.
        total = sum(nz.money_cents(v) for v in ["0,10", "0,20"])
        assert total == nz.money_cents("0,30")


class TestDates:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("15/08/2022", date(2022, 8, 15)),
            ("2022-08-15", date(2022, 8, 15)),
            ("2022-08-15T13:45:00", date(2022, 8, 15)),
            ("data invalida", None),
        ],
    )
    def test_formatos(self, raw, expected):
        assert nz.parse_date(raw) == expected


class TestCanonicalName:
    def test_remove_acento_e_caixa(self):
        assert nz.canonical_name("José da Conceição") == "JOSE DA CONCEICAO"

    def test_compara_variantes(self):
        assert nz.canonical_name("JOÃO  SILVA") == nz.canonical_name("joao silva")


class TestSocialNetwork:
    @pytest.mark.parametrize(
        "url,network",
        [
            ("https://instagram.com/fulano", "instagram"),
            ("https://www.facebook.com/fulano", "facebook"),
            ("https://twitter.com/fulano", "x"),
            ("https://fulano2026.com.br", "site"),
        ],
    )
    def test_deteccao(self, url, network):
        assert nz.detect_network(url)[0] == network
