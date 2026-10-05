"""Crawler: votações da Câmara — o que foi votado e como cada deputado votou.

Quatro arquivos anuais, todos de https://dadosabertos.camara.leg.br/arquivos/:

    votacoes             uma linha por votação (plenário e comissões)
    votacoesObjetos      o que estava em votação (proposição, ementa)
    votacoesOrientacoes  o que cada bancada orientou
    votacoesVotos        o voto de cada deputado, só em votação nominal

Usamos a versão JSON dos arquivos, não o CSV. Mesmo conteúdo, mas o JSON
aninha (`deputado_: {id, nome}`) e o CSV achata com o mesmo prefixo
(`deputado_id`). Este módulo achata o JSON para os nomes do CSV, então o
parser é um só e funciona com qualquer dos dois formatos.

Por que importa para o projeto: a cota parlamentar mostra como o mandato
GASTA. A votação mostra o que o mandato FAZ. O identificador do deputado é o
mesmo nas duas bases, então dá para pôr lado a lado sem resolver identidade.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .. import normalize as nz
from ..provenance import Collector
from .base import chunked

VERSION = "1.0"

BASE = "https://dadosabertos.camara.leg.br/arquivos"
FILES = {
    "sessions": "votacoes",
    "subjects": "votacoesObjetos",
    "orientations": "votacoesOrientacoes",
    "votes": "votacoesVotos",
}
API_VOTE_URL = "https://dadosabertos.camara.leg.br/api/v2/votacoes/{id}"

# Ordem importa: TRUNCATE ... CASCADE resolve dependências, mas listar
# explicitamente documenta o que este crawler possui.
OWNED_TABLES = ("vote_cast", "vote_orientation", "vote_subject", "vote_session", "legislator")

BRASILIA = ZoneInfo("America/Sao_Paulo")


def file_url(kind: str, year: int) -> str:
    name = FILES[kind]
    return f"{BASE}/{name}/json/{name}-{year}.json"


# ---------------------------------------------------------------------------
# Funções puras — testáveis sem banco e sem rede
# ---------------------------------------------------------------------------


def flatten(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Achata o JSON da Câmara para os nomes de coluna do CSV.

    `{"deputado_": {"id": 1}}`            -> `{"deputado_id": 1}`
    `{"ultimaAberturaVotacao": {"x": 1}}` -> `{"ultimaAberturaVotacao_x": 1}`

    A Câmara usa o sufixo `_` nos objetos aninhados justamente para que o
    achatamento produza o nome do CSV; quando o sufixo falta, acrescentamos.
    """
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, name if name.endswith("_") else f"{name}_"))
        else:
            out[name] = value
    return out


def text(value: Any) -> str | None:
    if value is None:
        return None
    return nz.clean(str(value))


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return nz.int_or_none(str(value))


def brasilia_to_utc(value: Any) -> datetime | None:
    """'2026-09-03T17:28:34' (horário de Brasília, sem fuso) -> UTC.

    A Câmara publica hora local sem offset. A convenção do banco é UTC; a
    conversão acontece aqui, uma vez, com fuso de verdade (horário de verão
    existiu até 2019 e a série histórica passa por ele).
    """
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BRASILIA)
    return parsed.astimezone(timezone.utc)


def iso_date(value: Any) -> date | None:
    raw = text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return nz.parse_date(raw)


def approved(value: Any) -> bool | None:
    """`aprovacao` vem como 1/0, às vezes vazio. Vazio não é 'rejeitada'."""
    n = int_or_none(value)
    if n == 1:
        return True
    if n == 0:
        return False
    return None


