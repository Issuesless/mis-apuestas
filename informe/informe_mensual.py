#!/usr/bin/env python3
"""Informe mensual de apuestas: lee la base de datos, genera un PDF y lo envía por email.

Variables de entorno (las pone el workflow de GitHub desde los "secrets"):
  SUPABASE_URL, SUPABASE_SERVICE_KEY      lectura de la base de datos
  SMTP_USER, SMTP_PASSWORD, EMAIL_TO      envío del email (SMTP_HOST / SMTP_PORT opcionales)
  MES                                     opcional, AAAA-MM. Vacío = mes anterior

Uso manual:
  python informe_mensual.py --mes 2026-10 --no-enviar --salida informe.pdf
  python informe_mensual.py --desde-json datos.json --mes 2026-10 --no-enviar   (pruebas)

IMPORTANTE: este script se ejecuta en un repositorio público, así que NO imprime importes ni
partidos en el registro; solo mensajes de estado.
"""
import argparse
import io
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_RIGHT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
MESES_CORTO = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DEFAULT_BANK = 1000.0

INK = colors.HexColor("#13211A")
MUTED = colors.HexColor("#5B6B63")
LINE = colors.HexColor("#D3DCD6")
SOFT = colors.HexColor("#EEF2EF")
ACCENT = colors.HexColor("#17603F")
WIN = colors.HexColor("#1C8A52")
LOSS = colors.HexColor("#BF3B34")
PEND = colors.HexColor("#A86A12")


# ------------------------------------------------------------------ formato
def num(v, d=2):
    """Formato español: coma decimal; punto de millares solo desde 10.000."""
    s = f"{abs(v):,.{d}f}" if abs(v) >= 10000 else f"{abs(v):.{d}f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return ("-" if v < 0 and float(s.replace(".", "").replace(",", ".")) != 0 else "") + s


def eur(v):
    return f"{num(v)} €"


def eur_s(v):
    r = round(v, 2)
    return ("+" if r > 0 else "") + eur(r) if r != 0 else eur(0)


def pct(v, d=1):
    return f"{num(v * 100, d)} %"


def pct_s(v, d=1):
    r = round(v * 100, d)
    return ("+" if r > 0 else "") + pct(v, d) if r != 0 else pct(0, d)


def color_for(v):
    return WIN if round(v, 2) > 0 else LOSS if round(v, 2) < 0 else INK


def hexc(c):
    return "#%02x%02x%02x" % (int(c.red * 255), int(c.green * 255), int(c.blue * 255))


# ------------------------------------------------------------------ datos
def api_get(base, key, path, missing_ok=False):
    headers = {"apikey": key, "Accept": "application/json"}
    if key.count(".") == 2:  # clave antigua tipo JWT (service_role)
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if missing_ok and e.code == 404:  # tabla aún no creada (movimientos.sql sin ejecutar)
            return None
        sys.exit(f"Error al leer la base de datos (HTTP {e.code}). Revisa SUPABASE_URL y SUPABASE_SERVICE_KEY.")
    except urllib.error.URLError:
        sys.exit("No se pudo conectar con la base de datos. ¿Está pausada o la URL es correcta?")


def fetch_data(base, key):
    rows, off = [], 0
    while True:
        page = api_get(base, key, f"/rest/v1/bets?select=*&order=bet_date.asc,created_at.asc&limit=1000&offset={off}")
        rows += page
        if len(page) < 1000:
            break
        off += 1000
    st = api_get(base, key, "/rest/v1/settings?select=initial_bank&limit=1")
    bank0 = float(st[0]["initial_bank"]) if st else DEFAULT_BANK
    movs, off = [], 0
    while True:
        page = api_get(base, key, f"/rest/v1/movements?select=*&order=mov_date.asc,created_at.asc&limit=1000&offset={off}",
                       missing_ok=True)
        if page is None:
            print("Aviso: no existe la tabla de retiradas (movimientos.sql); el informe sale sin ellas.")
            break
        movs += page
        if len(page) < 1000:
            break
        off += 1000
    return rows, bank0, movs


