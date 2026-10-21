# Integración del extra `.irodl` en el bot S3 (`juanbas2005/S3`)

> **Principio**: el modo normal del bot queda **intacto**. IroDL es un **extra**:
> dos capacidades nuevas que se enganchan a los mismos ganchos que ya usa el
> soporte `.udyat` (Task 9): `_process_link` para importar, y el flujo de subida
> para exportar.

## 1. Instalar el módulo

```bash
# en el clon del repo del bot, rama arena/6d143e90-s3
cp bot_patch/downloaders/irodl.py  bot/downloaders/irodl.py
```

El módulo es **autocontenido** (solo depende de `requests` y `pycryptodome`,
ya presentes en el bot) y no importa nada de `downloaders/udyat.py`.

## 2. IMPORTAR `.irodl` (recibir paquetes por Telegram)

Mismo patrón que `_peek_udyat_document` / `_handle_udyat_manifest_doc` de
`bot.py`. En `bot.py`, junto a la clasificación de documentos:

```python
from bot.downloaders.irodl import (IrodlDownloader, IrodlCancelled,
                                   IrodlError, is_irodl_document,
                                   parse_irodl_document)

# dentro de _peek_udyat_document (o del clasificador equivalente):
if (doc.file_name or "").lower().endswith(".irodl") or is_irodl_document(raw[:4096]):
    return "irodl_manifest"

# handler espejo del de .udyat:
async def _handle_irodl_manifest_doc(self, client, message):
    raw = await message.download(in_memory=True)
    try:
        manifest = parse_irodl_document(raw)
    except IrodlError as e:
        return await message.reply(f"❌ Paquete .irodl inválido: {e}")
    name = manifest["filename"]
    size = manifest["filesize"]
    n = len(manifest["chunks"])
    # entra por el pipeline estándar (cuota → subida S3 → enlace / modo manual)
    return await self._process_link(
        message,
        f"irodl://{name}",
        meta={"kind": "irodl", "manifest": manifest, "filename": name,
              "filesize": size, "chunks": n},
    )
```

Y en `dispatcher.py`, plataforma espejo de `"udyat"` (sin fallback al genérico):

```python
Platform(
    name="irodl",
    match=lambda url: url.startswith("irodl://"),
    handler=handle_irodl,          # consume meta.manifest del pipeline
    fallback=None,
)
```

Descarga (dentro del handler, tras pasar cuota — igual que el bloque `.udyat`):

```python
cancel = asyncio.Event()
loop = asyncio.get_running_loop()
dl = IrodlDownloader(
    meta["manifest"],
    work_dir,                       # mismo dir de trabajo que usa .udyat
    progress=cb_progreso,           # misma callback que usa .udyat
    cancel=threading_event_de_cancel,
)
final = await loop.run_in_executor(None, dl.run)
# → final es la ruta del archivo reconstruido; sigue el flujo normal:
#   subir al S3 propio → enlace /enviar en modo manual (con el límite de
#   2 GiB de Telegram ya gestionado por el bot para el modo normal).
```

## 3. EXPORTAR `.irodl` (generar paquetes con el bot)

El bot ya descarga el archivo a disco cuando lo procesa; el punto de enganche
es **justo después de tener el archivo local** (misma rutina que la subida
normal). Añadir un botón/comando `Extra → Generar .irodl`:

```python
from bot.downloaders.irodl import export_irodl, IrodlError

def upload_chunk(data: bytes, remote_name: str) -> str:
    # adaptar al cliente de subida que ya usa el bot para el S3 propio:
    tmp = work_dir / remote_name
    tmp.write_bytes(data)                    # 2 MiB, coste de disco mínimo
    rel = subir_a_s3(tmp, destino_rel=f"{carpeta_usuario}/{remote_name}")
    tmp.unlink(missing_ok=True)              # ← limpieza inmediata (disco)
    return rel                               # ruta relativa guardada

manifest, irodl_path = await loop.run_in_executor(
    None,
    lambda: export_irodl(
        local_path,
        upload_chunk,
        prefix=f"{carpeta_usuario}/{local_path.name}",
        progress=cb_progreso_export,
        cancel=threading_event_de_cancel,
    ),
)
await message.reply_document(irodl_path,
    caption=f"📦 {manifest['filename']} · {len(manifest['chunks'])} chunks de 2 MiB · MD5 verificado\n"
            f"Compártelo: se abre con IroDL o con este bot.")
irodl_path.unlink(missing_ok=True)
```

**Alternativa para archivos ya subidos** (sin volver a descargar de Telegram):
recuperar la ruta S3 guardada en la DB del bot, re-descargar el objeto a
`work_dir` con el mismo cliente de descarga que ya usa el bot, y llamar a
`export_irodl` igual que arriba.

## 4. Notas operativas

| Tema | Recomendación |
|---|---|
| Disco | Cada chunk temporal vive <1 s (`upload_chunk` borra al terminar). Los `.part` del importador se limpian solos al verificar el MD5 global. |
| Cuota | Cada chunk es una subida S3: contarlas como subidas del usuario (cota defensiva `MAX_CHUNKS_PER_EXPORT = 500` ≈ 1 GB ya incluida en el módulo). |
| Paralelismo | Importador usa 4 conexiones (sube a 8 máx. por constructor); exportador es secuencial para no saturar la API de ToDus. |
| Cancelación | Mismo `threading.Event` que usa el pipeline de `.udyat` (el motor lo chequea entre bloques). |
| Límites Telegram | El `.irodl` es un JSON pequeño; el archivo reconstruido pasa por el flujo normal de envío del bot (con su gestión de 2 GiB / modo manual ya existente). |
| Modo normal | **Cero cambios**: ni rutas nuevas ni comportamiento distinto salvo los dos ganchos descritos. |

## 5. Pruebas recomendadas tras integrar

1. `pytest bot/tests/ -q` — la suite existente (106 tests de la Task 9) debe seguir verde.
2. Enviar al bot un `.irodl` de prueba generado con el CLI del repo IroDL
   (`python -m irodl pack <archivo> -o /tmp`) y verificar:
   enlace resultante + MD5 del archivo subido == `md5` del manifiesto.
3. Usar `Extra → Generar .irodl` sobre un archivo subido y abrir el paquete
   con la app IroDL en un dispositivo fuera de Cuba (mirror si hace falta).
