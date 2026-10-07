"""
fetch_meta.py  -  Trae de Meta (Facebook / Instagram) las consultas por mensaje y los anuncios, para LAGOUARDE.

Uso:
  python scripts/fetch_meta.py          # datos reales (necesita META_TOKEN y META_PAGE_ID)
  python scripts/fetch_meta.py --demo   # datos ficticios para ver la pestaña

Variables (en LAGOUARDE/.env o como secrets de GitHub):
  META_TOKEN          token de acceso de la PÁGINA de Facebook (de larga duración)
  META_PAGE_ID        ID de la página de Facebook de Lagouarde
  META_AD_ACCOUNT_ID  (opcional) ID de la cuenta publicitaria, con o sin "act_"

Privacidad: el texto de los mensajes se lee solo para contar temas con palabras clave y se descarta.
En data/redes.json quedan únicamente cantidades: nunca nombres, textos, teléfonos ni IDs de personas.
Si falta META_TOKEN, el script avisa y termina sin error (para no romper el sync diario de Tokko).
"""

import os
import re
import sys
import json
import time
import random
import datetime
import argparse
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from fetch_tokko import ROOT, DATA_DIR, write_json, read_json  # noqa: E402

GRAPH = "https://graph.facebook.com/v23.0"
DIAS = 400  # cuánto historial de mensajes leer
TZ = datetime.timezone(datetime.timedelta(hours=-3))

# ---------------------------------------------------------------- temas por palabras clave

TEMAS = {
    "Precio / valor": r"precio|valor|cuanto (sale|cuesta|esta|piden)|cuanto es|u\$s|usd|dolar|\$",
    "Financiación / cuotas": r"financ|cuota|credito|hipotec|anticipo|plan de pago|permuta",
    "Visita": r"visit|ver la (casa|propiedad|lote)|conocer|recorr|cuando (puedo|se puede) ver|coordinar",
    "Ubicación": r"ubicaci|donde (queda|esta)|direcci|como llego|mapa",
    "Disponibilidad": r"disponible|sigue (en venta|disponible)|todavia|se vendio|vendid",
    "Más información / fotos": r"info|mas datos|detalle|foto|video|plano|medidas|metros|m2",
    "Alquiler": r"alquil|alquiler|temporad|por mes|mensual",
    "Quiero vender / tasar": r"tasaci|tasar|vender mi|quiero vender|tengo (una|un) (casa|lote|terreno|campo)|publicar mi",
    "Servicios / expensas": r"expensa|servicio|luz|gas|agua|cloaca|internet|seguridad",
    "Escritura / papeles": r"escritur|boleto|titulo|papeles|posesion",
}
TIPOS = {
    "Casa": r"\bcasa", "Lote / terreno": r"\blote|terreno", "Campo": r"\bcampo|hectar|\bha\b",
    "Chacra": r"chacra", "Quinta": r"quinta", "Departamento": r"depto|departamento|monoamb",
    "Local / galpón": r"\blocal\b|galpon|deposito|nave",
}
ZONAS_BASE = ["La Carlina", "Carlina", "Los Cardales", "Alto Los Cardales", "Capilla del Señor", "Campana",
              "Zárate", "Exaltación de la Cruz", "San Jorge", "Parque Natura", "Las Lomadas", "El Cardal",
              "Las Calandrias", "Pilar", "Escobar", "Baradero", "Chacras de la Cruz", "La Reserva",
              "Open Door", "Fátima", "Lima", "Río Luján", "Monteverde", "Las Palmas"]


def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def zonas_conocidas():
    zs = set(ZONAS_BASE)
    for p in read_json("propiedades.json", []):
        if p.get("zona") and len(p["zona"]) > 3:
            zs.add(p["zona"])
    canon = {}
    for z in sorted(zs, key=len, reverse=True):
        canon.setdefault(norm(z), "La Carlina" if norm(z) == "carlina" else z)
    return [(re.compile(r"\b" + re.escape(k) + r"\b"), v) for k, v in canon.items()]


def clasificar(texto, zonas):
    t = norm(texto)
    temas = {k for k, rx in TEMAS.items() if re.search(rx, t)}
    tipos = {k for k, rx in TIPOS.items() if re.search(rx, t)}
    zs = {v for rx, v in zonas if rx.search(t)}
    return temas, tipos, zs


# ---------------------------------------------------------------- Graph API

def load_env():
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    get = lambda k: (os.environ.get(k) or env.get(k) or "").strip()
    return get("META_TOKEN"), get("META_PAGE_ID"), get("META_AD_ACCOUNT_ID")


def graph(path, token, params=None, full_url=None):
    url = full_url or f"{GRAPH}/{path}?{urllib.parse.urlencode({**(params or {}), 'access_token': token})}"
    for intento in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            msg = (json.loads(body).get("error") or {}).get("message", body[:200]) if body.startswith("{") else body[:200]
            if e.code in (400, 401, 403):
                raise RuntimeError(f"Meta rechazó el pedido ({e.code}): {msg}")
            err = msg
        except (urllib.error.URLError, TimeoutError) as e:
            err = e
        time.sleep(3 * (intento + 1))
    raise RuntimeError(f"No se pudo leer {path}: {err}")


