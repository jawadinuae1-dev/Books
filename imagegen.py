"""
Infographic card renderer (Pillow). Turns a lesson's "visual" spec into a futuristic
bilingual (English + Urdu) concept image. No image-API needed: free and works offline.

Layouts:
  radial -> core concept in the middle, 4 key ideas around it
  flow   -> 3-5 numbered steps top to bottom (process / sequence)
"""
import io
import math
import os

import arabic_reshaper
from bidi import get_display
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1350
FONT_DIR = os.path.dirname(os.path.abspath(__file__))  # fonts sit next to this file
EN_BOLD = os.path.join(FONT_DIR, "Inter-Bold.otf")
EN_MED = os.path.join(FONT_DIR, "Inter-Medium.otf")
UR_BOLD = os.path.join(FONT_DIR, "FreeSerifBold.ttf")
UR_REG = os.path.join(FONT_DIR, "FreeSerif.ttf")

PALETTE = [(34, 228, 255), (255, 61, 203), (139, 92, 246), (52, 245, 160), (255, 184, 0)]
WHITE = (240, 246, 255)
MUTED = (140, 160, 200)


# ───────────────────────── text helpers ─────────────────────────
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    # BASIC layout: identical metrics everywhere; Urdu is shaped by arabic_reshaper + bidi below.
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


def _shape(text: str, rtl: bool) -> str:
    return get_display(arabic_reshaper.reshape(text)) if rtl else text


def _wrap(text: str, font, maxw: int, rtl: bool) -> list:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if not cur or font.getlength(_shape(trial, rtl)) <= maxw:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(text, path, maxw, maxh, size, min_size, rtl, max_lines):
    while True:
        font = _font(path, size)
        lines = _wrap(text, font, maxw, rtl)
        line_h = int(size * (1.5 if rtl else 1.28))
        fits = len(lines) <= max_lines and len(lines) * line_h <= maxh
        if fits or size <= min_size:
            return font, lines[:max_lines], line_h
        size -= 2


def _text_block(d, cx, top, text, path, maxw, maxh, size, min_size, rtl, fill, max_lines=2) -> int:
    """Centered, auto-shrinking text. Returns height used."""
    text = str(text or "").strip()
    if not text:
        return 0
    font, lines, line_h = _fit(text, path, maxw, maxh, size, min_size, rtl, max_lines)
    y = top
    for line in lines:
        d.text((cx, y), _shape(line, rtl), font=font, fill=fill, anchor="ma")
        y += line_h
    return y - top


def _measure(text, path, maxw, maxh, size, min_size, rtl, max_lines) -> int:
    text = str(text or "").strip()
    if not text:
        return 0
    _, lines, line_h = _fit(text, path, maxw, maxh, size, min_size, rtl, max_lines)
    return len(lines) * line_h


# ───────────────────────── drawing helpers ─────────────────────────
def _background() -> Image.Image:
    grad = Image.linear_gradient("L").resize((W, H))
    bg = ImageOps.colorize(grad, black=(6, 9, 22), white=(20, 14, 52)).convert("RGBA")
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(grid)
    for x in range(0, W, 60):
        g.line([(x, 0), (x, H)], fill=(80, 120, 255, 14), width=1)
    for y in range(0, H, 60):
        g.line([(0, y), (W, y)], fill=(80, 120, 255, 14), width=1)
    bg = Image.alpha_composite(bg, grid)
    blobs = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    b = ImageDraw.Draw(blobs)
    b.ellipse([-200, -200, 520, 420], fill=(34, 228, 255, 55))
    b.ellipse([640, 900, 1300, 1560], fill=(255, 61, 203, 50))
    return Image.alpha_composite(bg, blobs.filter(ImageFilter.GaussianBlur(120)))


def _glow(img, draw_fn, radius=16):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    return Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(radius)))


def _tint(color, a=0.14, base=(10, 14, 34)):
    return tuple(int(base[i] * (1 - a) + color[i] * a) for i in range(3))


def _card(img, box, color):
    img = _glow(img, lambda d: d.rounded_rectangle(box, 28, outline=color + (220,), width=10))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, 28, fill=_tint(color), outline=color, width=3)
    return img


