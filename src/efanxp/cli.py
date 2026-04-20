"""
CLI entry point.

Usage:
  efanxp sync --all
  efanxp sync --club boca-juniors --dry-run
  efanxp sync --club boca-juniors --club river-plate
  efanxp schedule start
  efanxp status
  efanxp sources find "Universidad de Chile"
  efanxp sources list
"""

from __future__ import annotations

import sys

import click
import yaml
from rich.console import Console
from rich.table import Table

from efanxp.config import get_settings
from efanxp.utils.logger import setup_logging

console = Console()


@click.group()
@click.option("--log-level", default=None, help="Override log level (DEBUG/INFO/WARNING)")
def cli(log_level: str | None):
    """eFanXP Calendar Sync — genera archivos ICS desde fixtures y eventos de venues."""
    settings = get_settings()
    setup_logging(
        level=log_level or settings.log_level,
        log_file=settings.log_file,
    )


# ─── sync ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--all", "sync_all", is_flag=True, help="Sync all clubs")
@click.option("--club", "clubs", multiple=True, metavar="CLUB_ID",
              help="Sync specific club(s). Repeatable.")
@click.option("--dry-run", is_flag=True,
              help="Fetch y guardar en DB, pero NO escribir archivos ICS")
def sync(sync_all: bool, clubs: tuple, dry_run: bool):
    """Fetch eventos y genera archivos ICS en public/."""
    from efanxp.core.orchestrator import Orchestrator

    if not sync_all and not clubs:
        console.print("[red]Especificá --all o al menos un --club CLUB_ID[/red]")
        sys.exit(1)

    if dry_run:
        console.print("[yellow]DRY RUN — no se escribirán archivos ICS[/yellow]")

    club_ids = list(clubs) if not sync_all else None
    stats = Orchestrator(dry_run=dry_run).run_full_sync(club_ids=club_ids)
    _print_stats(stats, dry_run)


# ─── schedule ─────────────────────────────────────────────────────────────────

@cli.group()
def schedule():
    """Manage the scheduled sync daemon."""