def paged(path, token, params, max_items=100000):
    out, page = [], graph(path, token, params)
    while True:
        out.extend(page.get("data", []))
        nxt = (page.get("paging") or {}).get("next")
        if not nxt or len(out) >= max_items:
            return out
        page = graph(None, token, full_url=nxt)


def leer_conversaciones(token, page_id, plataforma, desde):
    convs = []
    params = {"platform": plataforma, "limit": 25,
              "fields": "updated_time,messages.limit(100){message,from,created_time}"}
    page = graph(f"{page_id}/conversations", token, params)
    while True:
        for c in page.get("data", []):
            if c.get("updated_time", "") < desde:
                return convs
            convs.append(c)
        nxt = (page.get("paging") or {}).get("next")
        if not nxt:
            return convs
        page = graph(None, token, full_url=nxt)


# ---------------------------------------------------------------- agregación (sin datos personales)

def agregar_mensajes(convs_por_plataforma, page_ids):
    zonas = zonas_conocidas()
    por_mes = defaultdict(Counter)        # mes -> {plataforma: conversaciones}
    msgs_mes = defaultdict(Counter)       # mes -> {plataforma: mensajes recibidos}
    temas, tipos, zs = Counter(), Counter(), Counter()
    temas_mes = defaultdict(Counter)
    hora, dia = Counter(), Counter()
    respuestas, sin_respuesta = [], 0
    for plat, convs in convs_por_plataforma.items():
        for c in convs:
            msgs = sorted((c.get("messages") or {}).get("data", []), key=lambda m: m.get("created_time", ""))
            entrantes = [m for m in msgs if (m.get("from") or {}).get("id") not in page_ids]
            if not entrantes:
                continue
            primero = datetime.datetime.strptime(entrantes[0]["created_time"][:19], "%Y-%m-%dT%H:%M:%S") \
                .replace(tzinfo=datetime.timezone.utc).astimezone(TZ)
            mes = primero.strftime("%Y-%m")
            por_mes[mes][plat] += 1
            msgs_mes[mes][plat] += len(entrantes)
            hora[primero.hour] += 1
            dia[primero.weekday()] += 1
            te, ti, zo = clasificar(" ".join(m.get("message") or "" for m in entrantes), zonas)
            temas.update(te); tipos.update(ti); zs.update(zo)
            for t in te:
                temas_mes[mes][t] += 1
            resp = next((m for m in msgs if (m.get("from") or {}).get("id") in page_ids
                         and m["created_time"] > entrantes[0]["created_time"]), None)
            if resp:
                t0 = datetime.datetime.fromisoformat(entrantes[0]["created_time"].replace("+0000", "+00:00"))
                t1 = datetime.datetime.fromisoformat(resp["created_time"].replace("+0000", "+00:00"))
                respuestas.append(((t1 - t0).total_seconds() / 60, mes))
            else:
                sin_respuesta += 1
    return {
        "por_mes": {m: dict(v) for m, v in sorted(por_mes.items())},
        "mensajes_mes": {m: dict(v) for m, v in sorted(msgs_mes.items())},
        "temas": dict(temas.most_common()), "tipos": dict(tipos.most_common()), "zonas": dict(zs.most_common(25)),
        "temas_mes": {m: dict(v) for m, v in sorted(temas_mes.items())},
        "hora": [hora[h] for h in range(24)], "dia": [dia[d] for d in range(7)],
        "respuesta_min": sorted(round(r) for r, _ in respuestas),
        "respondidas": len(respuestas), "sin_respuesta": sin_respuesta,
    }


def leer_anuncios(token, cuenta):
    cuenta = cuenta if cuenta.startswith("act_") else "act_" + cuenta
    hoy = datetime.date.today()
    rango = json.dumps({"since": (hoy - datetime.timedelta(days=365)).isoformat(), "until": hoy.isoformat()})
    filas = paged(f"{cuenta}/insights", token, {
        "level": "campaign", "time_increment": "monthly", "time_range": rango, "limit": 200,
        "fields": "campaign_name,date_start,spend,impressions,reach,clicks,actions,account_currency"})
    out = []
    for f in filas:
        acts = {a["action_type"]: float(a["value"]) for a in f.get("actions") or []}
        contactos = acts.get("lead", 0) + acts.get("onsite_conversion.messaging_conversation_started_7d", 0)
        out.append({"campana": f.get("campaign_name"), "mes": f.get("date_start", "")[:7],
                    "gasto": float(f.get("spend") or 0), "impresiones": int(f.get("impressions") or 0),
                    "alcance": int(f.get("reach") or 0), "clics": int(f.get("clicks") or 0),
                    "contactos": int(contactos), "moneda": f.get("account_currency")})
    return out


