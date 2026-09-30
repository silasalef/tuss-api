"""Regras de vigência. Sem banco, HTTP nem framework.

Nesta fase só o critério; "vigente em uma data" e o histórico chegam na Fase 3.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

Criterio = Literal["oficial", "observado"]


def criterio(inicio_vigencia: date | None) -> Criterio:
    """De onde vem a vigência de uma versão.

    `oficial`: a ANS informou a data de início. `observado`: a ANS não informou,
    então a vigência é o período em que o código apareceu nas nossas cargas.
    """
    return "oficial" if inicio_vigencia is not None else "observado"
