"""Línea de comandos: imprime el Top 3 y los KPIs.

Uso: python -m kwd.cli --estado data/estado.json [--inicio "2026-10-02 14:00"] [--pdf salida/informe.pdf]
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from .datos import cargar_estado
from .config import RECURSOS
from .motor import Recomendacion, recomendar


def _linea_plan(p, hz) -> str:
    k = p.kpis
    return (f"{p.nombre:<20} estado={p.estado:<10} puntuación={p.puntuacion:6.2f}  idoneidad={p.idoneidad_txt():>8}  "
            f"gap={'—' if p.gap is None else format(p.gap, '.4f')}  t={p.tiempo_s:5.2f}s\n"
            f"    config turno actual: {p.config_turno_actual}\n"
            f"    contribuciones: " + ", ".join(f"{c}={v:.2f}" for c, v in p.contribuciones.items()) + "\n"
            f"    operarios-h={k['operarios_horas']:.0f} (media {k['operarios_ocup_media_pct']:.0f} %, pico "
            f"{k['operarios_ocup_pico_pct']:.0f} %)  picking-h={k['picking_horas']:.1f}  "
            f"carretilleros-h={k['carretilleros_horas']:.1f}\n"
            f"    Mto media {k['mto_ocup_media_pct']:.0f} % pico {k['mto_ocup_pico_pct']:.0f} %  Calidad media "
            f"{k['calidad_ocup_media_pct']:.0f} % pico {k['calidad_ocup_pico_pct']:.0f} %\n"
            f"    almacén medio {k['m2_medio']:.0f} m² ({k['m2_medio_pct']:.0f} %), pico {k['m2_pico']:.0f} m² "
            f"({k['m2_pico_pct']:.0f} %)\n"
            f"    kWh red {k['kwh_total']:.0f} (bruto {k['kwh_bruto']:.0f}; solar {k['kwh_solar_pct']:.0f} %; "
            f"noche {k['kwh_noche']:.0f})  demanda cubierta {k['demanda_cubierta_pct']:.0f} %  "
            f"horas-célula {k['horas_celula']:.0f}\n"
            f"    camiones/día {k['camiones_dia']:.0f} (máx {k['camiones_por_ciclo_max']}/ciclo)  "
            f"HORAS LIBRES total {k['horas_libres_total']:.0f} (" +
            ", ".join(f"{r} {k[f'horas_libres_{r}']:.0f}" for r in RECURSOS) + ")  "
            f"consumos de SS {k.get('ss_consumos', 0)}\n"
            f"    stock vs óptimo en cierres: desviación media {k['stock_opt_dev_media_pct'] or 0:.1f} %, "
            f"máx {k['stock_opt_dev_max_pct'] or 0:.1f} %")


def imprimir(rec: Recomendacion) -> None:
    hz = rec.horizonte
    print(f"Plan desde {rec.inicio:%Y-%m-%d %H:%M} ({hz.horas} h), cálculo total {rec.tiempo_total_s:.2f} s")
    print("=" * 100)
    for p in rec.top:
        print(_linea_plan(p, hz))
    if rec.contingencia is not None:
        print("*** PLAN INVIABLE ***")
        print(_linea_plan(rec.contingencia, hz))
        for m in rec.contingencia.incumplimientos[:10]:
            print("    - " + m)
    print("=" * 100)
    e = rec.explicacion
    print(f"QUÉ ACTIVAR en el turno actual ({e['turno_actual']}):")
    if len(e["que"]):
        print(e["que"].to_string(index=False, float_format=lambda x: f"{x:.1f}"))
    print("\nPOR QUÉ:")
    for m in e["porque"]:
        print("  - " + m)
    for d in e["impacto"]["delta_vs_alternativas"]:
        print(f"IMPACTO vs {d['nombre']}: puntuación {d['puntuacion']:+.2f}, "
              f"horas libres {d['horas_libres_total']:+.0f}")
    if rec.aviso_direccion:
        print("\n" + rec.aviso_direccion)
    if rec.alertas:
        print("\nALERTAS:")
        for m in rec.alertas:
            print("  ! " + m)
    print("\nResumen por turno (Top 1):")
    p = rec.mejor
    if p is not None and len(p.resumen_turnos):
        cols = ["turno", "inicio", "horas", "operarios_horas", "operarios_ocup_pico_pct", "m2_medio",
                "kwh_total", "kwh_solar_pct", "celulas_activas"]
        print(p.resumen_turnos[cols].to_string(index=False, float_format=lambda x: f"{x:.1f}"))


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Motor de decisión KWD")
    ap.add_argument("--estado", default=None, help="JSON de estado (por defecto data/estado.json)")
    ap.add_argument("--inicio", default=None, help='Inicio del plan, p. ej. "2026-10-02 14:00"')
    ap.add_argument("--horas", type=int, default=None, help="Horas del horizonte")
    ap.add_argument("--top", type=int, default=3, help="Número de alternativas")
    ap.add_argument("--pdf", default=None, help="Ruta del informe PDF (requiere kwd.informes)")
    args = ap.parse_args(argv)
    esc = cargar_estado(args.estado)
    inicio = pd.Timestamp(args.inicio) if args.inicio else pd.Timestamp.now().floor("h")
    rec = recomendar(esc, inicio, args.horas, args.top)
    imprimir(rec)
    if args.pdf:
        try:
            from .informes import generar_informe_pdf
        except ImportError:
            print("El módulo kwd.informes no está disponible; no se genera el PDF.")
        else:
            print("Informe PDF:", generar_informe_pdf(rec, esc, args.pdf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