def leer_formularios(token, page_id):
    try:
        fs = paged(f"{page_id}/leadgen_forms", token, {"fields": "name,leads_count,status,created_time", "limit": 100})
    except RuntimeError as e:
        print(f"  (sin formularios: {e})")
        return []
    return [{"nombre": f.get("name"), "contactos": f.get("leads_count", 0), "estado": f.get("status"),
             "creado": (f.get("created_time") or "")[:10]} for f in fs]


# ---------------------------------------------------------------- demo

def demo():
    rnd = random.Random(5)
    hoy = datetime.date.today()
    meses = sorted({(hoy - datetime.timedelta(days=30 * i)).strftime("%Y-%m") for i in range(13)})
    por_mes = {m: {"facebook": rnd.randint(20, 60), "instagram": rnd.randint(30, 90)} for m in meses}
    camp = ["Lotes La Carlina", "Casas Los Cardales", "Campos y chacras", "Marca Lagouarde"]
    anuncios = [{"campana": c, "mes": m, "gasto": rnd.randint(40, 260) * 1000, "impresiones": rnd.randint(8, 60) * 1000,
                 "alcance": rnd.randint(5, 40) * 1000, "clics": rnd.randint(150, 1200), "contactos": rnd.randint(4, 45),
                 "moneda": "ARS"} for c in camp for m in meses[-6:]]
    return {
        "modo": "demo", "actualizado": datetime.datetime.now(TZ).replace(microsecond=0).isoformat(),
        "mensajes": {
            "por_mes": por_mes,
            "mensajes_mes": {m: {k: v * rnd.randint(3, 6) for k, v in d.items()} for m, d in por_mes.items()},
            "temas": {"Precio / valor": 410, "Más información / fotos": 330, "Disponibilidad": 250, "Visita": 190,
                      "Ubicación": 160, "Financiación / cuotas": 120, "Alquiler": 45, "Quiero vender / tasar": 38,
                      "Servicios / expensas": 30, "Escritura / papeles": 12},
            "tipos": {"Lote / terreno": 320, "Casa": 280, "Campo": 90, "Chacra": 40, "Departamento": 25},
            "zonas": {"Los Cardales": 260, "La Carlina": 180, "Capilla del Señor": 70, "Campana": 55, "Zárate": 40},
            "temas_mes": {}, "hora": [rnd.randint(2, 10) if 0 < h < 8 else rnd.randint(25, 80) for h in range(24)],
            "dia": [rnd.randint(120, 200) for _ in range(5)] + [rnd.randint(60, 110), rnd.randint(40, 90)],
            "respuesta_min": sorted(rnd.choice([rnd.randint(1, 30), rnd.randint(30, 240), rnd.randint(240, 1500)]) for _ in range(900)),
            "respondidas": 900, "sin_respuesta": 120},
        "anuncios": anuncios,
        "formularios": [{"nombre": "Formulario La Carlina", "contactos": 214, "estado": "ACTIVE", "creado": "2024-03-01"},
                        {"nombre": "Tasaciones", "contactos": 37, "estado": "ARCHIVED", "creado": "2023-09-10"}],
    }


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        write_json("redes.json", demo())
        print("Listo (demo): data/redes.json")
        return
    token, page_id, cuenta = load_env()
    if not token or not page_id:
        print("Meta: falta META_TOKEN o META_PAGE_ID; se saltea (el resto del tablero se actualiza igual).")
        return
    out = {"modo": "real", "actualizado": datetime.datetime.now(TZ).replace(microsecond=0).isoformat(), "errores": []}
    desde = (datetime.datetime.utcnow() - datetime.timedelta(days=DIAS)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        ig = graph(page_id, token, {"fields": "instagram_business_account"}).get("instagram_business_account", {})
        page_ids = {page_id, ig.get("id")} - {None}
        convs = {}
        for plat in ("messenger", "instagram"):
            try:
                print(f"Meta: leyendo conversaciones de {plat}…")
                convs["facebook" if plat == "messenger" else "instagram"] = leer_conversaciones(token, page_id, plat, desde)
            except RuntimeError as e:
                out["errores"].append(f"{plat}: {e}")
                print(f"  {e}")
        out["mensajes"] = agregar_mensajes(convs, page_ids)
    except RuntimeError as e:
        out["errores"].append(str(e))
        print(f"  {e}")
    out["formularios"] = leer_formularios(token, page_id)
    if cuenta:
        try:
            print("Meta: leyendo anuncios…")
            out["anuncios"] = leer_anuncios(token, cuenta)
        except RuntimeError as e:
            out["errores"].append(f"anuncios: {e}")
            print(f"  {e}")
    write_json("redes.json", out)
    m = out.get("mensajes") or {}
    print(f"Listo Meta: {sum(sum(v.values()) for v in (m.get('por_mes') or {}).values())} conversaciones, "
          f"{len(out.get('anuncios') or [])} filas de anuncios, {len(out['formularios'])} formularios.")


if __name__ == "__main__":
    main()
