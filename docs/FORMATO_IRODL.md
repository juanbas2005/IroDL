# Especificación del formato `.irodl` (v1)

Un archivo `.irodl` es un **paquete de descarga**: JSON UTF-8 (sin BOM) que describe un
archivo original dividido en chunks de **2 MiB** alojados en un servidor S3-compatible.
Cualquier cliente (la app IroDL, el bot S3, el CLI de este repo) puede reconstruir el
archivo original sin ningún servidor intermedio: **cero backend**.

---

## 1. Esquema

```json
{
  "app": "IroDL",
  "format": "irodl",
  "version": 1,
  "filename": "pelicula.mkv",
  "filesize": 734003200,
  "md5": "<md5 hex del archivo COMPLETO>",
  "chunk_size": 2097152,
  "chunks": [
    { "name": "pelicula.mkv.000",
      "url": "irodl://<token>",
      "md5": "<md5 hex del chunk 0>" },
    { "name": "pelicula.mkv.001",
      "url": "irodl://<token>",
      "md5": "<md5 hex del chunk 1>" }
  ]
}
```

### Campos obligatorios

| Campo | Tipo | Descripción |
|---|---|---|
| `filename` | string | Nombre original del archivo (solo nombre, sin ruta). |
| `filesize` | int | Tamaño total en bytes. Debe coincidir con la suma de los chunks. |
| `md5` | string | MD5 global del archivo completo (32 hex minúsculas). |
| `chunks` | array | Lista ordenada; el orden del array es el orden de reensamblado. |
| `chunks[].name` | string | `<filename>.NNN` (índice decimal, 3 dígitos; a partir de 1000 chunks, 4). |
| `chunks[].url` | string | `irodl://<token>` — token cifrado de la ruta del chunk. |
| `chunks[].md5` | string | MD5 del chunk (32 hex minúsculas). |

### Campos opcionales (recomendados)

| Campo | Default | Descripción |
|---|---|---|
| `app` | — | Identificador del generador. |
| `format` / `version` | — | Deben ser `"irodl"` y `1`. |
| `chunk_size` | 2097152 | Tamaño declarado de chunk. Los clientes **no** lo usan para reensamblar (cada chunk se descarga completo). |
| `host` | `https://s3.todus.cu/stream/` | Base de descarga. Permite redirección a mirror sin regenerar tokens. |

## 2. Tokens (`irodl://`)

```
seed    = "eb4c353716dbf7e763149b2fa90981bb32614eceaba1ef930ebba3cf577ad9bf"   (ASCII, propiedad de IroDL)
key     = SHA-256( seed )[32 bytes]                                            → AES-256
token   = base64url( IV[16] || AES-256-CBC(key, PKCS7( ruta_relativa )) )      (padding "=" omitido)
ruta    = "<prefijo_propio>/<usuario>/<filename>.NNN"                          (relativa al bucket)
```

Clave derivada (referencia para verificar implementaciones):

```
key_hex = f81a508acd1d8ffb0f1ccb1ffba4dd48320b9d84ddcf8672619ce1ec6db8e7c4
```

- IV **aleatorio por token**: dos generaciones del mismo chunk producen tokens
  distintos (los tokens no son canónicos, solo el plaintext lo es).
- Descifrado: `base64url_decode` (reponer `=`), primeros 16 bytes = IV, resto =
  ciphertext, PKCS7 unpad, UTF-8 → ruta relativa.
- El cliente resuelve: `GET {host}{ruta}` — con `host` por defecto
  `https://s3.todus.cu/stream/`.

> **Compatibilidad deliberada con `.udyat`**: la mecánica es idéntica (2 MiB, PKCS7,
> b64url IV‖CT, MD5 por chunk y global). Solo cambian la semilla de cifrado y el
> esquema del portador (`irodl://` en lugar de una URL del backend ajeno). Un cliente
> IroDL **no** puede abrir `.udyat` y viceversa: el formato es exclusivo.

## 3. Reglas de generación (bot/exportador)

1. Dividir el archivo en bloques de **2 097 152 bytes**; el último queda del tamaño
   restante (puede ser < 2 MiB; archivos ≤ 2 MiB generan un único chunk `.000`).
2. MD5 por chunk **mientras se genera** (no releer).
3. Subir cada chunk a la carpeta propia del usuario en el bucket y guardar la ruta.
4. Construir el manifiesto con la ruta cifrada de cada chunk; `filesize` = suma real.
5. Validar antes de publicar: suma de tamaños == `filesize` y `len(chunks)` correcta.

## 4. Reglas de consumo (cliente/importador)

1. Validar JSON, campos obligatorios y coherencia (`len(chunks)` ≥ 1).
2. Descifrar cada token → ruta; construir URL final.
3. Descargar chunks (paralelo, 4–6 conexiones), **verificando el MD5 de cada chunk**;
   reintentos con `Range: bytes=<lo descargado>-` y máximo de intentos por chunk.
4. Escribir cada chunk en `<destino>/<name>.part`; cuando su MD5 cuadra, renombrar a
   `<destino>/<name>` (resume sin re-descargar).
5. Reensamblar en orden → archivo final; verificar MD5 global; si falla, identificar
   chunks corruptos por tamaño/md5 y re-descargarlos.
6. Limpiar `.part` y temporales siempre (disco del dispositivo es un recurso crítico).

## 5. Límites y consideraciones

- Tamaño máximo práctico: 4 GiB (límite de Telegram para documentos en bots MTProto).
- Los tokens revelan rutas a quien tenga la clave (va compilada en la app, igual que
  en el diseño original): el formato es **ofuscación de rutas**, no cifrado del
  contenido. El contenido se transfiere por HTTPS plano.
- Los servidores pueden purgar objetos: un `.irodl` no garantiza disponibilidad
  perpetua; el error esperado es `404` por chunk vencido.
