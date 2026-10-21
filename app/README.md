# IroDL Downloader (app Flutter)

Descargador Android de paquetes `.irodl`: chunks de 2 MiB, tokens AES-256
descifrados localmente, MD5 verificado por chunk y global. **Sin backend** y
**sin marcas de terceros** — identidad 100 % IroDL.

## Compilar

```bash
flutter pub get
dart run flutter_launcher_icons   # genera mipmaps del icono
flutter build apk --release
# → build/app/outputs/flutter-apk/app-release.apk
```

El CI del repo hace lo mismo en cada push (Flutter 3.24.5 stable, JDK 17,
Gradle 8.3, AGP 8.1) y deja el APK como artefacto.

## Estructura

```
lib/
├── main.dart                  # tema IroDL (navy/cyan), utilidades
├── irodl/
│   ├── crypto.dart            # token b64url(IV‖AES-256-CBC(PKCS7(ruta)))
│   ├── format.dart            # manifiesto + validaciones
│   └── downloader.dart        # paralelo 4 conexiones, MD5, resume, cancel
└── screens/
    ├── home_screen.dart       # elegir .irodl → vista previa
    └── task_screen.dart       # progreso / resultado / reintentos
```

## Datos

- Descargas en `<Documents>/IroDL/<filename>`.
- Chunks temporales en `<Documents>/IroDL/irodl_parts_<nombre>/`, borrados al
  verificar el archivo completo; si cancelas a medias, quedan para reanudar.
- Permisos: solo `INTERNET` (el selector de archivos no requiere storage).
