"""Limite de anomalia: quando uma carga é estranha demais para publicar sozinha.

A ANS muda pouco de uma vez. Se uma carga remove muitos códigos, o mais provável é
problema na coleta (fonte devolvendo lista pela metade, arquivo cortado), não uma
decisão da ANS. Nesse caso a carga fica retida até alguém aprovar pelo CLI.
Sem banco, HTTP nem framework.
"""

from __future__ import annotations

MAX_REMOVIDOS = 0.02  # remoções acima de 2% da tabela
MAX_QUEDA = 0.01  # tabela encolhendo mais de 1%


def motivo_para_reter(publicados_antes: int, removidos: int, publicados_depois: int) -> str | None:
    """Por que reter a carga, ou `None` se ela pode ser publicada sozinha."""
    if publicados_antes == 0:
        return None
    if removidos > publicados_antes * MAX_REMOVIDOS:
        return (
            f"remove {removidos} de {publicados_antes} conceitos"
            f" ({removidos / publicados_antes:.1%}; limite {MAX_REMOVIDOS:.0%})"
        )
    queda = (publicados_antes - publicados_depois) / publicados_antes
    if queda > MAX_QUEDA:
        return (
            f"tabela cai de {publicados_antes} para {publicados_depois} conceitos"
            f" ({queda:.1%}; limite {MAX_QUEDA:.0%})"
        )
    return None
