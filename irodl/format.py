"""Modelo y validación del manifiesto .irodl."""
from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from . import DEFAULT_HOST, CHUNK_SIZE

MD5_RE = re.compile(r"^[0-9a-f]{32}$")
NAME_RE = re.compile(r"^.+\.\d{3,5}$")


class ManifestError(ValueError):
    """Manifiesto inválido o incoherente."""


@dataclass
class Chunk:
    name: str
    token: str          # contenido de url (sin esquema)
    md5: str
    url: str            # url original del manifiesto

    @property
    def index(self) -> int:
        return int(self.name.rsplit(".", 1)[1])


@dataclass
class Manifest:
    filename: str
    filesize: int
    md5: str
    chunks: List[Chunk] = field(default_factory=list)
    chunk_size: int = CHUNK_SIZE
    host: str = DEFAULT_HOST
    app: str = "IroDL"
    format: str = "irodl"
    version: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)

    # --- helpers -------------------------------------------------
    @property
    def n_chunks(self) -> int:
        return len(self.chunks)

    def expected_chunk_count(self) -> int:
        full, rem = divmod(self.filesize, self.chunk_size)
        return full + (1 if rem else 0) if self.filesize else 0

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "app": self.app,
            "format": self.format,
            "version": self.version,
            "filename": self.filename,
            "filesize": self.filesize,
            "md5": self.md5,
            "chunk_size": self.chunk_size,
            "chunks": [
                {"name": c.name, "url": f"irodl://{c.token}", "md5": c.md5}
                for c in self.chunks
            ],
        }
        if self.host and self.host != DEFAULT_HOST:
            d["host"] = self.host
        d.update(self.extra)
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def parse_manifest(obj: Any) -> Manifest:
    """Valida y normaliza el dict JSON de un .irodl. Lanza ManifestError."""
    if not isinstance(obj, dict):
        raise ManifestError("el manifiesto debe ser un objeto JSON")

    filename = obj.get("filename")
    if not isinstance(filename, str) or not filename.strip() or "/" in filename or "\\" in filename:
        raise ManifestError("filename inválido (vacío o con ruta)")

    filesize = obj.get("filesize")
    if not isinstance(filesize, int) or isinstance(filesize, bool) or filesize < 0:
        raise ManifestError("filesize inválido")

    md5 = obj.get("md5")
    if not isinstance(md5, str) or not MD5_RE.match(md5.lower()):
        raise ManifestError("md5 global inválido")

    raw_chunks = obj.get("chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        raise ManifestError("chunks vacío o ausente")

    host = obj.get("host") or DEFAULT_HOST
    chunk_size = obj.get("chunk_size") or CHUNK_SIZE
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ManifestError("chunk_size inválido")

    chunks: List[Chunk] = []
    seen_names = set()
    for i, rc in enumerate(raw_chunks):
        if not isinstance(rc, dict):
            raise ManifestError(f"chunk {i}: no es objeto")
        name = rc.get("name")
        url = rc.get("url") or ""
        cmd5 = (rc.get("md5") or "").lower()
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ManifestError(f"chunk {i}: name inválido ({name!r})")
        if name in seen_names:
            raise ManifestError(f"chunk {i}: name duplicado ({name})")
        seen_names.add(name)
        token = url.split("://", 1)[1] if "://" in url else url
        if not token:
            raise ManifestError(f"chunk {i}: url sin token")
        if not MD5_RE.match(cmd5):
            raise ManifestError(f"chunk {i}: md5 inválido")
        chunks.append(Chunk(name=name, token=token, md5=cmd5, url=url))

    m = Manifest(
        filename=filename,
        filesize=filesize,
        md5=md5.lower(),
        chunks=chunks,
        chunk_size=chunk_size,
        host=host,
        app=obj.get("app", "IroDL"),
        format=obj.get("format", "irodl"),
        version=obj.get("version", 1),
        extra={k: v for k, v in obj.items()
               if k not in {"app", "format", "version", "filename", "filesize",
                            "md5", "chunk_size", "chunks", "host"}},
    )

    # coherencia: suma de tamaños no verificable sin descargar, pero el conteo sí
    expected = m.expected_chunk_count()
    if expected and len(chunks) != expected:
        raise ManifestError(
            f"incoherencia: filesize implica {expected} chunks, el manifiesto trae {len(chunks)}"
        )
    idx = [c.index for c in chunks]
    if idx != list(range(len(chunks))):
        raise ManifestError("índices de chunks no correlativos (0..N-1)")
    return m


def load_manifest(path: str | Path) -> Manifest:
    p = Path(path)
    try:
        obj = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"JSON inválido en {p.name}: {exc}") from exc
    return parse_manifest(obj)


def file_md5(path: str | Path, block: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