def normalize(rows):
    out = []
    for r in rows:
        try:
            out.append({
                "match": str(r.get("match") or "Sin nombre"),
                "stake": float(r["stake"]),
                "odds": float(r["odds"]),
                "date": date.fromisoformat(str(r["bet_date"])[:10]),
                "result": r.get("result") if r.get("result") in ("pendiente", "ganada", "perdida", "nula") else "pendiente",
                "created": str(r.get("created_at") or ""),
            })
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda b: (b["date"], b["created"]))
    return out


def normalize_movs(rows):
    out = []
    for r in rows or []:
        try:
            if r.get("kind") not in ("retirada", "deposito"):
                continue
            out.append({"kind": r["kind"], "amount": float(r["amount"]),
                        "date": date.fromisoformat(str(r["mov_date"])[:10]),
                        "note": str(r.get("note") or ""), "created": str(r.get("created_at") or "")})
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda x: (x["date"], x["created"]))
    return out


def mov_delta(x):
    return x["amount"] if x["kind"] == "deposito" else -x["amount"]


def pl(b):
    return b["stake"] * (b["odds"] - 1) if b["result"] == "ganada" else -b["stake"] if b["result"] == "perdida" else 0.0


def resolved(b):
    return b["result"] in ("ganada", "perdida")


def stats(bets, start_bank):
    """Mismas definiciones que la web. `bets` en orden cronológico."""
    res = [b for b in bets if resolved(b)]
    won = [b for b in res if b["result"] == "ganada"]
    lost = [b for b in res if b["result"] == "perdida"]
    n = len(res)
    staked = sum(b["stake"] for b in res)
    profit = sum(pl(b) for b in res)
    gross = sum(pl(b) for b in won)
    loss_g = -sum(pl(b) for b in lost)
    hit = len(won) / n if n else 0.0
    implied = sum(1 / b["odds"] for b in res) / n if n else 0.0
    bank = peak = start_bank
    max_dd = max_dd_p = 0.0
    cur = best_w = worst_l = 0
    series = [start_bank]
    for b in res:
        bank += pl(b)
        series.append(bank)
        peak = max(peak, bank)
        dd = bank - peak
        if dd < max_dd:
            max_dd, max_dd_p = dd, (dd / peak if peak else 0.0)
        if b["result"] == "ganada":
            cur = cur + 1 if cur > 0 else 1
            best_w = max(best_w, cur)
        else:
            cur = cur - 1 if cur < 0 else -1
            worst_l = max(worst_l, -cur)
    return {
        "res": res, "n": n, "won": len(won), "lost": len(lost),
        "nul": sum(1 for b in bets if b["result"] == "nula"),
        "pend": sum(1 for b in bets if b["result"] == "pendiente"),
        "staked": staked, "profit": profit, "roi": profit / staked if staked else 0.0,
        "hit": hit, "avg_odds": sum(b["odds"] for b in res) / n if n else 0.0,
        "implied": implied, "edge": hit - implied if n else 0.0,
        "pf": gross / loss_g if loss_g else None,
        "best": max(res, key=pl) if res else None, "worst": min(res, key=pl) if res else None,
        "max_dd": max_dd, "max_dd_p": max_dd_p, "best_w": best_w, "worst_l": worst_l,
        "series": series, "end_bank": start_bank + profit,
    }


def odds_ranges(res):
    out = []
    for name, lo, hi in [("< 1,50", 1, 1.5), ("1,50 - 1,99", 1.5, 2), ("2,00 - 2,99", 2, 3), (">= 3,00", 3, 1e9)]:
        r = [b for b in res if lo <= b["odds"] < hi]
        st = sum(b["stake"] for b in r)
        p = sum(pl(b) for b in r)
        out.append((name, len(r), sum(1 for b in r if b["result"] == "ganada"), st, p, p / st if st else 0.0))
    return out


