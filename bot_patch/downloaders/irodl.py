"""IroDL para el bot S3 — módulo autocontenido (drop-in).

EXTRA del bot: no sustituye nada del modo normal. Da dos capacidades:

1) IMPORTAR un paquete .irodl recibido por Telegram:
       doc = parse_irodl_document(raw_bytes)
       dl = IrodlDownloader(doc, dest_dir, progress=cb, cancel=cancel_event)
       final_path = await asyncio.get_running_loop().run_in_executor(None, dl.run)
   → devuelve la ruta del archivo reconstruido (MD5 verificado por chunk y global).

2) EXPORTAR un archivo local como .irodl:
       m, out_path = export_irodl(local_path, upload_fn, prefix, progress=cb)
   donde upload_fn(data: bytes, remote_name: str) -> str sube el chunk al S3
   propio y devuelve la ruta relativa bajo la que quedó guardado.
   Los chunks son de 2 MiB, igual que el sistema original.

Sin backend: los tokens se descifran localmente (AES-256-CBC, clave propia IroDL)
y la descarga va directa a https://s3.todus.cu/stream/<ruta>.
Formato: docs/FORMATO_IRODL.md del repo IroDL.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from Crypto.Cipher import AES

# --------------------------------------------------------------------------
# Clave y constantes del formato (propias de IroDL — NO son las de .udyat)
# --------------------------------------------------------------------------

IRODL_SEED = "eb4c353716dbf7e763149b2fa90981bb32614eceaba1ef930ebba3cf577ad9bf"
IRODL_KEY = hashlib.sha256(IRODL_SEED.encode("ascii")).digest()

DEFAULT_HOST = "https://s3.todus.cu/stream/"
CHUNK_SIZE = 2 * 1024 * 1024  # 2 MiB — idéntico al sistema original
MAX_CHUNKS_PER_EXPORT = 500   # cota defensiva: 500 × 2 MiB ≈ 1 GB por paquete

MD5_RE = re.compile(r"^[0-9a-f]{32}$")


class IrodlError(RuntimeError):
    pass


class IrodlCancelled(IrodlError):
    pass


class IrodlChunkGone(IrodlError):
    pass


# --------------------------------------------------------------------------
# Cripto de tokens:  token = b64url( IV[16] ‖ AES-256-CBC( PKCS7( ruta ) ) )
# --------------------------------------------------------------------------

def _pkcs7_pad(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise IrodlError("ciphertext inválido")
    n = data[-1]
    if not 1 <= n <= 16 or data[-n:] != bytes([n]) * n:
        raise IrodlError("padding PKCS7 inválido (¿clave incorrecta?)")
    return data[:-n]


def encrypt_path(rel_path: str) -> str:
    if not rel_path or rel_path.startswith("/"):
        raise ValueError("ruta relativa requerida")
    iv = os.urandom(16)
    ct = AES.new(IRODL_KEY, AES.MODE_CBC, iv).encrypt(_pkcs7_pad(rel_path.encode("utf-8")))
    return base64.urlsafe_b64encode(iv + ct).decode("ascii").rstrip("=")


def decrypt_token(token_or_url: str) -> str:
    tok = token_or_url.strip()
    if tok.startswith("irodl://"):
        tok = tok[len("irodl://"):]
    elif tok.startswith(("http://", "https://")):
        tok = tok.rsplit("/d/", 1)[-1].split("?")[0]
    try:
        raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
    except Exception as exc:
        raise IrodlError(f"token b64url inválido: {exc}") from exc
    if len(raw) < 32 or len(raw) % 16:
        raise IrodlError("token de tamaño inválido")
    iv, ct = raw[:16], raw[16:]
    return _pkcs7_unpad(AES.new(IRODL_KEY, AES.MODE_CBC, iv).decrypt(ct)).decode("utf-8")


def resolve_url(token_or_url: str, host: Optional[str] = None) -> str:
    path = decrypt_token(token_or_url)
    base = host or DEFAULT_HOST
    if not base.endswith("/"):
        base += "/"
    return base + quote(path)


# --------------------------------------------------------------------------
# Manifiesto .irodl
# --------------------------------------------------------------------------

def parse_irodl_document(raw: bytes | str) -> Dict[str, Any]:
    """Valida el JSON de un .irodl y lo normaliza. Lanza IrodlError."""
    try:
        obj = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    except Exception as exc:
        raise IrodlError(f"no es un JSON válido: {exc}") from exc
    if not isinstance(obj, dict):
        raise IrodlError("el paquete no es un objeto JSON")

    filename = obj.get("filename")
    if not isinstance(filename, str) or not filename.strip() or "/" in filename or "\\" in filename:
        raise IrodlError("filename inválido")
    filesize = obj.get("filesize")
    if not isinstance(filesize, int) or filesize < 0:
        raise IrodlError("filesize inválido")
    md5 = (obj.get("md5") or "").lower()
    if not MD5_RE.match(md5):
        raise IrodlError("md5 global inválido")
    chunks = obj.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise IrodlError("paquete sin chunks")
    for i, c in enumerate(chunks):
        if not isinstance(c, dict):
            raise IrodlError(f"chunk {i} inválido")
        if not MD5_RE.match((c.get("md5") or "").lower()):
            raise IrodlError(f"chunk {i}: md5 inválido")
        url = c.get("url") or ""
        if "://" not in url:
            raise IrodlError(f"chunk {i}: url sin token")
    idx = [int(c["name"].rsplit(".", 1)[1]) for c in chunks]
    if idx != list(range(len(chunks))):
        raise IrodlError("índices de chunks no correlativos")

    obj["md5"] = md5
    for c in chunks:
        c["md5"] = c["md5"].lower()
    obj["_n_expected"] = (filesize + CHUNK_SIZE - 1) // CHUNK_SIZE or 1
    if len(chunks) != obj["_n_expected"]:
        raise IrodlError(
            f"incoherencia: filesize implica {obj['_n_expected']} chunks, hay {len(chunks)}")
    return obj


def is_irodl_document(raw: bytes) -> bool:
    """Reconocimiento barato: JSON con 'chunks' y tokens irodl://."""
    head = raw[:4096]
    return b'"chunks"' in head and (b"irodl://" in head or b'"filename"' in head)