def _fold(value: str) -> str:
    """Maiúsculo e sem acento: 'União' e 'UNIAO' precisam bater."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


# Prefixos que a Câmara usa no nome de federação e bloco.
_BLOC_PREFIX = re.compile(r"^(FDR|FED|BL|BLOCO)\b\.?\s*", re.IGNORECASE)
_SEPARATORS = re.compile(r"[\s,;/+\-]+")
# Partidos cuja grafia mistura maiúscula e minúscula e quebrariam a separação
# por camelCase ('PCdoB' viraria 'P Cdo B').
_MIXED_CASE_PARTIES = ("PCdoB",)


def bloc_members(bloc: str) -> set[str]:
    """Partidos contidos no nome de uma bancada.

    'PL'                -> {'PL'}
    'Fdr PT-PCdoB-PV'   -> {'PT', 'PCDOB', 'PV'}
    'Bl UniãoPpPsd'     -> {'UNIAO', 'PP', 'PSD'}   (bloco escrito sem separador)
    """
    name = _BLOC_PREFIX.sub("", bloc.strip())
    for party in _MIXED_CASE_PARTIES:
        name = re.sub(party, party.upper(), name, flags=re.IGNORECASE)
    tokens = [t for t in _SEPARATORS.split(name) if t]
    # Sem separador nenhum e em camelCase: 'UniãoPpPsd'. Quebra antes de cada
    # maiúscula que segue uma minúscula.
    if len(tokens) == 1 and re.search(r"[a-zà-ú][A-ZÀ-Ú]", tokens[0]):
        tokens = re.sub(r"(?<=[a-zà-ú])(?=[A-ZÀ-Ú])", " ", tokens[0]).split()
    return {_fold(t) for t in tokens}


# Bancadas que não são partido. Nunca servem de "orientação do partido".
NON_PARTY_BLOCS = {"GOVERNO", "MAIORIA", "MINORIA", "OPOSICAO", "REPR.GOV", "REPRGOV"}


def match_party_bloc(party: str | None, blocs: Iterable[str]) -> str | None:
    """A bancada cuja orientação vale para um deputado deste partido.

    Prioridade: o próprio partido; senão a única federação ou bloco que o
    contém. Se o partido aparece em mais de uma bancada, devolve None — na
    dúvida, não afirmamos (mesma disciplina de ADs/identidade.md).
    """
    if not party:
        return None
    target = _fold(party.strip())
    candidates = [b for b in blocs if _fold(b.strip()) not in NON_PARTY_BLOCS]

    exact = [b for b in candidates if _fold(b.strip()) == target]
    if len(exact) == 1:
        return exact[0]

    containing = [b for b in candidates if target in bloc_members(b)]
    if len(containing) == 1:
        return containing[0]
    return None


def session_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Uma linha de `votacoes` achatada -> colunas de vote_session."""
    external_id = text(raw.get("id"))
    voted_on = iso_date(raw.get("data"))
    description = text(raw.get("descricao"))
    if not external_id or voted_on is None or not description:
        return None
    return {
        "external_id": external_id,
        "voted_on": voted_on,
        "registered_at": brasilia_to_utc(raw.get("dataHoraRegistro")),
        "body_acronym": text(raw.get("siglaOrgao")),
        "approved": approved(raw.get("aprovacao")),
        "yes_count": int_or_none(raw.get("votosSim")),
        "no_count": int_or_none(raw.get("votosNao")),
        "other_count": int_or_none(raw.get("votosOutros")),
        "description": description,
        "source_url": text(raw.get("uri")) or API_VOTE_URL.format(id=external_id),
    }


def subject_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    session = text(raw.get("idVotacao"))
    if not session:
        return None
    return {
        "session": session,
        "proposition_id": text(raw.get("proposicao_id")),
        "kind": text(raw.get("proposicao_siglaTipo")),
        "number": int_or_none(raw.get("proposicao_numero")),
        "year": int_or_none(raw.get("proposicao_ano")) or None,
        "title": text(raw.get("proposicao_titulo")),
        "summary": text(raw.get("proposicao_ementa")),
    }


def orientation_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    session = text(raw.get("idVotacao"))
    bloc = text(raw.get("siglaBancada"))
    orientation = text(raw.get("orientacao"))
    if not session or not bloc or not orientation:
        return None
    return {"session": session, "bloc": bloc, "orientation": orientation}


