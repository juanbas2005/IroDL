import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'screens/home_screen.dart';

void main() {
  runApp(const IrodlApp());
}

class IrodlApp extends StatelessWidget {
  const IrodlApp({super.key});

  @override
  Widget build(BuildContext context) {
    const navy = Color(0xFF0B1220);
    const navyCard = Color(0xFF14263F);
    const cyan = Color(0xFF22D3EE);
    const blue = Color(0xFF3B82F6);

    final scheme = ColorScheme.fromSeed(
      seedColor: cyan,
      brightness: Brightness.dark,
      primary: cyan,
      secondary: blue,
      surface: navyCard,
    );

    return MaterialApp(
      title: 'IroDL',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: scheme,
        scaffoldBackgroundColor: navy,
        appBarTheme: const AppBarTheme(
          backgroundColor: navy,
          elevation: 0,
          centerTitle: true,
          titleTextStyle: TextStyle(
              color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
        ),
        cardTheme: CardTheme(
          color: navyCard,
          elevation: 0,
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16)),
          margin: EdgeInsets.zero,
        ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            backgroundColor: cyan,
            foregroundColor: Colors.black,
            textStyle: const TextStyle(fontWeight: FontWeight.w700),
            minimumSize: const Size.fromHeight(52),
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(14)),
          ),
        ),
        snackBarTheme: const SnackBarThemeData(
            behavior: SnackBarBehavior.floating),
      ),
      home: const HomeScreen(),
    );
  }
}

/// Directorio de descargas: <Documents>/IroDL/
Future<Directory> irodlDownloadsDir() async {
  final docs = await getApplicationDocumentsDirectory();
  final d = Directory('${docs.path}${Platform.pathSeparator}IroDL');
  if (!d.existsSync()) d.createSync(recursive: true);
  return d;
}

String humanSize(num bytes) {
  const units = ['B', 'KiB', 'MiB', 'GiB'];
  var v = bytes.toDouble();
  var u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  return u == 0 ? '${v.toInt()} ${units[u]}' : '${v.toStringAsFixed(1)} ${units[u]}';
}

String humanSpeed(double bps) {
  if (bps <= 0) return '—';
  return '${humanSize(bps)}/s';
}
