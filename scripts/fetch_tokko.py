"""
fetch_tokko.py  -  Trae propiedades y emprendimientos de Tokko Broker para el tablero LAGOUARDE.

Uso:
  python scripts/fetch_tokko.py           # trae los datos reales (necesita la API key)
  python scripts/fetch_tokko.py --demo    # genera datos ficticios para ver el tablero

La API key se lee de la variable de entorno TOKKO_API_KEY o del archivo LAGOUARDE/.env
(una línea: TOKKO_API_KEY=xxxx). Nunca se escribe en data/ ni en el HTML.

Historial: la API de Tokko devuelve el estado ACTUAL de cada propiedad. Para medir ventas y
alquileres en el tiempo, cada corrida compara contra la corrida anterior y registra en
data/historial.json los cambios (alta, reserva, vendida/alquilada, baja, cambio de precio).
Cuanto más seguido se corra (ideal: una vez por día), más completo queda el historial.

Archivos que escribe:
  data/propiedades.json     propiedades normalizadas (sin datos de propietarios ni contactos)
  data/emprendimientos.json emprendimientos
  data/historial.json       eventos detectados entre corridas + foto diaria de totales
  data/meta.json            fecha de sincronización, cantidades, modo (real/demo)
"""

import os
import sys
import json
import time
import random
import datetime
import argparse
import urllib.parse
import urllib.request
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT, "data")
BASE_URL = "https://www.tokkobroker.com/api/v1"
PAGE_SIZE = 20  # Tokko recomienda páginas chicas: la API corta a los 30 s

ESTADOS = {1: "A cotizar", 2: "Disponible", 3: "Reservada", 4: "No disponible"}


# ---------------------------------------------------------------- utilidades