def vote_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Uma linha de `votacoesVotos` achatada.

    Tolerante a nome de campo: o arquivo anual usa `voto`/`dataHoraVoto`, a
    API usa `tipoVoto`/`dataRegistroVoto`. Os dois chegam ao mesmo lugar.
    """
    session = text(raw.get("idVotacao"))
    deputy = text(raw.get("deputado_id"))
    vote = text(raw.get("voto") or raw.get("tipoVoto"))
    if not session or not deputy or not vote:
        return None
    return {
        "session": session,
        "deputy_id": deputy,
        "name": text(raw.get("deputado_nome")) or deputy,
        "party": text(raw.get("deputado_siglaPartido")),
        "uf": text(raw.get("deputado_siglaUf")),
        "photo_url": text(raw.get("deputado_urlFoto")),
        "vote": vote,
        "voted_at": brasilia_to_utc(raw.get("dataHoraVoto") or raw.get("dataRegistroVoto")),
    }


def records(payload: Any) -> list[dict[str, Any]]:
    """Os arquivos vêm como `{"dados": [...]}`; aceita lista crua também."""
    items = payload.get("dados", []) if isinstance(payload, dict) else payload
    return [flatten(item) for item in items if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


def run(conn, years: list[int] | None = None) -> dict:
    years = sorted(years or [date.today().year])
    report: dict = {"years": {}}

    with Collector(conn, "camara", "camara-votes", VERSION, args={"years": years}) as collector:
        collector.truncate(*OWNED_TABLES)
        legislators: dict[str, int] = {}

        for year in years:
            print(f"\n[{year}] votações da Câmara")
            counts: dict[str, int] = {}
            sessions: dict[str, int] = {}

            fetched = collector.fetch(file_url("sessions", year), reuse_existing=False)
            with collector.parse(fetched, fetched.path.name, "camara.votacoes.v1", year) as parse:
                sessions = _load_sessions(conn, _read(fetched.path), parse)
                counts["sessions"] = parse.rows_written

            fetched = collector.fetch(file_url("subjects", year), reuse_existing=False)
            with collector.parse(fetched, fetched.path.name, "camara.votacoes_objetos.v1", year) as parse:
                _load_subjects(conn, _read(fetched.path), parse, sessions)
                counts["subjects"] = parse.rows_written

            fetched = collector.fetch(file_url("orientations", year), reuse_existing=False)
            with collector.parse(
                fetched, fetched.path.name, "camara.votacoes_orientacoes.v1", year
            ) as parse:
                blocs = _load_orientations(conn, _read(fetched.path), parse, sessions)
                counts["orientations"] = parse.rows_written

            fetched = collector.fetch(file_url("votes", year), reuse_existing=False)
            with collector.parse(fetched, fetched.path.name, "camara.votacoes_votos.v1", year) as parse:
                matched = _load_votes(conn, _read(fetched.path), parse, sessions, blocs, legislators)
                counts["votes"] = parse.rows_written
                counts["votes_with_party_orientation"] = matched

            report["years"][year] = counts
            conn.commit()

    report["legislators"] = len(legislators)
    return report


def _read(path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as fh:
        return records(json.load(fh))


def _load_sessions(conn, rows: list[dict], parse) -> dict[str, int]:
    """Grava as votações em lote e devolve {id da Câmara: id no banco}.

    Em lote porque o pipeline roda longe do banco: um INSERT ... RETURNING
    por votação eram milhares de idas e voltas de ~150 ms cada.
    """
    batch: list[dict] = []
    seen: set[str] = set()
    for raw in rows:
        parse.rows_read += 1
        row = session_row(raw)
        if row is None or row["external_id"] in seen:
            parse.rows_rejected += 1
            continue
        seen.add(row["external_id"])
        batch.append({**row, "provenance_id": parse.id})

    ids: dict[str, int] = {}
    with conn.cursor() as cur:
        for chunk in chunked(batch, 2_000):
            cur.executemany(
                """
                INSERT INTO vote_session (
                    house, external_id, voted_on, registered_at, body_acronym, approved,
                    yes_count, no_count, other_count, description, source_url, provenance_id
                ) VALUES ('camara', %(external_id)s, %(voted_on)s, %(registered_at)s,
                          %(body_acronym)s, %(approved)s, %(yes_count)s, %(no_count)s,
                          %(other_count)s, %(description)s, %(source_url)s, %(provenance_id)s)
                """,
                chunk,
            )
            cur.execute(
                "SELECT id, external_id FROM vote_session WHERE house = 'camara' AND external_id = ANY(%s)",
                ([r["external_id"] for r in chunk],),
            )
            for r in cur.fetchall():
                ids[r["external_id"]] = r["id"]
    parse.rows_written += len(batch)
    return ids


def _load_subjects(conn, rows: list[dict], parse, sessions: dict[str, int]) -> None:
    batch: list[tuple] = []
    for raw in rows:
        parse.rows_read += 1
        row = subject_row(raw)
        session_id = sessions.get(row["session"]) if row else None
        if row is None or session_id is None:
            parse.rows_rejected += 1
            continue
        batch.append(
            (session_id, row["proposition_id"], row["kind"], row["number"], row["year"],
             row["title"], row["summary"], parse.id)
        )
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO vote_subject
                (vote_session_id, proposition_id, kind, number, year, title, summary, provenance_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            batch,
        )
    parse.rows_written += len(batch)


def _load_orientations(
    conn, rows: list[dict], parse, sessions: dict[str, int]
) -> dict[str, dict[str, str]]:
    """Grava e devolve {votação: {bancada: orientação}} para casar com os votos."""
    blocs: dict[str, dict[str, str]] = {}
    batch: list[tuple] = []
    for raw in rows:
        parse.rows_read += 1
        row = orientation_row(raw)
        session_id = sessions.get(row["session"]) if row else None
        if row is None or session_id is None:
            parse.rows_rejected += 1
            continue
        blocs.setdefault(row["session"], {})[row["bloc"]] = row["orientation"]
        batch.append((session_id, row["bloc"], row["orientation"], parse.id))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO vote_orientation (vote_session_id, bloc, orientation, provenance_id)
            VALUES (%s, %s, %s, %s)
            """,
            batch,
        )
    parse.rows_written += len(batch)
    return blocs


