<div align="center">

<img src="assets/banner.png" alt="IroDL" width="720"/>

**IroDL** — Reconstrucción limpia del sistema de paquetes de descarga chunked
(inspirado en el protocolo invertido de UDYAT Downloader), adaptada a la
infraestructura propia del bot S3 (`s3.todus.cu`).

*Sin backend · Sin marcas ajenas · Formato propio `.irodl` · Motor de descarga intacto*

</div>

---

## ¿Qué es esto?

**IroDL** es un ecosistema completo para compartir archivos grandes por Telegram
sin depender de servicios ajenos:

1. El **bot S3** (repositorio privado `juanbas2005/S3`) puede **exportar** cualquier
   archivo que gestione como paquete `.irodl`: lo divide en **chunks de 2 MiB**, sube
   cada parte a la carpeta del usuario en `s3.todus.cu`, cifra las rutas en tokens
   AES-256 y entrega un archivo `.irodl` listo para compartir.
2. El **destinatario** abre ese `.irodl` con la **app IroDL Downloader** (Flutter,
   código en `app/`) o se lo pasa al bot: el motor descifra los tokens **localmente**
   (cero backend), descarga los chunks en paralelo, **verifica el MD5 de cada parte**
   y el **MD5 global**, y reensambla el archivo original.
3. Todo el conocimiento del protocolo original está documentado en
   [`docs/INGENIERIA_INVERSA.md`](docs/INGENIERIA_INVERSA.md) — reconstrucción
   clean-room, sin código de terceros.

## Componentes

| Ruta | Qué es |
|---|---|
| `irodl/` | Motor **Python** (paquete + CLI): cripto, manifiesto, empaquetado, descarga paralela con MD5 y resume. |
| `tests/` | 22 tests: unitarios + E2E contra un servidor S3 simulado (protocolo idéntico). |
| `app/` | **App Flutter** (Android): descargador `.irodl`, tema oscuro propio, icono propio, sin watermarks. |
| `bot_patch/` | Módulos listos para integrar el extra `.irodl` en el bot S3 (exportar + importar), sin tocar el modo normal. |
| `docs/` | Especificación del formato `.irodl` e informe de ingeniería inversa. |
| `scripts/make_icon.py` | Regenera icono y banner (PIL puro, sin assets de terceros). |

## Uso rápido (CLI)

```bash
pip install -r requirements.txt

# Empaquetar un archivo (chunks 2 MiB + tokens) a disco (simulación)
python -m irodl pack pelicula.mkv -o salida/ --prefix pk/dd/tg_7970466590

# Ver un paquete y descifrar sus rutas
python -m irodl inspect pelicula.mkv.irodl --show-paths

# Descargar un paquete (paralelo, MD5, resume)
python -m irodl fetch pelicula.mkv.irodl -o descargas/ -c 4
```

## App Android

El APK se compila solo en cada push con GitHub Actions (ver pestaña **Actions** →
artefacto `IroDL-APK`); también con un tag `v*` se publica en **Releases**.

```bash
cd app && flutter pub get && dart run flutter_launcher_icons && flutter build apk --release
```

Características de la app:
- Importa `.irodl` desde el selector de archivos.
- Descifrado local de tokens (AES-256-CBC, PKCS7) — **no llama a ningún backend**.
- Descarga paralela (4 conexiones), MD5 por chunk, reintentos con `Range`,
  reanudación de sesiones anteriores, cancelación conservando el progreso.
- Reensamblado en streaming + verificación MD5 global antes de entregar el archivo.
- Abrir / Compartir el resultado; sin marcas de agua ni branding de terceros.

## Formato `.irodl`

```json
{
  "app": "IroDL", "format": "irodl", "version": 1,
  "filename": "pelicula.mkv", "filesize": 734003200,
  "md5": "<md5 global>", "chunk_size": 2097152,
  "chunks": [{"name": "pelicula.mkv.000", "url": "irodl://<token>", "md5": "<md5 chunk>"}]
}
```

`token = b64url( IV[16] ‖ AES-256-CBC( PKCS7( ruta ) ) )`, `key = SHA-256(seed)[32]`
con semilla propia de IroDL. Detalles completos en
[`docs/FORMATO_IRODL.md`](docs/FORMATO_IRODL.md).

## Integración con el bot

Ver [`bot_patch/README_INTEGRACION.md`](bot_patch/README_INTEGRACION.md): dos módulos
autocontenidos (`downloaders/irodl.py` para importar, `irodl_export.py` para
exportar) y los puntos exactos de enganche. El modo normal del bot queda **intacto**;
el sistema `.irodl` es un **extra**.

## Notas

- El secreto del formato viaja compilado en los clientes (igual que en el diseño
  original): es ofuscación de rutas, no cifrado del contenido.
- Los objetos del bucket pueden purgarse: un `.irodl` antiguo puede devolver 404.
- Proyecto personal; úsalo con tu propia infraestructura.

## Licencia

MIT — ver [LICENSE](LICENSE).