def load_key():
    key = os.environ.get("TOKKO_API_KEY", "").strip()
    env_path = os.path.join(ROOT, ".env")
    if not key and os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("TOKKO_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key


def read_json(name, default):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(name, obj):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = os.path.join(DATA_DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(DATA_DIR, name))


def num(v):
    try:
        n = float(str(v).replace(",", "."))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def name_of(obj):
    return (obj or {}).get("name") if isinstance(obj, dict) else None


# ---------------------------------------------------------------- API Tokko

def get(resource, key, params):
    q = {"format": "json", "lang": "es_ar", "key": key, **params}
    url = f"{BASE_URL}/{resource}/?{urllib.parse.urlencode(q)}"
    for intento in range(4):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit("Tokko rechazó la API key (HTTP %d). Revisá TOKKO_API_KEY." % e.code)
            err = e
        except (urllib.error.URLError, TimeoutError) as e:
            err = e
        time.sleep(2 * (intento + 1))
    sys.exit(f"No se pudo leer {resource} (offset {params.get('offset')}): {err}")


def fetch_all(resource, key, extra=None, page_size=PAGE_SIZE):
    out, offset, total = [], 0, None
    while True:
        page = get(resource, key, {"limit": page_size, "offset": offset, **(extra or {})})
        objs = page.get("objects", [])
        total = (page.get("meta") or {}).get("total_count", total)
        out.extend(objs)
        print(f"  {resource}: {len(out)}/{total or '?'}", end="\r")
        offset += page_size
        if not objs or not (page.get("meta") or {}).get("next") or (total and offset >= total):
            break
        time.sleep(0.3)
    print()
    return out


# ---------------------------------------------------------------- normalización

def norm_property(p):
    loc = p.get("location") or {}
    full = loc.get("full_location") or ""
    partes = [s.strip() for s in full.split("|") if s.strip()]
    ops = []
    for op in p.get("operations") or []:
        precios = [pr for pr in (op.get("prices") or []) if num(pr.get("price"))]
        pr = precios[0] if precios else {}
        ops.append({
            "op": op.get("operation_type") or "Otra",
            "moneda": pr.get("currency") or None,
            "precio": num(pr.get("price")),
            "periodo": pr.get("period") or None,
        })
    fotos = p.get("photos") or []
    foto = next((f.get("thumb") or f.get("image") for f in fotos if f.get("is_front_cover")), None)
    if not foto and fotos:
        foto = fotos[0].get("thumb") or fotos[0].get("image")
    status = p.get("status")
    return {
        "id": p.get("id"),
        "ref": p.get("reference_code"),
        "titulo": p.get("publication_title") or p.get("address") or "",
        "direccion": p.get("fake_address") or p.get("address") or "",
        "tipo": name_of(p.get("type")) or "Sin tipo",
        "estado": ESTADOS.get(status, str(status) if status is not None else "—"),
        "ops": ops,
        "zona": loc.get("name") or (partes[-1] if partes else "Sin zona"),
        "ciudad": partes[-2] if len(partes) >= 2 else "",
        "ubicacion": " > ".join(partes),
        "sucursal": name_of(p.get("branch")) or "",
        "agente": name_of(p.get("producer")) or "Sin asignar",
        "ambientes": num(p.get("room_amount")),
        "dormitorios": num(p.get("suite_amount")),
        "banos": num(p.get("bathroom_amount")),
        "sup_total": num(p.get("total_surface")) or num(p.get("surface")),
        "sup_cubierta": num(p.get("roofed_surface")),
        "antiguedad": p.get("age"),
        "expensas": num(p.get("expenses")),
        "emprendimiento": name_of(p.get("development")),
        "creada": (p.get("created_at") or "")[:10] or None,
        "modificada": (p.get("deleted_at") or "")[:10] or None,
        "lat": num(p.get("geo_lat")),
        "lng": num(p.get("geo_long")),
        "foto": foto,
        "url": p.get("public_url") or None,
        "tags": [t.get("name") for t in (p.get("tags") or []) if isinstance(t, dict) and t.get("name")],
    }


def consultas_agregadas(contacts):
    """Consultas SIN datos personales: solo fecha de alta, agente, estado, si es propietario y etiquetas.
    Nombres, teléfonos, emails y documentos de los contactos nunca se guardan."""
    idx = {"agentes": [], "estados": [], "tags": []}

    def i(lista, v):
        if v not in idx[lista]:
            idx[lista].append(v)
        return idx[lista].index(v)

    rows = []
    for c in contacts:
        fecha = (c.get("created_at") or "")[:10]
        if not fecha:
            continue
        st = c.get("opportunity_status") if isinstance(c.get("opportunity_status"), dict) else {}
        rows.append([
            fecha,
            i("agentes", name_of(c.get("agent")) or "Sin asignar"),
            i("estados", st.get("name") or c.get("lead_status") or "Sin estado"),
            1 if st.get("is_closed_status") else 0,
            1 if c.get("is_owner") else 0,
            [i("tags", t["name"]) for t in (c.get("tags") or []) if isinstance(t, dict) and t.get("name")],
        ])
    rows.sort(key=lambda r: r[0])
    return {**idx, "rows": rows}


def norm_development(d):
    loc = d.get("location") or {}
    return {
        "id": d.get("id"),
        "nombre": d.get("name") or d.get("publication_title") or "",
        "zona": loc.get("name") or "",
        "direccion": d.get("fake_address") or d.get("address") or "",
        "estado": name_of(d.get("construction_status")) or str(d.get("construction_status") or ""),
        "tipo": name_of(d.get("type")) or "",
        "entrega": d.get("construction_date") or None,
        "sucursal": name_of(d.get("branch")) or "",
    }


# ---------------------------------------------------------------- historial

def main_op(p):
    return p["ops"][0] if p["ops"] else {"op": "Otra", "moneda": None, "precio": None}


def evento(fecha, p, tipo, antes=None, despues=None):
    o = main_op(p)
    return {"fecha": fecha, "id": p["id"], "ref": p["ref"], "titulo": p["titulo"],
            "tipo_prop": p["tipo"], "zona": p["zona"], "agente": p["agente"],
            "op": o["op"], "moneda": o["moneda"], "precio": o["precio"],
            "evento": tipo, "antes": antes, "despues": despues}


def update_history(props, prev, hist, fecha):
    """Compara la corrida actual contra la anterior y agrega eventos."""
    eventos = hist.setdefault("eventos", [])
    if prev:  # en la primera corrida no hay contra qué comparar
        ant = {p["id"]: p for p in prev}
        act = {p["id"]: p for p in props}
        for pid, p in act.items():
            a = ant.get(pid)
            if a is None:
                eventos.append(evento(fecha, p, "Alta"))
                continue
            if a["estado"] != p["estado"]:
                tipo = {"Reservada": "Reservada", "No disponible": "Cerrada",
                        "Disponible": "Vuelve a disponible"}.get(p["estado"], "Cambio de estado")
                eventos.append(evento(fecha, p, tipo, a["estado"], p["estado"]))
            pa, pn = main_op(a)["precio"], main_op(p)["precio"]
            if pa and pn and pa != pn and main_op(a)["moneda"] == main_op(p)["moneda"]:
                eventos.append(evento(fecha, p, "Cambio de precio", pa, pn))
        for pid, a in ant.items():
            if pid not in act:
                eventos.append(evento(fecha, a, "Baja", a["estado"], None))

    fotos = [f for f in hist.setdefault("fotos", []) if f["fecha"] != fecha]
    cuenta = lambda pred: sum(1 for p in props if pred(p))
    fotos.append({
        "fecha": fecha,
        "total": len(props),
        "disponibles": cuenta(lambda p: p["estado"] == "Disponible"),
        "reservadas": cuenta(lambda p: p["estado"] == "Reservada"),
        "venta": cuenta(lambda p: any(o["op"] == "Venta" for o in p["ops"])),
        "alquiler": cuenta(lambda p: any(o["op"].startswith("Alquiler") for o in p["ops"])),
    })
    hist["fotos"] = sorted(fotos, key=lambda f: f["fecha"])
    return hist


# ---------------------------------------------------------------- demo

def demo_data():
    """Datos ficticios con la misma forma que los reales, para ver el tablero sin la key."""
    rnd = random.Random(7)
    zonas = ["Palermo", "Belgrano", "Núñez", "Caballito", "Recoleta", "Villa Urquiza",
             "Colegiales", "Almagro", "Saavedra", "Villa Crespo", "Olivos", "Vicente López", "La Carlina"]
    tipos = [("Departamento", 55), ("Casa", 15), ("PH", 12), ("Local", 6), ("Oficina", 6),
             ("Terreno", 8), ("Cochera", 3)]
    agentes = ["Agente A", "Agente B", "Agente C", "Agente D", "Agente E"]
    hoy = datetime.date.today()
    props = []
    for i in range(1, 241):
        tipo = rnd.choices([t for t, _ in tipos], [w for _, w in tipos])[0]
        amb = None if tipo in ("Terreno", "Cochera", "Local") else rnd.choice([1, 2, 2, 3, 3, 4, 5])
        sup = {"Casa": rnd.randint(120, 400), "Terreno": rnd.randint(200, 800),
               "Cochera": rnd.randint(12, 15)}.get(tipo, (amb or 2) * rnd.randint(18, 30))
        r = rnd.random()
        ops = []
        if r < 0.55 or tipo == "Terreno":
            ops.append({"op": "Venta", "moneda": "USD",
                        "precio": round(sup * rnd.randint(1700, 3400), -3), "periodo": None})
        elif r < 0.92:
            ops.append({"op": "Alquiler", "moneda": "ARS",
                        "precio": round(sup * rnd.randint(9000, 16000), -4), "periodo": None})
        else:
            ops.append({"op": "Alquiler temporario", "moneda": "USD",
                        "precio": rnd.randint(600, 2200), "periodo": "Mensual"})
        if rnd.random() < 0.08 and ops[0]["op"] == "Venta":
            ops.append({"op": "Alquiler", "moneda": "ARS",
                        "precio": round(sup * rnd.randint(9000, 16000), -4), "periodo": None})
        zona = "La Carlina" if tipo in ("Terreno", "Casa") and rnd.random() < 0.5 else rnd.choice(zonas[:-1])
        creada = hoy - datetime.timedelta(days=rnd.randint(0, 540))
        props.append({
            "id": 100000 + i, "ref": f"DEMO{i:04d}",
            "titulo": f"{tipo} {amb} amb. en {zona}" if amb else f"{tipo} en {zona}",
            "direccion": f"Calle Ficticia {rnd.randint(100, 4999)}", "tipo": tipo,
            "estado": rnd.choices(list(ESTADOS.values()), [3, 80, 9, 8])[0], "ops": ops,
            "zona": zona, "ciudad": "Capital Federal" if zona not in ("Olivos", "Vicente López", "La Carlina") else "GBA Norte", "ubicacion": "",
            "sucursal": rnd.choice(["Casa central", "Sucursal Norte"]),
            "agente": rnd.choice(agentes), "ambientes": amb,
            "dormitorios": max(amb - 1, 0) if amb else None, "banos": 1 if not amb else max(1, amb // 2),
            "sup_total": sup, "sup_cubierta": round(sup * 0.9), "antiguedad": rnd.randint(0, 60),
            "expensas": rnd.randint(40, 250) * 1000 if tipo in ("Departamento", "Oficina") else None,
            "emprendimiento": None, "creada": creada.isoformat(), "modificada": hoy.isoformat(),
            "lat": None, "lng": None, "foto": None, "url": None, "tags": [],
        })
    eventos = []
    for _ in range(170):
        p = rnd.choice(props)
        f = (hoy - datetime.timedelta(days=rnd.randint(0, 365))).isoformat()
        t = rnd.choices(["Cerrada", "Reservada", "Cambio de precio", "Alta", "Baja"], [40, 22, 20, 12, 6])[0]
        e = evento(f, p, t)
        if t == "Cambio de precio" and e["precio"]:
            e["antes"], e["despues"] = round(e["precio"] * 1.08, -3), e["precio"]
        if t == "Cerrada":
            e["antes"], e["despues"] = "Disponible", "No disponible"
        eventos.append(e)
    fotos = []
    for d in range(180, -1, -7):
        f = hoy - datetime.timedelta(days=d)
        base = len([p for p in props if p["creada"] <= f.isoformat()])
        fotos.append({"fecha": f.isoformat(), "total": base, "disponibles": round(base * 0.8),
                      "reservadas": round(base * 0.09), "venta": round(base * 0.58),
                      "alquiler": round(base * 0.42)})
    devs = [{"id": 1, "nombre": "Torre Demo I", "zona": "Palermo", "direccion": "Calle Ficticia 1",
             "estado": "En construcción", "tipo": "Edificio", "entrega": "2027-06", "sucursal": "Casa central"},
            {"id": 2, "nombre": "Residencias Demo", "zona": "Núñez", "direccion": "Calle Ficticia 2",
             "estado": "En pozo", "tipo": "Edificio", "entrega": "2028-03", "sucursal": "Casa central"}]
    return props, devs, {"eventos": sorted(eventos, key=lambda e: e["fecha"]), "fotos": fotos}


def demo_consultas():
    rnd = random.Random(11)
    hoy = datetime.date.today()
    origenes = ["Web", "Mercadolibre", "Zonaprop", "Contacto por Whatsapp", "Argenprop", "Proppit"]
    interes = ["Venta", "Alquiler", "Casa", "Terreno", "Palermo", "Belgrano", "La Carlina"]
    fake = []
    for _ in range(1400):
        st = rnd.choices([("Pendiente contactar", False), ("Evolucionando", False), ("Tomar Accion", False),
                          ("Para reasignacion", False), ("Cerrado", True)], [20, 25, 10, 15, 30])[0]
        fake.append({"created_at": (hoy - datetime.timedelta(days=int(rnd.triangular(0, 540, 0)))).isoformat(),
                     "agent": {"name": rnd.choice(["Agente A", "Agente B", "Agente C", "Agente D"])},
                     "opportunity_status": {"name": st[0], "is_closed_status": st[1]},
                     "is_owner": rnd.random() < 0.07,
                     "tags": [{"name": t} for t in {rnd.choice(origenes), rnd.choice(interes), rnd.choice(interes)}]})
    return consultas_agregadas(fake)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="generar datos ficticios")
    args = ap.parse_args()
    ahora = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3))).replace(microsecond=0)  # hora de Argentina, también en GitHub
    fecha = ahora.date().isoformat()

    if args.demo:
        props, devs, hist = demo_data()
        consultas = demo_consultas()
        modo = "demo"
    else:
        key = load_key()
        if not key:
            sys.exit("Falta la API key. Creá LAGOUARDE/.env con la línea TOKKO_API_KEY=... "
                     "o corré con --demo para ver datos de prueba.")
        print("Leyendo propiedades de Tokko…")
        props = [norm_property(p) for p in fetch_all("property", key)]
        print("Leyendo emprendimientos…")
        try:
            devs = [norm_development(d) for d in fetch_all("development", key)]
        except SystemExit as e:
            print(f"  (sin emprendimientos: {e})")
            devs = []
        print("Leyendo consultas (contactos)…")
        consultas = consultas_agregadas(fetch_all("contact", key, {"order_by": "created_at"}, page_size=50))
        meta_prev = read_json("meta.json", {})
        prev = read_json("propiedades.json", []) if meta_prev.get("modo") == "real" else []
        hist = read_json("historial.json", {}) if meta_prev.get("modo") == "real" else {}
        hist = update_history(props, prev, hist, fecha)
        if not prev:
            hist.setdefault("desde", fecha)
        modo = "real"

    write_json("propiedades.json", props)
    write_json("emprendimientos.json", devs)
    write_json("historial.json", hist)
    write_json("consultas.json", consultas)
    write_json("meta.json", {"actualizado": ahora.isoformat(), "modo": modo,
                             "propiedades": len(props), "emprendimientos": len(devs), "consultas": len(consultas["rows"]),
                             "historial_desde": hist.get("desde")})
    print(f"Listo ({modo}): {len(props)} propiedades, {len(devs)} emprendimientos, "
          f"{len(consultas['rows'])} consultas, {len(hist.get('eventos', []))} eventos en el historial.")


if __name__ == "__main__":
    main()
