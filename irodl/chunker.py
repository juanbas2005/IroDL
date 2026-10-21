"""Empaquetado: archivo local → chunks de 2 MiB + manifiesto .irodl.

El mismo sistema del protocolo original: divide en 2 MiB, calcula MD5 por
chunk y global, y produce tokens cifrados con la clave IroDL. El `uploader`
es un callback inyectable (el bot pasa su cliente S3; los tests pasan una
simulación que escribe a disco).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import CHUNK_SIZE
from .crypto import encrypt_path
from .format import Manifest, Chunk

#: uploader(data, remote_name) -> ruta_relativa_guardada
Uploader = Callable[[bytes, str], str]


def chunk_name(filename: str, index: int) -> str:
    digits = 4 if index >= 1000 else 3
    return f"{filename}.{index:0{digits}d}"


def iter_file_chunks(path: str | Path, chunk_size: int = CHUNK_SIZE):
    """Yield (index, data, md5_hex) sin cargar el archivo completo en RAM."""
    h_all = hashlib.md5()
    total = 0
    idx = 0
    with open(path, "rb") as fh:
        while True:
            data = fh.read(chunk_size)
            if not data:
                break
            h_all.update(data)
            total += len(data)
            yield idx, data, hashlib.md5(data).hexdigest()
            idx += 1
    if total == 0:
        yield 0, b"", hashlib.md5(b"").hexdigest()
        h_all.update(b"")
    return  # filesize y md5 global se exponen vía pack_file


def pack_file(
    path: str | Path,
    uploader: Uploader,
    *,
    chunk_size: int = CHUNK_SIZE,
    host: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Manifest:
    """Divide `path`, sube cada chunk con `uploader` y devuelve el Manifest.

    `uploader(data, remote_name)` debe guardar el chunk y devolver la ruta
    relativa bajo la que quedó (será lo que se cifre en el token).
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    filename = p.name

    chunks: List[Chunk] = []
    h_all = hashlib.md5()
    total = 0
    idx = 0
    with open(p, "rb") as fh:
        while True:
            data = fh.read(chunk_size)
            if not data:
                break
            h_all.update(data)
            total += len(data)
            cmd5 = hashlib.md5(data).hexdigest()
            rel = uploader(data, chunk_name(filename, idx))
            tok = encrypt_path(rel)
            chunks.append(Chunk(name=chunk_name(filename, idx),
                                token=tok, md5=cmd5, url=f"irodl://{tok}"))
            if progress:
                progress(idx + 1, total)
            idx += 1
    if total == 0:  # archivo vacío: un chunk vacío, igual que el original haría
        cmd5 = hashlib.md5(b"").hexdigest()
        h_all.update(b"")
        rel = uploader(b"", chunk_name(filename, 0))
        tok = encrypt_path(rel)
        chunks.append(Chunk(name=chunk_name(filename, 0), token=tok,
                            md5=cmd5, url=f"irodl://{tok}"))
        total = 0

    return Manifest(
        filename=filename,
        filesize=total,
        md5=h_all.hexdigest(),
        chunks=chunks,
        chunk_size=chunk_size,
        host=host or Manifest.host,
    )


def local_pack(path: str | Path, out_dir: str | Path, *, chunk_size: int = CHUNK_SIZE,
               prefix: str = "pk/dd/local") -> Tuple[Manifest, Path]:
    """Empaqueta a disco (simulación de S3): chunks en out_dir/<prefix>/.

    Devuelve (manifiesto, ruta_del_.irodl). Útil para pruebas y para ver el
    formato sin servidor.
    """
    out = Path(out_dir)
    target_dir = out / prefix
    target_dir.mkdir(parents=True, exist_ok=True)

    def up(data: bytes, remote_name: str) -> str:
        (target_dir / remote_name).write_bytes(data)
        return f"{prefix}/{remote_name}"

    m = pack_file(path, up, chunk_size=chunk_size)
    out_irodl = out / (m.filename + ".irodl")
    out_irodl.write_text(m.to_json(), encoding="utf-8")
    return m, out_irodl
