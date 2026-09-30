"""Rotas /v1. Todas exigem token."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.api.dependencias import conexao
from tuss.api.schemas import Status, StatusTabela, UltimaCarga
from tuss.api.seguranca import exigir_token
from tuss.db import consultas

rotas = APIRouter(prefix="/v1", dependencies=[Depends(exigir_token)])

Conexao = Annotated[AsyncConnection, Depends(conexao)]


@rotas.get("/status", summary="Última sincronização de cada tabela")
async def status(con: Conexao) -> Status:
    """Mostra, por tabela, a carga que a API está servindo e a tentativa de carga mais recente.

    É a forma de saber se os dados estão atualizados e se alguma carga falhou.
    """
    linhas = await consultas.status_das_tabelas(con)
    return Status(
        tabelas=[
            StatusTabela(
                tabela=linha.codigo,
                descricao=linha.descricao,
                conceitos=linha.conceitos,
                carga_id=linha.carga_atual_id,
                sincronizado_em=linha.ultima_sync_em,
                ultima_carga=None
                if linha.ultima_id is None
                else UltimaCarga(
                    id=linha.ultima_id,
                    status=linha.ultima_status,
                    iniciada_em=linha.ultima_iniciada_em,
                    finalizada_em=linha.ultima_finalizada_em,
                    erro=linha.ultima_erro,
                ),
            )
            for linha in linhas
        ]
    )