# --------------------------------------------------------------------------
# EXPORTAR: archivo local → chunks 2 MiB → uploader → manifiesto .irodl
# --------------------------------------------------------------------------

def _chunk_name(filename: str, index: int) -> str:
    return f"{filename}.{index:04d}" if index >= 1000 else f"{filename}.{index:03d}"


def export_irodl(
    local_path: str | Path,
    upload_fn: Callable[[bytes, str], str],
    prefix: str,
    *,
    host: Optional[str] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> Tuple[Dict[str, Any], Path]:
    """Divide `local_path` en chunks de 2 MiB, los sube con `upload_fn` y
    devuelve (manifiesto, ruta_del_archivo_.irodl_temporal).

    upload_fn(data, remote_name) -> ruta relativa guardada en el S3.
    progress(done_chunks, total_chunks, estado) para mostrar avance.
    """
    p = Path(local_path)
    if not p.is_file() or p.stat().st_size == 0:
        raise IrodlError("archivo vacío o inexistente")
    size = p.stat().st_size
    n_expected = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
    if n_expected > MAX_CHUNKS_PER_EXPORT:
        raise IrodlError(
            f"archivo demasiado grande para .irodl ({n_expected} chunks > {MAX_CHUNKS_PER_EXPORT})")

    chunks: List[Dict[str, str]] = []
    h_all = hashlib.md5()
    done = 0
    with open(p, "rb") as fh:
        for i in range(n_expected):
            if cancel is not None and cancel.is_set():
                raise IrodlCancelled("exportación cancelada")
            data = fh.read(CHUNK_SIZE)
            if not data:
                break
            h_all.update(data)
            name = _chunk_name(p.name, i)
            cmd5 = hashlib.md5(data).hexdigest()
            rel = upload_fn(data, name)          # sube el chunk al S3 propio
            tok = encrypt_path(rel)
            chunks.append({"name": name, "url": f"irodl://{tok}", "md5": cmd5})
            done += 1
            if progress:
                progress(done, n_expected, f"subiendo {name}")

    manifest = {
        "app": "IroDL",
        "format": "irodl",
        "version": 1,
        "filename": p.name,
        "filesize": size,
        "md5": h_all.hexdigest(),
        "chunk_size": CHUNK_SIZE,
        "chunks": chunks,
    }
    if host:
        manifest["host"] = host
    out = Path(tempfile.gettempdir()) / f"{p.name}.irodl"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest, out


# --------------------------------------------------------------------------
# IMPORTAR: descargar chunks paralelos con MD5 y reensamblar
# --------------------------------------------------------------------------

ProgressCB = Callable[[int, int, int, int, float, str], None]


class IrodlDownloader:
    """Motor de descarga .irodl (hilos, MD5 por chunk, resume por Range)."""

    def __init__(
        self,
        manifest: Dict[str, Any],
        dest_dir: str | Path,
        *,
        concurrency: int = 4,
        retries: int = 3,
        progress: Optional[ProgressCB] = None,
        cancel: Optional[threading.Event] = None,
        timeout: Tuple[int, int] = (15, 90),
        host: Optional[str] = None,
    ) -> None:
        self.m = manifest
        self.dest = Path(dest_dir)
        self.host = host or manifest.get("host") or DEFAULT_HOST
        self.concurrency = max(1, min(8, concurrency))
        self.retries = retries
        self.timeout = timeout
        self.progress_cb = progress
        self.cancel = cancel or threading.Event()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "IroDL/1.0"})
        self._lock = threading.Lock()
        self._done_bytes = 0
        self._speed = 0.0
        self._t_last = 0.0
        self._instant = 0.0
        self._status = "iniciando"

    # -- progreso ------------------------------------------------------
    def _report(self, done_chunks: int, force: bool = False) -> None:
        if not self.progress_cb:
            return
        now = time.monotonic()
        with self._lock:
            dt = now - self._t_last
            if not force and dt < 0.4:
                return
            if dt > 0:
                inst = self._instant / dt
                self._speed = 0.7 * self._speed + 0.3 * inst if self._speed else inst
            self._t_last = now
            self._instant = 0.0
            state = (self._done_bytes, int(self.m.get("filesize", 0)), done_chunks,
                     len(self.m["chunks"]), self._speed, self._status)
        try:
            self.progress_cb(*state)
        except Exception:
            pass

    def _add_bytes(self, n: int) -> None:
        with self._lock:
            self._done_bytes += n
            self._instant += n

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise IrodlCancelled("cancelado por el usuario")

    # -- chunks --------------------------------------------------------
    def _parts_dir(self) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in self.m["filename"])[:60]
        d = self.dest / f"irodl_parts_{safe}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _download_chunk(self, chunk: Dict[str, Any]) -> int:
        parts = self._parts_dir()
        final = parts / chunk["name"]
        if final.exists():
            h = hashlib.md5()
            with open(final, "rb") as fh:
                for b in iter(lambda: fh.read(1 << 20), b""):
                    h.update(b)
            if h.hexdigest() == chunk["md5"]:
                with self._lock:
                    self._done_bytes += final.stat().st_size
                self._report(0, force=True)
                return final.stat().st_size

        partial = parts / f"{chunk['name']}.part"
        url = resolve_url(chunk["url"], host=self.host)
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            self._check_cancel()
            try:
                self._fetch_once(partial, url, chunk)
                h = hashlib.md5(partial.read_bytes()).hexdigest()
                if h != chunk["md5"]:
                    raise IrodlError(f"md5 del chunk {h} != {chunk['md5']}")
                partial.replace(final)
                return final.stat().st_size
            except IrodlCancelled:
                raise
            except IrodlChunkGone:
                raise
            except Exception as exc:
                last_exc = exc
                time.sleep(min(1.2 * attempt, 4.0))
        raise IrodlError(f"chunk {chunk['name']} falló: {last_exc}")

    def _fetch_once(self, partial: Path, url: str, chunk: Dict[str, Any]) -> None:
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            resp = self.session.get(url, headers=headers, stream=True, timeout=self.timeout)
        except requests.RequestException as exc:
            raise IrodlError(f"red: {exc}") from exc
        if resp.status_code == 404:
            raise IrodlChunkGone(f"chunk vencido (404): {chunk['name']}")
        if resp.status_code == 416:
            resp.close()
            return
        if offset and resp.status_code == 200:
            offset = 0  # sin soporte Range: desde cero
        if resp.status_code not in (200, 206):
            resp.close()
            raise IrodlError(f"HTTP {resp.status_code} en {chunk['name']}")

        self._status = f"descargando {chunk['name']}"
        tmp = partial.with_suffix(".part.new")
        mode = "ab" if offset else "wb"
        target = partial if mode == "ab" else tmp
        try:
            with open(target, mode) as fh:
                for data in resp.iter_content(256 * 1024):
                    self._check_cancel()
                    if data:
                        fh.write(data)
                        self._add_bytes(len(data))
                        self._report(0)
        finally:
            resp.close()
        if mode == "wb":
            tmp.replace(partial)

    # -- flujo completo --------------------------------------------------
    def run(self) -> Path:
        self.dest.mkdir(parents=True, exist_ok=True)
        parts = self._parts_dir()
        final_path = self.dest / self.m["filename"]

        self._status = "descifrando tokens"
        for c in self.m["chunks"]:
            resolve_url(c["url"], host=self.host)
        self._report(0, force=True)

        self._status = "descargando"
        done = 0
        errors: List[Tuple[str, Exception]] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futs = {pool.submit(self._download_chunk, c): c for c in self.m["chunks"]}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    fut.result()
                    done += 1
                    self._status = f"chunks {done}/{len(self.m['chunks'])}"
                    self._report(done, force=True)
                except IrodlCancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception as exc:
                    errors.append((c["name"], exc))
        if errors:
            gone = [n for n, e in errors if isinstance(e, IrodlChunkGone)]
            if gone:
                raise IrodlChunkGone(f"chunks vencidos: {', '.join(gone[:5])}")
            raise IrodlError("; ".join(f"{n}: {e}" for n, e in errors[:5]))

        self._status = "reensamblando"
        self._report(done, force=True)
        out_tmp = final_path.with_suffix(final_path.suffix + ".rebuilding")
        h = hashlib.md5()
        size = 0
        with open(out_tmp, "wb") as out:
            for c in self.m["chunks"]:
                cf = parts / c["name"]
                with open(cf, "rb") as fh:
                    while True:
                        b = fh.read(1 << 20)
                        if not b:
                            break
                        h.update(b)
                        out.write(b)
                        size += len(b)
        if size != self.m["filesize"]:
            out_tmp.unlink(missing_ok=True)
            raise IrodlError(f"tamaño final {size} != manifiesto {self.m['filesize']}")
        if h.hexdigest() != self.m["md5"]:
            out_tmp.unlink(missing_ok=True)
            raise IrodlError("MD5 global no coincide con el manifiesto")
        out_tmp.replace(final_path)

        shutil.rmtree(parts, ignore_errors=True)
        self._status = "completado"
        self._report(len(self.m["chunks"]), force=True)
        return final_path
