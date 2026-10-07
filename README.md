# LAGOUARDE: tablero de propiedades (Tokko Broker)

Tablero HTML que lee los datos de Tokko Broker a través de su API oficial y los muestra con filtros.

- **Resumen:** totales en venta y en alquiler, casas, precio típico, US$/m², gráficos por tipo, zona, estado y agente, y captaciones por mes.
- **Listado:** todas las propiedades, con orden por columna y link a la publicación.
- **Historial:** cierres (vendidas o alquiladas), reservas, altas, bajas y cambios de precio.
- **Consultas:** contactos por mes, origen (portal), agente, estado, zona y tipo buscado. Solo cantidades: no se guardan nombres, teléfonos ni emails.
- **Emprendimientos:** unidades disponibles y reservadas.

## Conectar con Tokko (una sola vez)

1. Sacá la API key de Tokko: **Mi empresa → Permisos → API key**.
2. Copiá `.env.example` como `.env` y pegá la key después de `TOKKO_API_KEY=`.
   El archivo `.env` no se sube ni se comparte, y la key nunca queda en `data/` ni en el HTML.
3. Doble clic en `actualizar.cmd`, o corré `python scripts/fetch_tokko.py`.
4. Abrí el tablero con `devserver.cmd` → http://localhost:3010

`python scripts/fetch_tokko.py --demo` genera datos ficticios, que se marcan como DATOS DE PRUEBA.

## Sobre el historial

La API de Tokko devuelve el **estado actual** de cada propiedad. Para medir ventas y alquileres
en el tiempo, cada corrida se compara con la anterior y los cambios se guardan en `data/historial.json`.
Conviene correrlo todos los días, por ejemplo con una tarea programada. Las captaciones por mes
usan la fecha de alta real de Tokko, así que salen completas desde el primer día.

No se guardan datos de propietarios ni de contactos: solo datos de la propiedad, la zona y el nombre del agente.

## Publicado en GitHub Pages

- El workflow `.github/workflows/sync-tokko.yml` corre todos los días a las 06:00 (hora de Argentina). Trae los datos con la key guardada como secret `TOKKO_API_KEY` y guarda `data/*.json` en un commit.
- Para actualizar a mano: pestaña **Actions → Sync Tokko → Run workflow**.
- El archivo `.env` con la key **no** se sube (está en `.gitignore`).

## Redes (Meta)

`scripts/fetch_meta.py` lee los mensajes de Facebook e Instagram, los formularios y los anuncios de la página.
Necesita los secrets `META_TOKEN` (token de la página), `META_PAGE_ID` y, opcional, `META_AD_ACCOUNT_ID`.
Los mensajes se clasifican por palabras clave (temas, zonas, tipo de propiedad) y en `data/redes.json`
quedan solo cantidades: nada de textos, nombres ni IDs de personas. Sin token, el paso se saltea.
