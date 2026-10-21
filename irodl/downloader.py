"""Motor de descarga .irodl.

Mismo diseño que el motor invertido del protocolo original:
  · descarga paralela de chunks (ThreadPool, 4–6 conexiones)
  · MD5 por chunk verificado antes de aceptarlo
  · reintentos con reanudación por `Range: bytes=n-`
  · archivos `.part` → renombrados cuando el MD5 cuadra → resume sin re-descargar
  · reensamblado en streaming + verificación del MD5 global
  · progreso y cancelación cooperativa (threading.Event)

Sin backend: cada token se descifra localmente y la descarga va directa a
`{host}{ruta}` (host por defecto https://s3.todus.cu/stream/).
"""
from __future__ import annotations

import hashlib
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import requests

from .crypto import resolve_url
from .format import Manifest

BLOCK = 256 * 1024  # 256 KiB por lectura de red
ProgressCB = Callable[[int, int, int, int, float, str], None]
# progreso(bytes_hechos, bytes_total, chunks_hechos, chunks_total, velocidad_bps, estado)


class IrodlError(RuntimeError):
    pass


class IrodlCancelled(IrodlError):
    pass


class ChunkGone(IrodlError):
    """El servidor no tiene el chunk (purga o ruta inválida)."""


class HashMismatch(IrodlError):
    pass


