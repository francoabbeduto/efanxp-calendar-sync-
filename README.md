# eFanXP Calendar Sync

Genera archivos `.ics` con fixtures y eventos de venues de todos los clientes eFanXP.
Los archivos se publican automáticamente en GitHub Pages — Google Calendar los suscribe como webcal URL.

**100% gratis. Sin tarjeta de crédito. Sin APIs de pago.**

---

## Cómo funciona

```
TheSportsDB / API-Sports / VenueScraper
          ↓
    Normalize + Dedup
          ↓
      SQLite DB
          ↓
  Genera public/*.ics
          ↓
  GitHub Actions pushea
          ↓
  GitHub Pages sirve los archivos
          ↓
  Google Calendar se suscribe (webcal://)
```

Cada 6 horas, GitHub Actions:
1. Corre `efanxp sync --all`
2. Genera `public/efanxp-all.ics` (todos los clubes) + uno por club
3. Commitea y pushea los cambios
4. Google Calendar detecta la actualización automáticamente

---

## Setup (primera vez)

### 1. Fork + GitHub Pages

1. Hacé fork de este repo en tu cuenta de GitHub
2. Ir a **Settings → Pages → Source: Deploy from branch → Branch: main → Folder: /public**
3. GitHub te da una URL como `https://tu-usuario.github.io/efanxp-calendar-sync/`

### 2. Agregar secrets (solo para API-Sports/Selknam)

En **Settings → Secrets and variables → Actions**:
- `API_SPORTS_KEY` → tu key de api-sports.io (gratis, 100 req/día)

`THESPORTSDB_API_KEY` no necesita secret — la key pública `3` ya está en el workflow.

### 3. Correr el primer sync

Ir a **Actions → Sync Calendar → Run workflow → Run workflow**

Listo. En un minuto vas a ver los archivos `.ics` en la carpeta `public/`.

### 4. Suscribir Google Calendar

1. Abrir [Google Calendar](https://calendar.google.com)
2. Click en **"+" → "Desde URL"**
3. Pegar la URL webcal:
   ```
   webcal://tu-usuario.github.io/efanxp-calendar-sync/efanxp-all.ics
   ```
4. Click **"Agregar calendario"**

También podés suscribirte a un club específico:
```
webcal://tu-usuario.github.io/efanxp-calendar-sync/efanxp-boca-juniors.ics
webcal://tu-usuario.github.io/efanxp-calendar-sync/efanxp-river-plate.ics
# etc.
```

---

## Archivos ICS generados

| Archivo | Contenido |
|---------|-----------|
| `efanxp-all.ics` | Todos los clubes combinados |
| `efanxp-boca-juniors.ics` | Solo Boca Juniors |
| `efanxp-river-plate.ics` | Solo River Plate |
| `efanxp-{club-id}.ics` | Uno por cada club configurado |

---

## Clubes y fuentes de datos

| Club | País | Fuente primaria | ID verificado |
|------|------|-----------------|---------------|
| Boca Juniors | AR | TheSportsDB `135156` | ✅ |
| River Plate | AR | TheSportsDB `135171` | ✅ |
| Estudiantes de La Plata | AR | TheSportsDB `135160` | ✅ |
| Vélez Sársfield | AR | TheSportsDB `135179` | ✅ |
| Huracán | AR | TheSportsDB `135163` | ✅ |
| San Lorenzo | AR | TheSportsDB `135173` | ✅ |
| Colo Colo | CL | TheSportsDB `137724` | ✅ |
| Alianza Lima | PE | TheSportsDB `138311` | ✅ |
| Universidad de Chile | CL | TheSportsDB `135424` | ⚠️ verificar |
| Bahia / Arena Fonte Nova | BR | TheSportsDB + VenueScraper | ⚠️ verificar |
| Selknam | CL | API-Sports Rugby `28` | ⚠️ verificar |

Para verificar IDs sin confirmar:
```bash
efanxp sources find "Universidad de Chile"
efanxp sources find "Bahia"
```

---

## Uso local

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env

# Ver qué hay configurado
efanxp sources list

# Dry run (sin escribir ICS)
efanxp sync --all --dry-run

# Sync real
efanxp sync --all

# Sync de un club
efanxp sync --club boca-juniors

# Estado
efanxp status

# Daemon local (útil en servidor propio)
efanxp schedule start
```

---

## Formato de cada evento en el ICS

| Campo ICS | Ejemplo |
|-----------|---------|
| SUMMARY | `⚽ Boca Juniors vs River Plate` |
| DTSTART | Datetime con timezone si hay hora; all-day si no; 2099-01-01 si TBD |
| DTEND | DTSTART + 2h por defecto |
| LOCATION | La Bombonera |
| STATUS | CONFIRMED / CANCELLED / TENTATIVE |
| DESCRIPTION | Venue, competencia, cliente, país, fuente, ID interno, última actualización |
| UID | `thesportsdb_boca-juniors_12345@efanxp.com` (estable, nunca cambia) |
| X-EFANXP-CLUB | `boca-juniors` |
| X-EFANXP-SOURCE | `thesportsdb` |
| X-EFANXP-TYPE | `match_home` |

El UID estable garantiza que Google Calendar actualiza el evento existente en vez de crear uno duplicado cuando hay cambios de hora, fecha o estado.

---

## Agregar un nuevo club

1. Añadir entrada a `config/clubs.yaml`
2. Buscar el ID: `efanxp sources find "Nombre del Club"`
3. Actualizar `team_id` y poner `verified: true`
4. `efanxp sync --club nuevo-club --dry-run`

---

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

### Agregar un nuevo adapter de fuente

1. Crear `src/efanxp/sources/mi_fuente.py` extendiendo `BaseSource`
2. Implementar `fetch(lookahead_days, lookback_days) -> list[RawEvent]`
3. Registrar en `ADAPTER_MAP` en `orchestrator.py`
4. Agregar a `clubs.yaml`

---

## Despliegue alternativo (sin GitHub Actions)

Si preferís correrlo en tu propio servidor:

```bash
# Cron cada 6 horas
0 */6 * * * cd /opt/efanxp-calendar-sync && .venv/bin/efanxp sync --all

# O daemon
efanxp schedule start
```

Los archivos ICS en `public/` se pueden servir con cualquier servidor HTTP estático (nginx, caddy, etc.).