def _header(img, kind, item, title_en, title_ur) -> int:
    d = ImageDraw.Draw(img)
    tag = "BOOK INSIGHT" if kind == "book" else "PSYCHOLOGY LAB"
    d.text((W // 2, 52), f"◆ {tag} ◆", font=_font(EN_BOLD, 24), fill=PALETTE[0], anchor="ma")
    name = str(item).split(" — ")[0]
    _text_block(d, W // 2, 90, name, EN_MED, 900, 34, 26, 18, False, MUTED, 1)
    y = 135
    y += _text_block(d, W // 2, y, title_en, EN_BOLD, 940, 140, 60, 36, False, WHITE, 2) + 6
    y += _text_block(d, W // 2, y, title_ur, UR_BOLD, 940, 110, 54, 34, True, PALETTE[0], 2)
    return y


def _footer(img):
    d = ImageDraw.Draw(img)
    d.line([(300, 1262), (780, 1262)], fill=PALETTE[2], width=2)
    d.text((W // 2, 1280), "NEURAL MIND  ·  daily mind upgrade", font=_font(EN_MED, 24), fill=MUTED, anchor="ma")


def _pair(d, cx, y0, y1, maxw, en, ur, color, en_args, ur_args):
    """EN + UR text pair, vertically centred between y0 and y1."""
    h_en = _measure(en, EN_BOLD, maxw, *en_args[:2], *en_args[2:], False, 2)
    h_ur = _measure(ur, UR_REG, maxw, *ur_args[:2], *ur_args[2:], True, 2)
    top = y0 + max(8, (y1 - y0 - (h_en + 4 + h_ur)) // 2)
    used = _text_block(d, cx, top, en, EN_BOLD, maxw, en_args[0], en_args[1], en_args[2], False, WHITE, 2)
    _text_block(d, cx, top + used + 4, ur, UR_REG, maxw, ur_args[0], ur_args[1], ur_args[2], True, color, 2)


def _node_text(d, box, node, color):
    x0, y0, x1, y1 = box
    _pair(d, (x0 + x1) // 2, y0, y1, (x1 - x0) - 50, node["en"], node["ur"], color, (96, 38, 24), (90, 38, 26))


# ───────────────────────── layouts ─────────────────────────
def _radial(img, visual):
    nodes = visual["nodes"][:4]
    cx, cy, r = W // 2, 745, 125
    boxes = [(60, 380, 490, 590), (590, 380, 1020, 590), (60, 900, 490, 1110), (590, 900, 1020, 1110)]
    centers = [((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for b in boxes]

    # connector lines
    img = _glow(img, lambda d: [d.line([(cx, cy), c], fill=PALETTE[i] + (200,), width=8) for i, c in enumerate(centers)])
    d = ImageDraw.Draw(img)
    for i, c in enumerate(centers):
        d.line([(cx, cy), c], fill=PALETTE[i], width=3)

    for i, box in enumerate(boxes):
        img = _card(img, box, PALETTE[i])
        _node_text(ImageDraw.Draw(img), box, nodes[i], PALETTE[i])

    # centre orb
    img = _glow(img, lambda d: d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=PALETTE[0] + (255,), width=14), 22)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(12, 18, 46), outline=PALETTE[0], width=4)
    d.ellipse([cx - r + 14, cy - r + 14, cx + r - 14, cy + r - 14], outline=PALETTE[1], width=2)
    used = _text_block(d, cx, cy - 48, visual.get("center_en", ""), EN_BOLD, 190, 70, 34, 20, False, WHITE, 2)
    _text_block(d, cx, cy - 48 + used + 6, visual.get("center_ur", ""), UR_BOLD, 190, 70, 34, 22, True, PALETTE[0], 2)
    return img


def _flow(img, visual, top):
    nodes = visual["nodes"][:5]
    n = len(nodes)
    top, bottom, gap = max(top + 30, 330), 1235, 46
    h = min(200, (bottom - top - gap * (n - 1)) // n)
    for i, node in enumerate(nodes):
        y0 = top + i * (h + gap)
        box = (70, y0, 1010, y0 + h)
        col = PALETTE[i % len(PALETTE)]
        img = _card(img, box, col)
        d = ImageDraw.Draw(img)
        ncx, ncy = 160, y0 + h // 2
        d.ellipse([ncx - 44, ncy - 44, ncx + 44, ncy + 44], fill=col)
        d.text((ncx, ncy), str(i + 1), font=_font(EN_BOLD, 46), fill=(8, 10, 26), anchor="mm")
        tx0, tx1 = 235, 985
        cx, maxw = (tx0 + tx1) // 2, tx1 - tx0 - 20
        _pair(d, cx, y0, y0 + h, maxw, node["en"], node["ur"], col, (int(h * 0.5), 40, 24), (int(h * 0.45), 40, 26))
        if i < n - 1:  # arrow to next step
            ax, ay = W // 2, y0 + h + gap // 2
            d.polygon([(ax - 16, ay - 10), (ax + 16, ay - 10), (ax, ay + 12)], fill=PALETTE[(i + 1) % len(PALETTE)])
    return img


# ───────────────────────── public API ─────────────────────────
def valid_visual(v) -> bool:
    try:
        nodes = v["nodes"]
        assert isinstance(nodes, list) and 3 <= len(nodes) <= 5
        for node in nodes:
            assert isinstance(node["en"], str) and node["en"].strip()
            assert isinstance(node["ur"], str) and node["ur"].strip()
        return True
    except Exception:  # noqa: BLE001
        return False


def render_infographic(kind: str, item: str, title_en: str, title_ur: str, visual: dict) -> bytes:
    """Return JPEG bytes of the finished card."""
    if not valid_visual(visual):
        raise ValueError("invalid visual spec")
    img = _background()
    layout = "radial" if str(visual.get("layout", "")).lower() == "radial" and len(visual["nodes"]) >= 4 else "flow"
    bottom = _header(img, kind, item, title_en, title_ur)
    img = _radial(img, visual) if layout == "radial" else _flow(img, visual, bottom)
    _footer(img)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()
