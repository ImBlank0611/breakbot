"""Draw the 7-day day-off calendar as a PNG table (Discord can't render tables)."""

from __future__ import annotations

import io
from datetime import date

from PIL import Image, ImageDraw, ImageFont

S = 2                                  # draw at 2x so it stays sharp on phones
BG = (43, 45, 49)                      # Discord dark embed background
CELL = (49, 51, 56)
HEAD = (30, 31, 34)
TODAY = (88, 101, 242)                 # blurple
TODAY_CELL = (56, 60, 92)
LINE = (63, 65, 71)
TEXT = (242, 243, 245)
MUTED = (148, 155, 164)
PILL = (35, 165, 90)                   # green name tag
PILL_TEXT = (255, 255, 255)
NOTE = (250, 168, 26)

COL_W, PAD, GAP = 168, 16, 6
HEAD_H, PILL_H, TITLE_H = 64, 30, 50


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size * S)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def render(days: list[date], offs: dict[date, list[tuple[str, str | None]]],
           today: date, title: str) -> bytes:
    """days: the dates to show (one column each).
    offs: {date: [(display name, note or None), ...]}"""
    rows = max([len(offs.get(d, [])) for d in days] + [1])
    body_h = PAD + rows * (PILL_H + GAP) + PAD
    w = PAD * 2 + COL_W * len(days)
    h = TITLE_H + HEAD_H + body_h + PAD
    img = Image.new("RGB", (w * S, h * S), BG)
    dr = ImageDraw.Draw(img)

    f_title, f_day, f_date = _font(20), _font(13), _font(17)
    f_name, f_note, f_empty = _font(14), _font(11), _font(13)

    dr.text((PAD * S, 14 * S), title, font=f_title, fill=TEXT)

    top = TITLE_H
    for i, d in enumerate(days):
        x0 = PAD + i * COL_W
        x1 = x0 + COL_W
        is_today = d == today
        # header
        dr.rectangle([x0 * S, top * S, x1 * S, (top + HEAD_H) * S],
                     fill=TODAY if is_today else HEAD)
        label = "TODAY" if is_today else "TOMORROW" if (d - today).days == 1 \
            else f"{d:%A}".upper()
        dr.text((x0 * S + 12 * S, (top + 10) * S), label, font=f_day,
                fill=TEXT if is_today else MUTED)
        dr.text((x0 * S + 12 * S, (top + 30) * S), f"{d:%a %b} {d.day}",
                font=f_date, fill=TEXT)
        # body
        by = top + HEAD_H
        dr.rectangle([x0 * S, by * S, x1 * S, (by + body_h) * S],
                     fill=TODAY_CELL if is_today else CELL)
        people = offs.get(d, [])
        if not people:
            dr.text((x0 * S + 12 * S, (by + PAD + 7) * S), "No one off",
                    font=f_empty, fill=MUTED)
        for j, (name, note) in enumerate(people):
            py = by + PAD + j * (PILL_H + GAP)
            px0, px1 = x0 + 8, x1 - 8
            dr.rounded_rectangle([px0 * S, py * S, px1 * S, (py + PILL_H) * S],
                                 radius=6 * S, fill=PILL)
            note_w = 0
            if note:
                note_txt = _fit(dr, note, f_note, 52 * S)
                note_w = dr.textlength(note_txt, font=f_note) / S + 8
                dr.text(((px1 - 8 - note_w + 8) * S, (py + 9) * S), note_txt,
                        font=f_note, fill=(255, 236, 200))
            name_txt = _fit(dr, name, f_name, int((px1 - px0 - 18 - note_w) * S))
            dr.text(((px0 + 9) * S, (py + 7) * S), name_txt, font=f_name,
                    fill=PILL_TEXT)
        # column divider
        if i:
            dr.line([x0 * S, top * S, x0 * S, (by + body_h) * S],
                    fill=LINE, width=S)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
