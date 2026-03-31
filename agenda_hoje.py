"""
agenda_hoje.py — Compromissos do dia de AGORA em diante.
Replica exatamente o que a aba Agenda da Home mostra.
Fontes:
  - CPQD: /api/week  (Google Calendar ao vivo, campo day="today")
  - Globo: db/data/globo_week.json
  - AlliedIT: db/data/allidit_week.json
Uso: python agenda_hoje.py
"""
import urllib.request
import json
import sys
import os
from datetime import datetime, timezone, timedelta

DASHBOARD_URL = "http://127.0.0.1:8888"
WORKSPACE     = r"C:\Users\hudsons\.openclaw\workspace"

CLIENT_BADGE = {
    "CPQD":     "CPQD   ",
    "Globo":    "Globo  ",
    "AlliedIT": "Allied ",
}

def status_str(hora, now):
    """Devolve (status_code, texto_display) a partir de hora HH:MM."""
    try:
        h, m = map(int, hora.split(":"))
        ev_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = int((ev_dt - now).total_seconds() / 60)
        if diff < -90:   return "encerrado", ""
        if diff < 0:     return "em_andamento", "🔴 EM ANDAMENTO"
        if diff <= 15:   return "iminente",     f"⚡ em {diff} min"
        if diff <= 60:   return "em_breve",     f"🟡 em {diff} min"
        hh, mm = divmod(diff, 60)
        txt = f"🟢 em {hh}h{mm:02d}" if mm else f"🟢 em {hh}h"
        return "mais_tarde", txt
    except Exception:
        return "mais_tarde", "🟢"

def main():
    sp  = timezone(timedelta(hours=-3))
    now = datetime.now(sp)
    today_str = now.date().isoformat()  # YYYY-MM-DD

    events = []
    seen   = set()

    # ── 1. CPQD via /api/week (campo day="today") ──
    try:
        r = urllib.request.urlopen(f"{DASHBOARD_URL}/api/week", timeout=12)
        d = json.loads(r.read())
        for m in d.get("cal_meetings", []):
            if m.get("day") != "today":
                continue
            hora   = m.get("time", "")
            titulo = m.get("title", "") or "(sem título)"
            key    = (titulo.strip().lower(), hora)
            if key in seen: continue
            seen.add(key)
            atts = [a.split("@")[0] for a in (m.get("attendees") or [])
                    if "resource.calendar" not in a]
            events.append({
                "hora":          hora,
                "titulo":        titulo,
                "cliente":       "CPQD",
                "link":          m.get("meetLink", ""),
                "local":         m.get("location", ""),
                "participantes": atts,
                "cancelado":     False,
            })
    except Exception as e:
        print(f"  [aviso CPQD] {e}", file=sys.stderr)

    # ── 2. Globo via globo_week.json ──
    try:
        path = os.path.join(WORKSPACE, "db", "data", "globo_week.json")
        if os.path.exists(path):
            gd = json.loads(open(path, encoding="utf-8").read())
            for r in gd.get("reunioes", []):
                if r.get("data", "") != today_str: continue
                hora   = r.get("hora", "")
                titulo = r.get("titulo", "") or "(sem título)"
                key    = (titulo.strip().lower(), hora)
                if key in seen: continue
                seen.add(key)
                events.append({
                    "hora":          hora,
                    "titulo":        titulo,
                    "cliente":       "Globo",
                    "link":          r.get("link", ""),
                    "local":         r.get("plataforma", ""),
                    "participantes": r.get("participantes", []),
                    "cancelado":     r.get("status", "").lower() == "cancelado",
                })
    except Exception as e:
        print(f"  [aviso Globo] {e}", file=sys.stderr)

    # ── 3. AlliedIT via allidit_week.json ──
    try:
        path = os.path.join(WORKSPACE, "db", "data", "allidit_week.json")
        if os.path.exists(path):
            ad = json.loads(open(path, encoding="utf-8").read())
            for r in ad.get("reunioes", []):
                if r.get("data", "") != today_str: continue
                hora   = r.get("hora", "")
                titulo = r.get("titulo", "") or "(sem título)"
                key    = (titulo.strip().lower(), hora)
                if key in seen: continue
                seen.add(key)
                events.append({
                    "hora":          hora,
                    "titulo":        titulo,
                    "cliente":       "AlliedIT",
                    "link":          r.get("link", ""),
                    "local":         r.get("plataforma", ""),
                    "participantes": r.get("participantes", []),
                    "cancelado":     r.get("status", "").lower() == "cancelado",
                })
    except Exception as e:
        print(f"  [aviso AlliedIT] {e}", file=sys.stderr)

    # ── Filtra encerrados e ordena ──
    visíveis = []
    for ev in events:
        hora = ev.get("hora", "")
        st, _ = status_str(hora, now)
        if st == "encerrado":
            continue
        ev["_st"], ev["_st_txt"] = status_str(hora, now)
        visíveis.append(ev)

    visíveis.sort(key=lambda x: x.get("hora") or "99:99")

    # ── Output ──
    clientes = list(dict.fromkeys(e["cliente"] for e in visíveis))
    print("=" * 60)
    print("  📅 AGENDA DO DIA — TODOS OS CLIENTES")
    print("=" * 60)
    print(f"  Agora: {now.strftime('%d/%m/%Y %H:%M')}")

    if not visíveis:
        print("\n  ✅ Sem mais compromissos para hoje.")
        print("=" * 60)
        return

    print(f"  Fontes: {' · '.join(clientes)}  ({len(visíveis)} eventos)\n")

    for ev in visíveis:
        hora      = ev.get("hora", "--:--") or "--:--"
        titulo    = ev.get("titulo", "")
        cliente   = ev.get("cliente", "")
        badge     = CLIENT_BADGE.get(cliente, cliente)
        partic    = ev.get("participantes", [])
        link      = ev.get("link", "")
        local     = ev.get("local", "")
        cancelado = ev.get("cancelado", False)
        st_txt    = ev.get("_st_txt", "🟢")

        if cancelado:
            print(f"  ╌╌ {hora}  [{badge}] {titulo}  [CANCELADO]\n")
            continue

        print(f"  ┌─ {hora}  {st_txt}")
        print(f"  │  [{badge}] {titulo}")
        if local:
            print(f"  │  📍 {local}")
        if link:
            print(f"  │  🔗 {link}")
        if partic:
            if isinstance(partic, list) and partic:
                names = ", ".join(str(p) for p in partic[:4])
                extra = f" (+{len(partic)-4})" if len(partic) > 4 else ""
                print(f"  │  👥 {names}{extra}")
        print("  └" + "─" * 48)
        print()

    print("=" * 60)

if __name__ == "__main__":
    main()
