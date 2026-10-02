"""Plan de referencia ("manual"): heurística sin optimizar para cuantificar el impacto del motor."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CELULA_LOGISTICA, CELULAS_PAREJA, RECURSOS
from .horizonte import Horizonte
from .modelo import evaluar, preparar
from .plan import Plan

HORIZONTE_MIRADA = 8  # horas de previsión de la regla manual


def plan_referencia(esc, hz: Horizonte, previo=None) -> Plan:
    """Heurística (ver `config.DEFINICION_PLAN_MANUAL`): cada hora laborable activa (u=1) las células cuya pieza
    quedaría por debajo del colchón (1+k)·SS dentro de 8 h, en orden de célula, mientras haya personal.
    Se evalúa con personal entero (techo de la carga por hora y rol)."""
    d = preparar(esc, hz)
    H, todas, prods = d.H, d.todas, d.prods
    a = np.zeros((H, len(todas)))
    u = np.zeros((H, len(todas)))
    stock = d.i0.copy()
    objetivo = (1 + d.k) * d.ss  # FLAG F9
    par = tuple(c for c in CELULAS_PAREJA if c in todas)
    ipp = {c: i for i, c in enumerate(prods)}

    for h in range(H):
        if d.W[h]:
            libre = d.disp[h].copy()
            # la célula 10 es obligatoria (regla 2) si procede
            if CELULA_LOGISTICA in d.ipos and hz.logistica_exigida[h]:
                a[h, d.ipos[CELULA_LOGISTICA]] = 1
                libre -= d.req[d.ipos[CELULA_LOGISTICA]]
            fin = min(H, h + HORIZONTE_MIRADA)
            previsto = stock - d.env[h:fin].sum(axis=0)  # sin producir
            if fin == H:  # FLAG F18: también cuentan los envíos previstos tras el horizonte
                previsto = previsto - d.env_final
            hechas = set()
            for c in prods:
                if c in hechas or h in hz.bloqueos.get(c, set()):
                    continue
                grupo = list(par) if (c in par and len(par) == 2) else [c]  # regla 3
                if any(h in hz.bloqueos.get(g, set()) for g in grupo):
                    continue
                hechas.update(grupo)
                if not any(previsto[ipp[g]] < objetivo[ipp[g]] for g in grupo):
                    continue
                req = sum(d.req[d.ipos[g]] for g in grupo)
                if np.all(req <= libre + 1e-9):
                    libre = libre - req
                    for g in grupo:
                        a[h, d.ipos[g]] = 1
                        u[h, d.ipos[g]] = 1.0
        # evolución del stock con la producción decidida
        prod = np.array([d.cap[i] * u[h, d.ipos[c]] for i, c in enumerate(prods)])
        stock = stock + prod - d.env[h]

    ix = hz.slots.index
    plan = evaluar(esc, hz, pd.DataFrame(a, index=ix, columns=todas), pd.DataFrame(u, index=ix, columns=todas),
                   estado="REFERENCIA", previo=previo)
    plan.nombre = "Plan de referencia (manual)"
    return plan
