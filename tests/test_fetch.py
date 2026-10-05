"""Testes do download e do hash. Sem rede: o zip é montado no próprio teste.

O que está sendo protegido aqui é a promessa central do projeto: o hash que
vai para o manifesto identifica de fato o conteúdo. Se isso quebrar, a base
perde a propriedade que a torna conferível por terceiros.
"""

import json
import zipfile

import pytest

from mirante.fetch import FetchedFile, hash_zip_members, iter_zip_text, sha256_bytes, sha256_file
from mirante.rules.base import Evidence, Signal


@pytest.fixture
def zip_path(tmp_path):
    path = tmp_path / "amostra.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("consulta_cand_2022_SP.csv", "SQ_CANDIDATO;NM_CANDIDATO\n1;JOSE\n")
        zf.writestr("consulta_cand_2022_RJ.csv", "SQ_CANDIDATO;NM_CANDIDATO\n2;MARIA\n")
    return path


class TestHashing:
    def test_hash_de_arquivo_bate_com_hash_de_bytes(self, tmp_path):
        path = tmp_path / "a.txt"
        path.write_bytes(b"dado publico")
        assert sha256_file(path) == sha256_bytes(b"dado publico")

    def test_hash_interno_por_membro(self, zip_path):
        hashes = hash_zip_members(zip_path)
        assert set(hashes) == {"consulta_cand_2022_SP.csv", "consulta_cand_2022_RJ.csv"}
        assert all(len(h) == 64 for h in hashes.values())

    def test_membros_diferentes_tem_hashes_diferentes(self, zip_path):
        hashes = hash_zip_members(zip_path)
        assert len(set(hashes.values())) == 2

    def test_conteudo_igual_gera_hash_igual(self, tmp_path):
        # É isto que permite a terceiros conferirem: mesmo conteúdo, mesmo hash,
        # independente de quando e por quem foi baixado.
        a, b = tmp_path / "a.zip", tmp_path / "b.zip"
        for path in (a, b):
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("x.csv", "col\nvalor\n")
        assert hash_zip_members(a) == hash_zip_members(b)


class TestZipReading:
    def test_filtra_por_padrao(self, zip_path):
        names = [name for name, _ in iter_zip_text(zip_path, pattern="_SP.")]
        assert names == ["consulta_cand_2022_SP.csv"]

    def test_le_conteudo_sem_descompactar(self, zip_path):
        for name, stream in iter_zip_text(zip_path, pattern="_RJ."):
            assert "MARIA" in stream.read()


class TestManifest:
    def test_entrada_tem_tudo_que_prova_a_origem(self, tmp_path):
        from datetime import datetime, timezone

        fetched = FetchedFile(
            url="https://cdn.tse.jus.br/exemplo.zip",
            path=tmp_path / "exemplo.zip",
            sha256="a" * 64,
            byte_size=1234,
            accessed_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
            http_status=200,
            inner_hashes={"x.csv": "b" * 64},
        )
        entry = fetched.manifest_entry()
        # URL + quando + hash: o suficiente para alguém re-baixar e conferir.
        assert entry["url"].startswith("https://")
        assert entry["accessed_at"].startswith("2026-10-04")
        assert entry["sha256"] == "a" * 64
        assert entry["inner_files"]["x.csv"] == "b" * 64
        json.dumps(entry)  # precisa ser serializável para ir ao git


class TestSignalDiscipline:
    """A disciplina epistêmica é testável, então é testada."""

    def test_sinal_sem_evidencia_e_rejeitado(self):
        with pytest.raises(ValueError, match="evidência"):
            Signal(severity="high", headline="afirmação sem lastro")

    def test_severidade_fora_do_vocabulario_e_rejeitada(self):
        with pytest.raises(ValueError, match="severidade"):
            Signal(
                severity="confirmado",
                headline="x",
                evidence=[Evidence("campaign_expense", 1, 1)],
            )

    def test_sinal_valido_passa(self):
        sig = Signal(
            severity="medium",
            headline="Despesa fora da curva",
            evidence=[Evidence("campaign_expense", 42, 7)],
        )
        assert sig.evidence[0].row_id == 42
