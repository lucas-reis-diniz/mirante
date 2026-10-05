"""Normalização de campos brasileiros. Puro, sem I/O, 100% testável.

Toda decisão de "isso é o mesmo CPF?" ou "quanto vale esse campo?" passa por
aqui, para que exista um único lugar onde a regra mora — e um único lugar
para corrigir quando a fonte muda de formato (e ela muda).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

# Valores que as fontes usam para dizer "não informado". Tratar como None,
# nunca como string literal, senão viram "pessoas" com nome #NULO.
NULL_TOKENS = {
    "",
    "#NULO#",
    "#NULO",
    "#NE#",
    "#NE",
    "-1",
    "-3",
    "NULO",
    "NAO DIVULGAVEL",
    "NÃO DIVULGÁVEL",
    "NAO INFORMADO",
    "N/A",
    "NA",
    "SEM INFORMACAO",
}

_DIGITS = re.compile(r"\D+")
_SPACES = re.compile(r"\s+")
_MASKED_CPF = re.compile(r"^\*{3}(\d{6})\*{2}$")


def clean(value: str | None) -> str | None:
    """Tira espaços e converte sentinelas de nulo em None."""
    if value is None:
        return None
    stripped = _SPACES.sub(" ", value.strip())
    if stripped.upper() in NULL_TOKENS:
        return None
    return stripped or None


def digits(value: str | None) -> str | None:
    if value is None:
        return None
    only = _DIGITS.sub("", value)
    return only or None


def cpf(value: str | None) -> str | None:
    """CPF com 11 dígitos e dígito verificador válido, ou None.

    Rejeitar CPF inválido no parser evita que lixo de digitação da fonte
    vire uma "pessoa" no banco.
    """
    d = digits(value)
    if d is None:
        return None
    d = d.zfill(11) if len(d) < 11 else d
    if len(d) != 11 or not _valid_cpf(d):
        return None
    return d


def cpf6(value: str | None) -> str | None:
    """Os 6 dígitos visíveis de um CPF mascarado (***123456**).

    A Receita entrega o CPF do sócio assim. Serve para corroborar um match
    por nome — nunca para afirmar identidade sozinho.
    """
    if value is None:
        return None
    m = _MASKED_CPF.match(value.strip())
    if m:
        return m.group(1)
    d = digits(value)
    if d and len(d) == 11:
        return d[3:9]
    return None


def cnpj(value: str | None) -> str | None:
    d = digits(value)
    if d is None:
        return None
    d = d.zfill(14) if len(d) < 14 else d
    if len(d) != 14 or not _valid_cnpj(d):
        return None
    return d


def cpf_or_cnpj(value: str | None) -> tuple[str | None, str | None]:
    """Devolve (cpf, cnpj) — exatamente um preenchido, ou nenhum.

    Nos arquivos do TSE a mesma coluna carrega os dois: doador pessoa física
    e doador pessoa jurídica dividem `NR_CPF_CNPJ_DOADOR`.
    """
    d = digits(value)
    if d is None:
        return None, None
    if len(d) <= 11:
        return cpf(d), None
    return None, cnpj(d)


def _valid_cpf(d: str) -> bool:
    if d == d[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11 % 10
        if check != int(d[size]):
            return False
    return True


def _valid_cnpj(d: str) -> bool:
    if d == d[0] * 14:
        return False
    for size, weights in (
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ):
        total = sum(int(d[i]) * weights[i] for i in range(size))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(d[size]):
            return False
    return True


def money_cents(value: str | None) -> int | None:
    """'1.234,56' ou '1234.56' -> 123456 centavos.

    Dinheiro NUNCA vira float. 0.1 + 0.2 != 0.3 e isso não é aceitável em
    base que serve de base para denúncia.
    """
    v = clean(value)
    if v is None:
        return None
    v = v.replace("R$", "").replace(" ", "")
    negative = v.startswith("-") or (v.startswith("(") and v.endswith(")"))
    v = v.strip("-()")
    if "," in v and "." in v:
        # 1.234,56 (pt-BR) vs 1,234.56 (en-US): o último separador manda
        v = v.replace(".", "").replace(",", ".") if v.rfind(",") > v.rfind(".") else v.replace(",", "")
    elif "," in v:
        v = v.replace(",", ".")
    try:
        cents = int(round(float(v) * 100))
    except ValueError:
        return None
    return -cents if negative else cents


DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%Y-%m-%dT%H:%M:%S", "%d%m%Y")


def parse_date(value: str | None) -> date | None:
    v = clean(value)
    if v is None:
        return None
    v = v.split("T")[0] if "T" in v and len(v) > 10 else v
    # '2025-01-14 00:00:00': data com hora separada por espaço.
    v = v.split(" ")[0] if len(v) > 10 and v[4:5] == "-" else v
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def canonical_name(value: str | None) -> str | None:
    """Maiúsculas, sem acento, espaço único. Chave de comparação de nomes.

    Não é identidade — é só o que permite comparar "JOSÉ DA SILVA" com
    "JOSE DA SILVA". O match de identidade exige mais do que isso.
    """
    v = clean(value)
    if v is None:
        return None
    nfkd = unicodedata.normalize("NFKD", v)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return _SPACES.sub(" ", ascii_only.upper().strip()) or None


def int_or_none(value: str | None) -> int | None:
    d = clean(value)
    if d is None:
        return None
    try:
        return int(float(d.replace(",", ".")))
    except ValueError:
        return None


SOCIAL_PATTERNS = (
    ("instagram", re.compile(r"instagram\.com", re.I)),
    ("facebook", re.compile(r"facebook\.com|fb\.com", re.I)),
    ("x", re.compile(r"twitter\.com|(?<![\w.])x\.com", re.I)),
    ("tiktok", re.compile(r"tiktok\.com", re.I)),
    ("youtube", re.compile(r"youtube\.com|youtu\.be", re.I)),
    ("linkedin", re.compile(r"linkedin\.com", re.I)),
    ("kwai", re.compile(r"kwai\.com", re.I)),
)


def detect_network(url: str | None) -> tuple[str | None, str | None]:
    """Devolve (rede, handle) a partir da URL declarada ao TSE."""
    u = clean(url)
    if u is None:
        return None, None
    for network, pattern in SOCIAL_PATTERNS:
        if pattern.search(u):
            handle = u.rstrip("/").split("/")[-1].split("?")[0] or None
            return network, handle
    return "site", None