class IrodlDownloader:
    def __init__(
        self,
        manifest: Manifest,
        dest_dir: str | Path,
        *,
        host: Optional[str] = None,
        concurrency: int = 4,
        retries: int = 3,
        timeout: tuple = (15, 90),
        progress: Optional[ProgressCB] = None,
        cancel: Optional[threading.Event] = None,
        user_agent: str = "IroDL/1.0",
    ) -> None:
        self.m = manifest
        self.dest = Path(dest_dir)
        self.host = host or manifest.host
        self.concurrency = max(1, min(8, concurrency))
        self.retries = retries
        self.timeout = timeout
        self.progress_cb = progress
        self.cancel = cancel or threading.Event()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._lock = threading.Lock()
        self._done_bytes = 0
        self._speed = 0.0
        self._t_last = 0.0
        self._status = "iniciando"
        self._hits = 0  # contador de peticiones HTTP (tests)

    # ------------------------------------------------------------------
    def _parts_dir(self) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in self.m.filename)
        d = self.dest / f"irodl_parts_{safe[:60]}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _report(self, done_chunks: int, force: bool = False) -> None:
        if not self.progress_cb:
            return
        now = time.monotonic()
        with self._lock:
            dt = now - self._t_last
            if not force and dt < 0.4:
                return
            if dt > 0:
                inst = self._instant / dt if hasattr(self, "_instant") else 0.0
                self._speed = 0.7 * self._speed + 0.3 * inst if self._speed else inst
            self._t_last = now
            self._instant = 0.0
            cb_state = (self._done_bytes, self.m.filesize, done_chunks,
                        self.m.n_chunks, self._speed, self._status)
        try:
            self.progress_cb(*cb_state)
        except Exception:
            pass

    def _add_bytes(self, n: int) -> None:
        with self._lock:
            self._done_bytes += n
            self._instant = getattr(self, "_instant", 0.0) + n

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise IrodlCancelled("descarga cancelada por el usuario")

    # ------------------------------------------------------------------
    def _chunk_path(self, parts: Path, name: str, done: bool) -> Path:
        return parts / (f"{name}.part" if not done else name)

    def _download_chunk(self, chunk) -> int:
        """Descarga un chunk con reintentos+Range. Devuelve bytes del chunk."""
        parts = self._parts_dir()
        final = self._chunk_path(parts, chunk.name, done=True)
        if final.exists() and hashlib.md5(final.read_bytes()).hexdigest() == chunk.md5:
            with self._lock:
                self._done_bytes += final.stat().st_size
            self._report(0, force=True)
            return final.stat().st_size  # ya estaba (resume)

        partial = self._chunk_path(parts, chunk.name, done=False)
        url = resolve_url(chunk.token, host=self.host)

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            self._check_cancel()
            try:
                self._fetch_once(partial, url, chunk)
                got = hashlib.md5(partial.read_bytes()).hexdigest()
                if got != chunk.md5:
                    raise HashMismatch(
                        f"chunk {chunk.name}: md5 {got} != {chunk.md5}")
                partial.replace(final)
                return final.stat().st_size
            except IrodlCancelled:
                raise
            except ChunkGone:
                raise  # 404: el objeto no existe; reintentar no ayuda
            except Exception as exc:  # red, md5, http
                last_exc = exc
                time.sleep(min(1.2 * attempt, 4.0))
        # agotado
        if isinstance(last_exc, (ChunkGone,)):
            raise last_exc
        raise IrodlError(f"chunk {chunk.name} falló tras {self.retries} intentos: {last_exc}")

    def _fetch_once(self, partial: Path, url: str, chunk) -> None:
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {}
        mode = "ab" if offset else "wb"
        if offset:
            headers["Range"] = f"bytes={offset}-"
        with self._lock:
            self._hits += 1
        try:
            resp = self.session.get(url, headers=headers, stream=True, timeout=self.timeout)
        except requests.RequestException as exc:
            raise IrodlError(f"red: {exc}") from exc
        if resp.status_code == 404:
            raise ChunkGone(f"chunk no disponible (404): {chunk.name}")
        if resp.status_code == 416:  # .part ya completo (raro)
            resp.close()
            return
        if offset and resp.status_code == 200:
            # servidor no soportó Range: reiniciar chunk
            offset, mode = 0, "wb"
        if resp.status_code not in (200, 206):
            resp.close()
            raise IrodlError(f"HTTP {resp.status_code} en {chunk.name}")

        self._status = f"descargando {chunk.name}"
        tmp_append = partial.with_suffix(".part.new")
        fh = open(partial, "ab") if mode == "ab" else open(tmp_append, "wb")
        try:
            for data in resp.iter_content(BLOCK):
                self._check_cancel()
                if not data:
                    continue
                fh.write(data)
                self._add_bytes(len(data))
                self._report(0)
        finally:
            fh.close()
        if mode == "wb":
            tmp_append.replace(partial)
        resp.close()

    # ------------------------------------------------------------------
    def run(self) -> Path:
        """Ejecuta la descarga completa. Devuelve la ruta del archivo final."""
        self.dest.mkdir(parents=True, exist_ok=True)
        parts = self._parts_dir()
        final_path = self.dest / self.m.filename

        self._status = "descifrando tokens"
        urls_ok = 0
        for c in self.m.chunks:  # valida todos los tokens antes de empezar
            resolve_url(c.token, host=self.host)
            urls_ok += 1
        self._report(0, force=True)

        self._status = "descargando"
        done_chunks = 0
        errors: list[tuple[str, Exception]] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futs = {pool.submit(self._download_chunk, c): c for c in self.m.chunks}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    fut.result()
                    done_chunks += 1
                    self._status = f"chunks {done_chunks}/{self.m.n_chunks}"
                    self._report(done_chunks, force=True)
                except IrodlCancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception as exc:
                    errors.append((c.name, exc))

        if errors:
            gone = [n for n, e in errors if isinstance(e, ChunkGone)]
            if gone:
                raise ChunkGone(f"chunks vencidos/no disponibles: {', '.join(gone[:5])}")
            raise IrodlError("; ".join(f"{n}: {e}" for n, e in errors[:5]))

        self._status = "reensamblando"
        self._report(done_chunks, force=True)
        out_tmp = final_path.with_suffix(final_path.suffix + ".rebuilding")
        h = hashlib.md5()
        size = 0
        with open(out_tmp, "wb") as out:
            for c in self.m.chunks:
                chunk_file = self._chunk_path(parts, c.name, done=True)
                if not chunk_file.exists():
                    raise IrodlError(f"falta el chunk aceptado {c.name} (¿borrado?)")
                with open(chunk_file, "rb") as fh:
                    while True:
                        b = fh.read(BLOCK)
                        if not b:
                            break
                        h.update(b)
                        out.write(b)
                        size += len(b)
        if size != self.m.filesize:
            raise HashMismatch(f"tamaño final {size} != manifiesto {self.m.filesize}")
        if h.hexdigest() != self.m.md5:
            out_tmp.unlink(missing_ok=True)
            raise HashMismatch(
                f"MD5 global {h.hexdigest()} != {self.m.md5} (manifiesto alterado o chunks mal generados)")
        out_tmp.replace(final_path)

        self._status = "limpiando"
        shutil.rmtree(parts, ignore_errors=True)
        self._status = "completado"
        self._report(self.m.n_chunks, force=True)
        return final_path