# ------------------------------------------------------------------ gráfico
def chart_png(month_bets, start_bank, year, month):
    res = [b for b in month_bets if resolved(b)]
    ys = [start_bank]
    for b in res:
        ys.append(ys[-1] + pl(b))
    xs = list(range(len(ys)))
    up = ys[-1] >= start_bank
    col = hexc(WIN if up else LOSS)
    fig, ax = plt.subplots(figsize=(7.0, 2.5), dpi=200)
    ax.axhline(start_bank, color=hexc(MUTED), lw=0.8, ls=(0, (4, 4)))
    ax.fill_between(xs, ys, start_bank, color=col, alpha=0.14, lw=0)
    ax.plot(xs, ys, color=col, lw=2, solid_capstyle="round", solid_joinstyle="round")
    ax.plot([xs[-1]], [ys[-1]], "o", color=col, ms=6, mec="white", mew=1.5)
    lo, hi = min(ys + [start_bank]), max(ys + [start_bank])
    pad = (hi - lo) * 0.18 or max(abs(start_bank) * 0.02, 1)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-0.2, max(xs[-1], 1) + 0.6)
    ticks = [0] + [i + 1 for i in range(len(res))]
    labels = ["Inicio"] + [f"{b['date'].day}/{b['date'].month}" for b in res]
    if len(ticks) > 12:
        step = max(1, len(ticks) // 8)
        last = len(ticks) - 1
        keep = sorted({0, last} | {i for i in range(0, len(ticks), step) if last - i >= step // 2 + 1 or i == last})
        ticks = [ticks[i] for i in keep]
        labels = [labels[i] for i in keep]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=7, color=hexc(MUTED))
    ax.tick_params(axis="y", labelsize=7, colors=hexc(MUTED), length=0)
    ax.tick_params(axis="x", length=0)
    ax.yaxis.set_major_formatter(lambda v, _: f"{num(v, 0)} €")
    ax.grid(axis="y", color=hexc(LINE), lw=0.6)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.annotate(eur(ys[-1]), (xs[-1], ys[-1]), textcoords="offset points",
                xytext=(-6, 9) if len(xs) > 2 else (6, 9), ha="right" if len(xs) > 2 else "left",
                fontsize=8, fontweight="bold", color=hexc(INK))
    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


# ------------------------------------------------------------------ PDF
def styles():
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
    return {
        "base": base,
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=22, leading=26),
        "sub": ParagraphStyle("sub", parent=base, fontSize=9, textColor=MUTED),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=14,
                             spaceBefore=12, spaceAfter=5),
        "lbl": ParagraphStyle("lbl", parent=base, fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=MUTED),
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=10.5),
        "right": ParagraphStyle("right", parent=base, fontSize=8.5, leading=10.5, alignment=TA_RIGHT),
    }


def kpi_cell(st, label, value, sub, color=INK):
    return [Paragraph(label.upper(), st["lbl"]),
            Paragraph(f'<font name="Helvetica-Bold" size="17" color="{hexc(color)}">{value}</font>',
                      ParagraphStyle("k", parent=st["base"], leading=22, spaceBefore=2)),
            Paragraph(sub, st["small"])]