@schedule.command("start")
@click.option("--cron", default=None, help="Override cron expression")
def schedule_start(cron: str | None):
    """Inicia el daemon APScheduler que corre sync según cron."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from efanxp.core.orchestrator import Orchestrator

    settings = get_settings()
    cron_expr = cron or settings.sync_cron_expression
    parts = cron_expr.split()

    if len(parts) != 5:
        console.print(f"[red]Cron inválido: {cron_expr!r}[/red]")
        sys.exit(1)

    scheduler = BlockingScheduler()

    def job():
        console.print("[cyan]Corriendo sync programado...[/cyan]")
        try:
            stats = Orchestrator().run_full_sync()
            console.print(f"[green]Sync ok: {stats.summary()}[/green]")
        except Exception as exc:
            console.print(f"[red]Error en sync: {exc}[/red]")

    trigger = CronTrigger(
        minute=parts[0], hour=parts[1], day=parts[2],
        month=parts[3], day_of_week=parts[4],
    )
    scheduler.add_job(job, trigger)
    console.print(f"[green]Scheduler iniciado. Cron: {cron_expr}[/green]")
    console.print("Ctrl+C para detener.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Scheduler detenido.[/yellow]")


# ─── status ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--club", default=None, help="Mostrar solo un club")
def status(club: str | None):
    """Muestra el estado del sync y los archivos ICS generados."""
    from pathlib import Path
    from sqlalchemy import select
    from efanxp.database import init_db, session_scope
    from efanxp.models import EventRecord, SyncLog

    init_db()
    settings = get_settings()

    with open(settings.clubs_config) as f:
        clubs = yaml.safe_load(f)["clubs"]

    if club:
        clubs = [c for c in clubs if c["id"] == club]

    table = Table(title="eFanXP Sync Status")
    table.add_column("Club", style="cyan")
    table.add_column("Eventos en DB", justify="right")
    table.add_column("Con fecha", justify="right")
    table.add_column("Sin fecha (TBD)", justify="right")
    table.add_column("ICS generado", justify="center")

    root = Path(__file__).resolve().parents[2]
    public_dir = root / "public"

    with session_scope() as session:
        for c in clubs:
            cid = c["id"]
            records = list(
                session.scalars(
                    select(EventRecord).where(EventRecord.club_id == cid)
                )
            )
            total = len(records)
            with_date = sum(1 for r in records if r.start_date)
            no_date = total - with_date
            ics_exists = "✅" if (public_dir / f"efanxp-{cid}.ics").exists() else "—"
            table.add_row(cid, str(total), str(with_date), str(no_date), ics_exists)

    console.print(table)

    # ICS files summary
    if public_dir.exists():
        ics_files = list(public_dir.glob("*.ics"))
        if ics_files:
            console.print(f"\n[bold]Archivos ICS en public/:[/bold]")
            for f in sorted(ics_files):
                size = f.stat().st_size
                console.print(f"  {f.name}  ({size:,} bytes)")

    # Last sync log
    with session_scope() as session:
        last = session.scalar(
            select(SyncLog).order_by(SyncLog.started_at.desc()).limit(1)
        )
        if last:
            console.print(
                f"\nÚltimo sync: [bold]{last.started_at}[/bold] "
                f"| insertados={last.events_created} actualizados={last.events_updated} "
                f"errores={last.errors} dry_run={last.dry_run}"
            )


# ─── sources ──────────────────────────────────────────────────────────────────

@cli.group()
def sources():
    """Utilidades para explorar y verificar fuentes de datos."""


@sources.command("list")
def sources_list():
    """Lista todos los clubes configurados y sus adapters."""
    settings = get_settings()
    with open(settings.clubs_config) as f:
        clubs = yaml.safe_load(f)["clubs"]

    table = Table(title="Fuentes configuradas")
    table.add_column("Club ID", style="cyan")
    table.add_column("País")
    table.add_column("Adapters")
    table.add_column("Verificado?")

    for c in clubs:
        adapters = ", ".join(s["adapter"] for s in c.get("sources", []))
        verified = all(s.get("verified", False) for s in c.get("sources", []))
        table.add_row(c["id"], c.get("country", ""), adapters, "✅" if verified else "⚠️")

    console.print(table)


@sources.command("find")
@click.argument("team_name")
def sources_find(team_name: str):
    """Busca un equipo en TheSportsDB para obtener su ID."""
    from efanxp.sources.thesportsdb import TheSportsDBSource

    src = TheSportsDBSource("_search", {"team_id": "0"})
    results = src.find_team_id(team_name)

    if not results:
        console.print(f"[red]Sin resultados para '{team_name}'[/red]")
        return

    table = Table(title=f"Resultados para '{team_name}'")
    table.add_column("ID")
    table.add_column("Nombre")
    table.add_column("Liga")
    table.add_column("País")

    for team in results[:10]:
        table.add_row(
            team.get("idTeam", ""),
            team.get("strTeam", ""),
            team.get("strLeague", ""),
            team.get("strCountry", ""),
        )
    console.print(table)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _print_stats(stats, dry_run: bool):
    prefix = "[DRY RUN] " if dry_run else ""
    table = Table(title=f"{prefix}Resultados del sync")
    table.add_column("Métrica")
    table.add_column("Cantidad", justify="right")

    table.add_row("Clubes procesados", str(len(stats.clubs_processed)))
    table.add_row("Eventos fetcheados", str(stats.fetched))
    table.add_row("[green]Insertados en DB[/green]", str(stats.inserted))
    table.add_row("[blue]Actualizados en DB[/blue]", str(stats.updated))
    table.add_row("Sin cambios", str(stats.unchanged))
    if stats.errors:
        table.add_row("[red]Errores[/red]", str(stats.errors))
    if stats.ics_files:
        table.add_row("[cyan]Archivos ICS generados[/cyan]", str(len(stats.ics_files)))

    console.print(table)

    if stats.ics_files and not dry_run:
        console.print("\n[bold]Archivos generados:[/bold]")
        for f in stats.ics_files:
            console.print(f"  public/{f}")


if __name__ == "__main__":
    cli()
