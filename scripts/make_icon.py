#!/usr/bin/env python3
"""Genera la identidad visual de IroDL: icono de app (1024), foreground adaptive
y banner para README. Todo generado con PIL, sin assets de terceros.

Uso:  python3 scripts/make_icon.py   (desde la raiz del repo)
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024  # lienzo maestro

# Paleta IroDL
NAVY_TOP = (11, 18, 32)       # #0B1220
NAVY_BOT = (20, 38, 63)       # #14263F
CYAN = (34, 211, 238)         # #22D3EE
BLUE = (59, 130, 246)         # #3B82F6
WHITE = (245, 250, 255)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgrad(size, top, bot):
    """Imagen con gradiente vertical."""
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        px[0, y] = lerp(top, bot, y / max(1, h - 1))
    return img.resize((w, h))


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m


def grad_fill(size, c1, c2, vertical=True):
    w, h = size
    if vertical:
        img = Image.new("RGB", (1, h))
        px = img.load()
        for y in range(h):
            px[0, y] = lerp(c1, c2, y / max(1, h - 1))
        return img.resize((w, h)).convert("RGBA")
    img = Image.new("RGB", (w, 1))
    px = img.load()
    for x in range(w):
        px[x, 0] = lerp(c1, c2, x / max(1, w - 1))
    return img.resize((w, h)).convert("RGBA")


def draw_arrow(layer_size, shaft_w, head_w, head_h, tray_w, tray_h, glow=True):
    """Dibuja flecha de descarga con bandeja, con gradiente cyan->blue y glow."""
    W = H = layer_size
    cx = W // 2
    arrow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(arrow)

    # geometria (coordenadas relativas)
    shaft_top = int(H * 0.18)
    shaft_bot = int(H * 0.52)
    head_bot = int(H * 0.74)
    tray_top = int(H * 0.80)

    grad = grad_fill((W, H), CYAN, BLUE)

    # shaft
    d.rounded_rectangle(
        [cx - shaft_w // 2, shaft_top, cx + shaft_w // 2, shaft_bot + head_h // 3],
        radius=shaft_w // 2, fill=WHITE)
    # cabeza (triangulo)
    d.polygon([(cx - head_w // 2, shaft_bot), (cx + head_w // 2, shaft_bot),
               (cx, head_bot)], fill=WHITE)
    # bandeja
    d.rounded_rectangle([cx - tray_w // 2, tray_top, cx + tray_w // 2, tray_top + tray_h],
                        radius=tray_h // 2, fill=WHITE)

    # aplicar gradiente como mascara
    mask = arrow.split()[3]
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g.paste(grad, (0, 0), mask)

    if glow:
        glow_img = g.filter(ImageFilter.GaussianBlur(28))
        # glow mas tenue
        a = glow_img.split()[3].point(lambda v: int(v * 0.85))
        glow_img.putalpha(a)
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        out.alpha_composite(glow_img)
        out.alpha_composite(g)
        return out
    return g


def make_icon(path, with_text=True, pad=0.0, radius_ratio=0.19):
    img = vgrad((S, S), NAVY_TOP, NAVY_BOT).convert("RGBA")

    # textura sutil: viñeta radial (oscurece solo los bordes)
    vig = Image.new("L", (S, S), 0)
    dv = ImageDraw.Draw(vig)
    for i in range(50):
        r = int(S * 0.30 + i * 7)
        dv.ellipse([S // 2 - r, S // 2 - r, S // 2 + r, S // 2 + r], outline=min(255, i * 9))
    vig = vig.filter(ImageFilter.GaussianBlur(60))
    dark = Image.new("RGBA", (S, S), (0, 0, 0, 90))
    img.paste(Image.composite(dark, Image.new("RGBA", (S, S), (0, 0, 0, 0)), vig), (0, 0))

    # flecha
    inner = int(S * (0.62 if with_text else 0.72))
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    arr = draw_arrow(inner, shaft_w=int(inner * 0.16), head_w=int(inner * 0.46),
                     head_h=int(inner * 0.22), tray_w=int(inner * 0.62),
                     tray_h=int(inner * 0.075))
    ax = (S - inner) // 2
    ay = int(S * (0.10 if with_text else 0.14))
    layer.alpha_composite(arr, (ax, ay))
    img.alpha_composite(layer)

    # wordmark
    if with_text:
        f = ImageFont.truetype(FONT_BOLD, int(S * 0.135))
        d = ImageDraw.Draw(img)
        text = "IroDL"
        bbox = d.textbbox((0, 0), text, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx, ty = (S - tw) // 2 - bbox[0], int(S * 0.845) - bbox[1]
        # sombra sutil
        d.text((tx + 3, ty + 3), text, font=f, fill=(0, 0, 0, 140))
        d.text((tx, ty), text, font=f, fill=WHITE)

    # padding para foreground adaptive
    if pad > 0:
        p = int(S * pad)
        canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        inner_img = img.resize((S - 2 * p, S - 2 * p), Image.LANCZOS)
        canvas.paste(inner_img, (p, p), inner_img)
        img = canvas

    # esquinas redondeadas solo para el icono con texto (play store muestra cuadrado en adaptive)
    if radius_ratio:
        img.putalpha(rounded_mask((S, S), int(S * radius_ratio)))

    img.save(path)
    print("ok:", path)


def make_banner(path):
    W, H = 1280, 400
    img = vgrad((W, H), NAVY_TOP, NAVY_BOT).convert("RGBA")
    # icono a la izquierda
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base = vgrad((S, S), NAVY_TOP, NAVY_BOT).convert("RGBA")
    arr = draw_arrow(int(S * 0.62), shaft_w=int(S * 0.10), head_w=int(S * 0.29),
                     head_h=int(S * 0.14), tray_w=int(S * 0.38), tray_h=int(S * 0.047))
    base.alpha_composite(arr, ((S - int(S * 0.62)) // 2, int(S * 0.19)))
    icon = base.resize((300, 300), Image.LANCZOS)
    icon.putalpha(rounded_mask((300, 300), 57))
    img.alpha_composite(icon, (60, 50))

    d = ImageDraw.Draw(img)
    f1 = ImageFont.truetype(FONT_BOLD, 120)
    f2 = ImageFont.truetype(FONT_BOLD, 34)
    f3 = ImageFont.truetype(FONT_BOLD, 24)
    d.text((410, 90), "IroDL", font=f1, fill=WHITE)
    # subrayado degradado
    bar = grad_fill((300, 8), CYAN, BLUE)
    img.paste(bar, (416, 235), bar)
    d.text((416, 265), "Descargador de paquetes .irodl", font=f2, fill=(148, 197, 255))
    d.text((416, 315), "Sin backend · Sin marcas · 2 MiB · MD5 verificado",
           font=f3, fill=(120, 140, 170))
    img.convert("RGB").save(path)
    print("ok:", path)


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "app", "assets", "icon"), exist_ok=True)
    make_icon(os.path.join(ROOT, "app", "assets", "icon", "icon.png"))
    make_icon(os.path.join(ROOT, "assets", "icon_1024.png"))
    make_icon(os.path.join(ROOT, "app", "assets", "icon", "icon_foreground.png"),
              with_text=False, pad=0.18, radius_ratio=0.0)
    make_banner(os.path.join(ROOT, "assets", "banner.png"))