def kv_table(st, rows, width):
    data = []
    for label, value, *rest in rows:
        c = rest[0] if rest else INK
        data.append([Paragraph(label, st["cell"]),
                     Paragraph(f'<font name="Helvetica-Bold" color="{hexc(c)}">{value}</font>', st["right"])])
    t = Table(data, colWidths=[width * 0.58, width * 0.42])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def grid_table(data, widths, aligns, header=True, repeat=True):
    t = Table(data, colWidths=widths, repeatRows=1 if repeat and header else 0)
    cmds = [("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    for i, a in enumerate(aligns):
        cmds.append(("ALIGN", (i, 0), (i, -1), a))
    if header:
        cmds += [("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 7),
                 ("TEXTCOLOR", (0, 0), (-1, 0), MUTED), ("BACKGROUND", (0, 0), (-1, 0), SOFT)]
    t.setStyle(TableStyle(cmds))
    return t


def build_pdf(path, bets, bank0, year, month, movs=()):
    st = styles()
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    before = [b for b in bets if b["date"] < first]
    in_month = [b for b in bets if first <= b["date"] < nxt]
    upto = [b for b in bets if b["date"] < nxt]
    mv_before = [x for x in movs if x["date"] < first]
    mv_month = [x for x in movs if first <= x["date"] < nxt]
    mv_upto = [x for x in movs if x["date"] < nxt]
    # dinero en la casa a inicio de mes: bank inicial + beneficio + depósitos − retiradas
    start_bank = bank0 + sum(pl(b) for b in before if resolved(b)) + sum(mov_delta(x) for x in mv_before)
    m = stats(in_month, start_bank)
    tot = stats(upto, bank0)
    w_m = sum(x["amount"] for x in mv_month if x["kind"] == "retirada")
    d_m = sum(x["amount"] for x in mv_month if x["kind"] == "deposito")
    w_t = sum(x["amount"] for x in mv_upto if x["kind"] == "retirada")
    d_t = sum(x["amount"] for x in mv_upto if x["kind"] == "deposito")
    contributed = bank0 + d_t
    m["end_bank"] = start_bank + m["profit"] + d_m - w_m
    m["withdrawn"], m["deposited"] = w_m, d_m
    tot["end_bank"] = contributed + tot["profit"] - w_t
    tot["withdrawn"], tot["deposited"], tot["contributed"] = w_t, d_t, contributed
    title = f"{MESES[month - 1].capitalize()} {year}"

    W = A4[0] - 32 * mm
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=15 * mm, bottomMargin=16 * mm,
                            title=f"Informe de apuestas - {title}", author="Mi Bank de Apuestas")
    story = []
    story.append(Paragraph("INFORME DE APUESTAS", ParagraphStyle("eyebrow", parent=st["lbl"], fontSize=8, textColor=ACCENT)))
    story.append(Paragraph(title, st["h1"]))
    now = datetime.now(ZoneInfo("Europe/Madrid"))
    story.append(Paragraph(f"Generado el {now.day}/{now.month}/{now.year}. "
                           "Beneficio, yield y acierto cuentan solo apuestas ganadas o perdidas.", st["sub"]))
    story.append(Spacer(1, 8))

    # tarjetas
    kc = [kpi_cell(st, "Beneficio del mes", eur_s(m["profit"]), f"{m['n']} apuestas resueltas", color_for(m["profit"])),
          kpi_cell(st, "Yield", pct_s(m["roi"]) if m["n"] else "-", f"sobre {eur(m['staked'])} apostados", color_for(m["roi"])),
          kpi_cell(st, "Acierto", pct(m["hit"]) if m["n"] else "-", f"{m['won']} G · {m['lost']} P · {m['nul']} N"),
          kpi_cell(st, "En la casa a fin de mes", eur(m["end_bank"]),
                   f"inicio de mes: {eur(start_bank)}" + (f" · retirado {eur(w_m)}" if w_m else ""))]
    kt = Table([kc], colWidths=[W / 4] * 4)
    kt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LINEAFTER", (0, 0), (-2, -1), 2, colors.white),
                            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    story.append(kt)

    # gráfico
    story.append(Paragraph("Evolución del bank en el mes", st["h2"]))
    if m["n"]:
        story.append(Image(chart_png(in_month, start_bank, year, month), width=W, height=W * 2.5 / 7.0))
        story.append(Paragraph("Cada punto es una apuesta resuelta (fecha en el eje). Línea discontinua: dinero en la casa a inicio de mes. "
                               "La curva muestra solo el resultado de las apuestas, sin retiradas ni depósitos.", st["small"]))
    else:
        story.append(Paragraph("Este mes no hay apuestas resueltas, así que no hay curva que dibujar.", st["small"]))

    # resumen + acumulado
    half = (W - 12 * mm) / 2
    left = kv_table(st, [
        ("Apuestas del mes", f"{len(in_month)}"),
        ("Pendientes de resolver", f"{m['pend']}"),
        ("Cuota media", num(m["avg_odds"]) if m["n"] else "-"),
        ("Probabilidad implícita media", pct(m["implied"]) if m["n"] else "-"),
        ("Ventaja sobre las cuotas", pct_s(m["edge"]) if m["n"] else "-", color_for(m["edge"])),
        ("Profit factor", num(m["pf"]) if m["pf"] is not None else "-"),
        ("Mejor racha ganadora", f"{m['best_w']} seguidas" if m["best_w"] else "-"),
        ("Peor racha perdedora", f"{m['worst_l']} seguidas" if m["worst_l"] else "-"),
        ("Máxima caída del mes", f"{eur_s(m['max_dd'])} ({pct_s(m['max_dd_p'])})" if m["n"] else "-", color_for(m["max_dd"])),
    ], half)
    right = kv_table(st, [
        ("Dinero en la casa (fin de mes)", eur(tot["end_bank"])),
        ("Beneficio acumulado", eur_s(tot["profit"]), color_for(tot["profit"])),
        ("Crecimiento sobre lo aportado", pct_s(tot["profit"] / contributed) if contributed else "-", color_for(tot["profit"])),
        ("Yield acumulado", pct_s(tot["roi"]) if tot["n"] else "-", color_for(tot["roi"])),
        ("Acierto acumulado", pct(tot["hit"]) if tot["n"] else "-"),
        ("Apuestas resueltas", f"{tot['n']}"),
        ("Cuota media", num(tot["avg_odds"]) if tot["n"] else "-"),
        ("Cuota mínima rentable", num(1 / tot["hit"]) if tot["hit"] else "-"),
        ("Máxima caída histórica", f"{eur_s(tot['max_dd'])} ({pct_s(tot['max_dd_p'])})" if tot["n"] else "-", color_for(tot["max_dd"])),
    ], half)
    two = Table([[Paragraph("Resumen del mes", st["h2"]), "", Paragraph("Acumulado hasta fin de mes", st["h2"])],
                 [left, "", right]], colWidths=[half, 12 * mm, half])
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    story.append(two)

    # mejor / peor
    if m["best"]:
        b, w = m["best"], m["worst"]
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f'Mejor apuesta: <b>{esc(b["match"])}</b> '
            f'(<font color="{hexc(color_for(pl(b)))}">{eur_s(pl(b))}</font>) · '
            f'Peor apuesta: <b>{esc(w["match"])}</b> (<font color="{hexc(color_for(pl(w)))}">{eur_s(pl(w))}</font>)', st["base"]))

    # retiradas y depósitos
    if movs:
        rec = w_t / contributed if contributed else 0.0
        kv = kv_table(st, [
            ("Retirado este mes", eur(w_m) if w_m else "-"),
            ("Depositado este mes", eur(d_m) if d_m else "-"),
            ("Retirado en total", eur(w_t)),
            ("Capital aportado (bank inicial + depósitos)", eur(contributed)),
            ("Inversión recuperada", pct(rec, 0)),
            ("Dinero propio aún en riesgo", eur(max(0.0, contributed - w_t))),
            ("Beneficio ya retirado", f"{eur(min(w_t, tot['profit']))} ({pct(min(1.0, w_t / tot['profit']), 0)})"
             if tot["profit"] > 0 else "-"),
        ], half)
        if mv_month:
            rows = [["FECHA", "TIPO", "NOTA", "IMPORTE"]]
            for x in mv_month:
                rows.append([f"{x['date'].day:02d}/{x['date'].month:02d}", "Retirada" if x["kind"] == "retirada" else "Depósito",
                             Paragraph(esc(x["note"]) or "-", st["cell"]),
                             ("-" if x["kind"] == "retirada" else "+") + eur(x["amount"])])
            right_mv = grid_table(rows, [half * 0.2, half * 0.24, half * 0.3, half * 0.26], ["LEFT", "LEFT", "LEFT", "RIGHT"], repeat=False)
        else:
            right_mv = Paragraph("Este mes no hay retiradas ni depósitos.", st["small"])
        tw = Table([[Paragraph("Retiradas y depósitos", st["h2"]), "", Paragraph("Movimientos del mes", st["h2"])],
                    [kv, "", right_mv]], colWidths=[half, 12 * mm, half])
        tw.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        story.append(Spacer(1, 6))
        story.append(KeepTogether([tw, Paragraph("Las retiradas y los depósitos no cuentan en el beneficio, el yield ni el acierto: "
                                                 "solo cambian el dinero que tienes en la casa.", st["small"])]))

    # tramos de cuota
    if m["n"]:
        rows = [["TRAMO", "APUESTAS", "ACIERTO", "APOSTADO", "BENEFICIO", "YIELD"]]
        for name, n, w, s_, p, r in odds_ranges(m["res"]):
            rows.append([name, str(n), pct(w / n, 0) if n else "-", eur(s_) if n else "-",
                         eur_s(p) if n else "-", pct_s(r) if n else "-"])
        t = grid_table(rows, [W * 0.2, W * 0.14, W * 0.14, W * 0.17, W * 0.18, W * 0.17],
                       ["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"], repeat=False)
        story.append(KeepTogether([Paragraph("Por tramo de cuota (mes)", st["h2"]), t]))

    # últimos meses
    by_month = {}
    for b in upto:
        if resolved(b):
            k = (b["date"].year, b["date"].month)
            d = by_month.setdefault(k, {"n": 0, "st": 0.0, "p": 0.0})
            d["n"] += 1
            d["st"] += b["stake"]
            d["p"] += pl(b)
    for x in mv_upto:
        by_month.setdefault((x["date"].year, x["date"].month), {"n": 0, "st": 0.0, "p": 0.0})
    keys = sorted(by_month)[-6:]
    if keys:
        wcol = bool(movs)
        rows = [["MES", "APUESTAS", "APOSTADO", "BENEFICIO", "YIELD"] + (["RETIRADO"] if wcol else []) + ["EN LA CASA A FIN DE MES"]]
        for k in keys:
            d = by_month[k]
            end = (bank0 + sum(pl(b) for b in upto if resolved(b) and (b["date"].year, b["date"].month) <= k)
                   + sum(mov_delta(x) for x in mv_upto if (x["date"].year, x["date"].month) <= k))
            wk = sum(x["amount"] for x in mv_upto if x["kind"] == "retirada" and (x["date"].year, x["date"].month) == k)
            rows.append([f"{MESES_CORTO[k[1] - 1]} {k[0]}", str(d["n"]), eur(d["st"]), eur_s(d["p"]),
                         pct_s(d["p"] / d["st"] if d["st"] else 0)] + ([eur(wk) if wk else "-"] if wcol else []) + [eur(end)])
        widths = ([W * 0.13, W * 0.12, W * 0.15, W * 0.15, W * 0.11, W * 0.14, W * 0.20] if wcol
                  else [W * 0.16, W * 0.14, W * 0.17, W * 0.18, W * 0.14, W * 0.21])
        t = grid_table(rows, widths, ["LEFT"] + ["RIGHT"] * (len(widths) - 1), repeat=False)
        story.append(KeepTogether([Paragraph("Últimos meses", st["h2"]), t]))

    # listado
    story.append(Paragraph(f"Apuestas de {MESES[month - 1]} ({len(in_month)})", st["h2"]))
    if in_month:
        names = {"ganada": ("GANADA", WIN), "perdida": ("PERDIDA", LOSS), "nula": ("NULA", MUTED), "pendiente": ("PENDIENTE", PEND)}
        rows = [["FECHA", "PARTIDO", "IMPORTE", "CUOTA", "RESULTADO", "BENEFICIO"]]
        for b in in_month:
            label, c = names[b["result"]]
            p = pl(b)
            rows.append([f"{b['date'].day:02d}/{b['date'].month:02d}", Paragraph(esc(b["match"]), st["cell"]),
                         eur(b["stake"]), num(b["odds"]),
                         Paragraph(f'<font color="{hexc(c)}" size="7"><b>{label}</b></font>', st["cell"]),
                         Paragraph(f'<font color="{hexc(color_for(p))}">{eur_s(p) if b["result"] in ("ganada", "perdida") else "-"}</font>', st["right"])])
        t = grid_table(rows, [W * 0.09, W * 0.38, W * 0.13, W * 0.09, W * 0.15, W * 0.16],
                       ["LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT", "RIGHT"])
        story.append(t)
    else:
        story.append(Paragraph("No hay apuestas registradas este mes.", st["small"]))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 9 * mm, f"Mi Bank de Apuestas · Informe de {title}")
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Página {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return m, tot, start_bank


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------ email
def send_email(pdf_path, year, month, m, tot):
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASSWORD", "").strip()
    to = os.environ.get("EMAIL_TO", "").strip() or user
    host = os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com"
    # Se quitan espacios normales, espacios especiales, saltos de línea y caracteres invisibles que se cuelan al copiar y pegar.
    user = "".join(ch for ch in user if ch.isprintable() and not ch.isspace() and ch.isascii())
    to = "".join(ch for ch in to if ch.isprintable() and not ch.isspace() and ch.isascii())
    if host == "smtp.gmail.com":
        pwd = "".join(ch for ch in pwd if ch.isalnum() and ch.isascii())  # la clave de aplicación son 16 letras
    if not (user and pwd and to):
        sys.exit("Faltan SMTP_USER, SMTP_PASSWORD o EMAIL_TO. Añádelos en Settings → Secrets del repositorio.")
    title = f"{MESES[month - 1]} de {year}"
    msg = EmailMessage()
    msg["Subject"] = f"Informe de apuestas · {title}"
    msg["From"] = user
    msg["To"] = to
    if m["n"]:
        resumen = (f"Beneficio del mes: {eur_s(m['profit'])} (yield {pct_s(m['roi'])}, acierto {pct(m['hit'])}, "
                   f"{m['n']} apuestas resueltas).")
    else:
        resumen = "Este mes no hay apuestas resueltas."
    msg.set_content(
        f"Hola,\n\nAdjunto el informe de apuestas de {title}.\n\n{resumen}\n"
        f"En la casa a fin de mes: {eur(m['end_bank'])} · Beneficio acumulado: {eur_s(tot['profit'])}.\n"
        + (f"Retirado este mes: {eur(m['withdrawn'])} · Retirado en total: {eur(tot['withdrawn'])}.\n" if tot.get("withdrawn") else "")
        + "\n"
        "Generado automáticamente.\n")
    with open(pdf_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(pdf_path))
    # Diagnóstico sin datos sensibles (el registro de Actions es público): solo formato, nunca valores.
    print(f"Comprobación: SMTP_USER con @ = {'sí' if '@' in user else 'NO'}; EMAIL_TO con @ = {'sí' if '@' in to else 'NO'}; "
          f"SMTP_PASSWORD de {len(pwd)} caracteres (una contraseña de aplicación de Google tiene 16).")
    ctx = ssl.create_default_context()
    forced = os.environ.get("SMTP_PORT", "").strip()
    # Si no se indica puerto, se prueba SSL (465) y después STARTTLS (587): Gmail admite los dos.
    attempts = [int(forced)] if forced.isdigit() else [465, 587]
    last = ""
    for port in attempts:
        stage = "conectar"
        try:
            if port == 465:
                s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=60)
            else:
                s = smtplib.SMTP(host, port, timeout=60)
            with s:
                if port != 465:
                    stage = "cifrar (STARTTLS)"
                    s.ehlo()
                    s.starttls(context=ctx)
                    s.ehlo()
                stage = "iniciar sesión"
                s.login(user, pwd)
                stage = "enviar"
                s.send_message(msg)
            return
        except smtplib.SMTPAuthenticationError:
            sys.exit("El correo rechazó el usuario o la contraseña. Con Gmail hay que usar una «contraseña de aplicación» "
                     "(SMTP_USER = tu Gmail completo, SMTP_PASSWORD = las 16 letras).")
        except (smtplib.SMTPException, OSError) as e:
            detalle = str(e).strip().replace("\n", " ")[:200]
            last = f"puerto {port}, fallo al {stage}: {type(e).__name__}" + (f" ({detalle})" if detalle else "")
            print(f"Intento fallido: {last}")
    sys.exit(f"No se pudo enviar el correo. Último intento: {last}.")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mes", help="AAAA-MM (por defecto, el mes anterior)")
    ap.add_argument("--salida", help="ruta del PDF (por defecto informe-AAAA-MM.pdf)")
    ap.add_argument("--no-enviar", action="store_true", help="solo genera el PDF")
    ap.add_argument("--desde-json", help="leer datos de un JSON en vez de la base de datos (pruebas)")
    a = ap.parse_args()

    mes = (a.mes or os.environ.get("MES", "")).strip()
    if mes:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
            sys.exit("El mes debe tener formato AAAA-MM, por ejemplo 2026-10.")
        year, month = int(mes[:4]), int(mes[5:])
    else:
        t = datetime.now(ZoneInfo("Europe/Madrid")).date()
        year, month = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)

    if a.desde_json:
        with open(a.desde_json, encoding="utf-8") as f:
            raw = json.load(f)
        rows, bank0, mrows = raw["bets"], float(raw.get("initial_bank", DEFAULT_BANK)), raw.get("movements", [])
    else:
        base, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not (base and key):
            sys.exit("Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY. Añádelos en Settings → Secrets del repositorio.")
        rows, bank0, mrows = fetch_data(base, key)

    bets = normalize(rows)
    out = a.salida or f"informe-{year}-{month:02d}.pdf"
    m, tot, _ = build_pdf(out, bets, bank0, year, month, normalize_movs(mrows))
    print(f"PDF generado para {year}-{month:02d} ({len(bets)} apuestas en total).")
    if a.no_enviar:
        return
    send_email(out, year, month, m, tot)
    print("Email enviado.")


if __name__ == "__main__":
    main()
