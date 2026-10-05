"""Download com hash. Sem dependência de banco — é a parte testável sozinha.

Regra da casa: não guardamos o arquivo bruto. Guardamos URL + data + SHA-256.
Quem quiser conferir re-baixa e compara o hash. Isso é o que torna a base
reconstruível por terceiros sem precisar confiar em nós.
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx

CHUNK = 1024 * 1024
DEFAULT_TIMEOUT = httpx.Timeout(60.0, read=600.0)

# Várias fontes públicas têm filtro anti-bot que devolve 403 para user-agent
# de biblioteca. Identificar-se honestamente costuma bastar.
USER_AGENT = os.environ.get(
    "MIRANTE_USER_AGENT",
    "mirante/0.1 (projeto de transparência de dados públicos; contato via repositório)",
)


@dataclass
class FetchedFile:
    """Um arquivo baixado, com tudo que precisamos para provar a origem."""

    url: str
    path: Path
    sha256: str
    byte_size: int
    accessed_at: datetime
    http_status: int
    media_type: str | None = None
    from_cache: bool = False
    inner_hashes: dict[str, str] = field(default_factory=dict)

    def manifest_entry(self) -> dict:
        return {
            "url": self.url,
            "accessed_at": self.accessed_at.isoformat(),
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "http_status": self.http_status,
            "media_type": self.media_type,
            "inner_files": self.inner_hashes,
        }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(
    url: str,
    dest_dir: Path,
    filename: str | None = None,
    reuse_existing: bool = True,
    hash_inner: bool = True,
) -> FetchedFile:
    """Baixa `url` para `dest_dir` e devolve os metadados de proveniência.

    Se o arquivo já existe e `reuse_existing`, não re-baixa — mas ainda
    calcula o hash, porque é o hash que entra no manifesto. Isso também é a
    saída manual quando a fonte devolve HTTP 403: baixe no navegador, coloque
    o arquivo em dest_dir com o nome esperado e rode o crawler de novo.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = filename or url.rstrip("/").split("/")[-1] or "download.bin"
    path = dest_dir / name

    if reuse_existing and path.exists() and path.stat().st_size > 0:
        digest = sha256_file(path)
        fetched = FetchedFile(
            url=url,
            path=path,
            sha256=digest,
            byte_size=path.stat().st_size,
            accessed_at=datetime.now(timezone.utc),
            http_status=200,
            from_cache=True,
        )
    else:
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        with httpx.stream(
            "GET", url, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True
        ) as response:
            response.raise_for_status()
            tmp = path.with_suffix(path.suffix + ".part")
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(CHUNK):
                    fh.write(chunk)
            tmp.replace(path)
            media_type = response.headers.get("content-type")
            status = response.status_code

        fetched = FetchedFile(
            url=url,
            path=path,
            sha256=sha256_file(path),
            byte_size=path.stat().st_size,
            accessed_at=datetime.now(timezone.utc),
            http_status=status,
            media_type=media_type,
        )

    if hash_inner and zipfile.is_zipfile(path):
        fetched.inner_hashes = hash_zip_members(path)

    return fetched


def hash_zip_members(path: Path) -> dict[str, str]:
    """SHA-256 de cada arquivo DENTRO do zip.

    O hash do zip sozinho não basta: o TSE reempacota, e dois zips com
    conteúdo idêntico podem ter hashes diferentes. O hash do CSV interno é
    o que de fato identifica o dado.
    """
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            h = hashlib.sha256()
            with zf.open(info) as member:
                for chunk in iter(lambda: member.read(CHUNK), b""):
                    h.update(chunk)
            hashes[info.filename] = h.hexdigest()
    return hashes


def iter_zip_text(
    path: Path,
    pattern: str | None = None,
    encoding: str = "latin-1",
) -> Iterator[tuple[str, io.TextIOWrapper]]:
    """Itera os membros de texto do zip sem descompactar em disco.

    Os CSVs do TSE vêm em latin-1 com separador ';' — não é UTF-8.
    """
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if pattern and pattern.lower() not in info.filename.lower():
                continue
            with zf.open(info) as raw:
                yield info.filename, io.TextIOWrapper(raw, encoding=encoding, newline="")
