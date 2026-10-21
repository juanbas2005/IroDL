# Ingeniería inversa de UDYAT Downloader → reconstrucción IroDL

> Documento técnico que registra **cómo se invirtió el protocolo `.udyat`** de la app
> UDYAT Downloader (v1.1.9, paquete de `ElJoker63/UDYATDL`) y cómo ese conocimiento se
> usó para construir **IroDL**, una reconstrucción limpia (clean-room) con formato propio
> `.irodl`. **Este repositorio no contiene código original de UDYAT**: todo el código fue
> escrito desde cero a partir del comportamiento observado del protocolo de red y del
> formato de los archivos.

---

## 1. Objetivo de análisis

UDYAT Downloader es una app Flutter distribuida como APK (~40.7 MB) cuyo repositorio
público **no contiene código fuente** (solo un `.gitattributes`). El objetivo fue
entender cómo un archivo `.udyat` recibido por Telegram se convierte en el archivo
original, sin depender de servidores ajenos, para poder:

1. Integrar la **importación** de esos paquetes en el bot propio (hecho: módulo
   `downloaders/udyat.py` del bot S3, desplegado y verificado E2E).
2. Construir un **ecosistema propio equivalente** (IroDL) con formato, clave,
   identidad y app propios.

## 2. Metodología

| Paso | Técnica | Resultado |
|------|---------|-----------|
| 1 | Descarga del APK y descompresión (ZIP) | Assets Flutter, `libapp.so` (Dart AOT), `libflutter.so` |
| 2 | `strings` sobre `libapp.so` + filtrado por dominios conocidos | URLs, esquema `irodl://`-equivalente (`udyat://`), nombres de campos, rutas |
| 3 | Análisis de un manifiesto `.udyat` real recibido por Telegram | Estructura JSON completa, tokens, MD5 por chunk y global |
| 4 | Cacería de clave con oráculo | Se probaron ~200 strings candidatos como semilla hasta reproducir un token real byte a byte |
| 5 | Verificación E2E contra `s3.todus.cu/stream/` | 5 chunks de 2 MiB descargados, MD5 por chunk exacto, reensamblado = MD5 global exacto |

La app es Dart AOT compilado (Flutter release), por lo que **no existe decompilación a
fuente Dart fiable**; el protocolo se reconstruyó por análisis de cadenas y tráfico.
Ese es el límite de la "decompilación" y la razón por la que IroDL es una
reconstrucción funcional, no una copia.

## 3. Hallazgos del protocolo `.udyat`

### 3.1 Manifiesto (archivo `.udyat` = JSON UTF-8)

```json
{
  "filename": "PSY_-_Gangnam_Style.mp3",
  "filesize": 8964766,
  "md5": "d7a9741fe0049c358d59d53b25bcc9a5",
  "chunks": [
    {
      "name": "PSY_-_Gangnam_Style.mp3.001",
      "url": "https://downdus.udyat.site/d/<token>",
      "md5": "ab65729ac22487839b3d3793ff2b9393"
    }
  ]
}
```

- Los chunks son piezas de **2 MiB (2 097 152 bytes)**; la última puede ser menor.
- `name` termina en `.NNN` (índice de 3 dígitos, base 0: `.000`, `.001`, …).
- `md5` por chunk (hex minúscula) + `md5` global del archivo completo.
- El campo `url` es un **portador del token**; el host `downdus.udyat.site` es solo
  vitrina/API: el descifrado ocurre **en el cliente**.

### 3.2 Criptografía del token

```
key      = SHA-256( ASCII("32214babcb55aeb5aaa9171e1f415bf4812a57c8b85608b863de59b015343344") )   # 32 bytes → AES-256
token    = b64url( IV[16] || AES-256-CBC( PKCS7( ruta_relativa ) ) )
ruta     = "dk/dd/tg_<uid>/<archivo>.NNN"      # ruta relativa dentro del bucket
```

- IV aleatorio de 16 bytes por token (el token cambia en cada generación).
- PKCS7 estándar; sin firma ni HMAC: el token es solo confidencialidad de la ruta.
- Sin padding de bloque doble ni capas extra: una sola operación AES-CBC.

### 3.3 Resolución de la descarga

```
GET https://s3.todus.cu/stream/<ruta_descifrada>
```

- Funciona **fuera de Cuba**, sin filtro de `User-Agent`.
- Soporta `Range` (bytes=N-) → permite reanudación y descarga paralela por rangos.
- El mirror `http://152.207.206.86/stream/` responde igual (mismo backend).
- El host `downdus.udyat.site` (74.208.79.138) solo sirve una landing con enlaces
  firmados (AES-256 + anti-scraping) y una API JSON con `X-API-KEY`/`X-Session-Token`;
  **no es necesario para descargar**.

### 3.4 Comportamiento observado del backend

- Los objetos se purgan tras un tiempo → los tokens viejos devuelven `404`.
- El listado del bucket (`GET /?prefix=…`, XML `ListBucketResult` con `ETag`=MD5)
  estuvo expuesto sin autenticación y fue cerrado (403) durante el análisis.
- La eliminación de objetos (`DELETE`) respondió `204` sin credenciales
  (idempotente); no se probó sobre datos reales. **Nota de seguridad válida también
  para el bot propio**: conviene no publicar rutas previsibles.

## 4. Diferencias de IroDL respecto a UDYAT

| Aspecto | UDYAT (original) | IroDL (reconstrucción) |
|---|---|---|
| Formato | `.udyat` | `.irodl` |
| Clave de tokens | semilla del autor original | semilla propia (ver `docs/FORMATO_IRODL.md`) |
| Backend | `downdus.udyat.site` + API con claves | **ninguno**: todo local |
| Marcas / watermark | branding del autor | **sin marcas**: identidad IroDL |
| Motor de descarga | paralelo por rangos, MD5 por chunk | **mismo diseño**, reimplementado y verificado |
| Tamaño de chunk | 2 MiB | **2 MiB (idéntico)** |
| Código fuente | no disponible | 100 % incluido aquí |

## 5. Nota legal y de alcance

- El análisis se limitó a formatos de archivo y tráfico con los que se trabajó
  directamente (paquetes recibidos por Telegram) y a cadenas contenidas en el binario.
- No se redistribuye código ni recursos de la app original; IroDL es una
  implementación limpia del mismo esquema de protocolo, aplicada a la infraestructura
  propia del bot S3 (`s3.todus.cu`, carpetas del propietario del bot).
- La "seguridad" del token es por ofuscación (la clave viaja dentro de la app, igual
  que en el original): no protege contra un atacante con el binario. El secreto real
  es la ruta relativa dentro del bucket.