def _load_votes(
    conn,
    rows: list[dict],
    parse,
    sessions: dict[str, int],
    blocs: dict[str, dict[str, str]],
    legislators: dict[str, int],
) -> int:
    """Grava os votos. Devolve quantos foram comparados a uma orientação de partido."""
    parsed = [vote_row(raw) for raw in rows]
    parse.rows_read += len(rows)

    # O voto mais recente define nome, partido e UF exibidos no cadastro.
    latest: dict[str, dict] = {}
    for row in parsed:
        if row is None:
            continue
        current = latest.get(row["deputy_id"])
        stamp = row["voted_at"] or datetime.min.replace(tzinfo=timezone.utc)
        if current is None or stamp >= (current["voted_at"] or datetime.min.replace(tzinfo=timezone.utc)):
            latest[row["deputy_id"]] = row

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO legislator
                (house, external_id, full_name, last_party, last_uf, photo_url, provenance_id)
            VALUES ('camara', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (house, external_id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                last_party = EXCLUDED.last_party,
                last_uf = EXCLUDED.last_uf,
                photo_url = COALESCE(EXCLUDED.photo_url, legislator.photo_url),
                provenance_id = EXCLUDED.provenance_id
            """,
            [
                (deputy_id, row["name"], row["party"], row["uf"], row["photo_url"], parse.id)
                for deputy_id, row in latest.items()
            ],
        )
        cur.execute(
            "SELECT id, external_id FROM legislator WHERE house = 'camara' AND external_id = ANY(%s)",
            (list(latest),),
        )
        for r in cur.fetchall():
            legislators[r["external_id"]] = r["id"]

    matched = 0
    seen: set[tuple[int, int]] = set()
    batch: list[tuple] = []
    for row in parsed:
        session_id = sessions.get(row["session"]) if row else None
        if row is None or session_id is None:
            parse.rows_rejected += 1
            continue
        legislator_id = legislators[row["deputy_id"]]
        if (session_id, legislator_id) in seen:
            parse.rows_rejected += 1
            continue
        seen.add((session_id, legislator_id))

        session_blocs = blocs.get(row["session"], {})
        bloc = match_party_bloc(row["party"], session_blocs)
        if bloc is not None:
            matched += 1
        batch.append(
            (session_id, legislator_id, row["party"], row["uf"], row["vote"], row["voted_at"],
             bloc, session_blocs.get(bloc) if bloc else None, parse.id)
        )
        if len(batch) >= 5_000:
            _flush_votes(conn, batch)
            parse.rows_written += len(batch)
            batch = []
    if batch:
        _flush_votes(conn, batch)
        parse.rows_written += len(batch)
    return matched


def _flush_votes(conn, batch: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO vote_cast (
                vote_session_id, legislator_id, party_acronym, uf, vote, voted_at,
                party_bloc, party_orientation, provenance_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            batch,
        )
    conn.commit()
