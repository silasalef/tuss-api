"""O ciclo diário do worker: catálogo e depois cada tabela, uma de cada vez.

Política de cada tabela (`escolher_modo`, ADR 0001):

- tabela pequena (até 2 páginas): sempre completa, que custa o mesmo que ler o topo
  e ainda detecta remoções;
- até 2.000 páginas (a 20 tem ~1.800): incremental todo dia e completa a cada 30 dias.
  Nenhuma execução lê mais de 300 páginas (~5 h): a completa de uma tabela maior que
  isso é dividida em trechos, um por ciclo, retomando de onde parou;
- maior que isso (19 e 64): fora do ciclo. A primeira carga vem de arquivo do portal
  (`tuss importar`) e a incremental fica com o serviço de recuperação
  (`tuss recuperar`), que roda o tempo todo em trechos: o arquivo ficou meses atrás da
  API e alcançar a diferença leva dias.

Ordem: da menor tabela para a maior, e as leituras completas longas por último.
Assim as tabelas rápidas ficam prontas cedo e uma tabela demorada não segura as outras.
Uma tabela com problema não impede as outras. Se a ANS parar de responder em 3
tabelas seguidas, o ciclo termina: ela está fora do ar, e insistir não ajuda.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import text

from tuss.config import PAPEL_INGESTAO, Config
from tuss.db.conexao import criar_engine
from tuss.ingestion.catalogo import sincronizar_catalogo
from tuss.ingestion.coleta import ColetaRecusada, Modo, coletar
from tuss.ingestion.fonte import ClienteANS, FonteIndisponivel

log = structlog.get_logger("tuss.worker")

POR_PAGINA = 25
PAGINAS_SEMPRE_COMPLETA = 2
PAGINAS_POR_EXECUCAO = 300  # ~5 h de leitura; também o limite para a primeira carga pela API
MAX_PAGINAS_COMPLETA = 2000  # acima disso, a leitura completa levaria semanas
DIAS_ENTRE_COMPLETAS = 30
MAX_FALHAS_DA_FONTE_SEGUIDAS = 3


@dataclass(frozen=True, slots=True)
class SituacaoTabela:
    codigo: str
    paginas: int  # estimativa pelo total do catálogo
    carregada: bool
    ultima_completa_em: datetime | None  # coleta completa ou importação de arquivo
    completa_interrompida: bool


@dataclass(frozen=True, slots=True)
class ResultadoTabela:
    tabela: str
    status: str  # publicada, sem_mudanca, retida, em_andamento, pulada ou falhou
    detalhe: str = ""


def escolher_modo(t: SituacaoTabela, agora: datetime) -> tuple[Modo | None, str]:
    """Como coletar a tabela hoje (`None` = não coletar) e por quê."""
    if t.completa_interrompida:
        return "completa", "retomando coleta completa interrompida"
    if not t.carregada:
        if t.paginas > PAGINAS_POR_EXECUCAO:
            return None, f"~{t.paginas} páginas: carga inicial por arquivo (tuss importar)"
        return "completa", "primeira carga"
    if gigante(t):
        return None, "coletada pelo serviço de recuperação (tuss recuperar)"
    if t.paginas <= PAGINAS_SEMPRE_COMPLETA:
        return "completa", "tabela pequena"
    vencida = t.ultima_completa_em is None or agora - t.ultima_completa_em > timedelta(
        days=DIAS_ENTRE_COMPLETAS
    )
    if t.paginas <= MAX_PAGINAS_COMPLETA and vencida:
        return "completa", f"última completa há mais de {DIAS_ENTRE_COMPLETAS} dias"
    return "incremental", "só o topo da lista"


async def executar_ciclo(
    cliente: ClienteANS, config: Config, agora: datetime
) -> list[ResultadoTabela]:
    """Um ciclo inteiro. Devolve o que aconteceu com cada tabela."""
    itens = await cliente.catalogo()
    novas = await sincronizar_catalogo(itens, config)
    log.info("catalogo", tabelas=len(itens), novas=novas)

    resultados = []
    falhas_da_fonte = 0
    situacoes = await carregar_situacoes(config)
    # Da menor para a maior, completas longas por último (ver docstring do módulo).
    situacoes.sort(key=lambda t: (_longa(t, agora), t.paginas))
    for tabela in situacoes:
        modo, motivo = escolher_modo(tabela, agora)
        if modo is None:
            resultados.append(ResultadoTabela(tabela.codigo, "pulada", motivo))
            log.info("tabela_pulada", tabela=tabela.codigo, motivo=motivo)
            continue
        log.info("coleta_inicio", tabela=tabela.codigo, modo=modo, motivo=motivo)
        try:
            r = await coletar(
                tabela.codigo, cliente, config, modo=modo, max_paginas=PAGINAS_POR_EXECUCAO
            )
        except FonteIndisponivel as exc:
            falhas_da_fonte += 1
            resultados.append(ResultadoTabela(tabela.codigo, "falhou", str(exc)))
            log.warning("fonte_indisponivel", tabela=tabela.codigo, erro=str(exc))
            if falhas_da_fonte >= MAX_FALHAS_DA_FONTE_SEGUIDAS:
                log.error("ciclo_interrompido", motivo="ANS fora do ar")
                break
            continue
        except ColetaRecusada as exc:
            resultados.append(ResultadoTabela(tabela.codigo, "pulada", str(exc)))
            log.info("tabela_pulada", tabela=tabela.codigo, motivo=str(exc))
            continue
        except Exception as exc:  # dado inválido, banco...: registra e segue para a próxima
            resultados.append(ResultadoTabela(tabela.codigo, "falhou", repr(exc)))
            log.exception("coleta_falhou", tabela=tabela.codigo)
            continue
        parou_por_falha = r.status == "em_andamento" and r.paginas_lidas < PAGINAS_POR_EXECUCAO
        falhas_da_fonte = falhas_da_fonte + 1 if parou_por_falha else 0
        resultados.append(ResultadoTabela(tabela.codigo, r.status, r.motivo or ""))
        log.info(
            "coleta_fim",
            tabela=tabela.codigo,
            carga_id=r.carga_id,
            modo=r.modo,
            status=r.status,
            paginas=r.paginas_lidas,
            **(r.contagem or {}),
        )
        if falhas_da_fonte >= MAX_FALHAS_DA_FONTE_SEGUIDAS:
            log.error("ciclo_interrompido", motivo="ANS fora do ar")
            break
    return resultados


def _longa(t: SituacaoTabela, agora: datetime) -> bool:
    return escolher_modo(t, agora)[0] == "completa" and t.paginas > PAGINAS_POR_EXECUCAO


def gigante(t: SituacaoTabela) -> bool:
    """Grande demais para a API inteira (19 e 64): vive no serviço de recuperação."""
    return t.paginas > MAX_PAGINAS_COMPLETA


async def carregar_situacoes(config: Config) -> list[SituacaoTabela]:
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.connect() as con:
            linhas = await con.execute(
                text("""
                SELECT t.codigo, t.total_fonte, t.carga_atual_id IS NOT NULL AS carregada,
                       (SELECT max(c.finalizada_em) FROM carga c
                        WHERE c.tabela_id = t.id AND c.modo = 'completa'
                          AND c.status IN ('publicada', 'sem_mudanca')) AS ultima_completa_em,
                       EXISTS (SELECT 1 FROM carga c
                               WHERE c.tabela_id = t.id AND c.status = 'em_andamento'
                                 AND c.origem = 'api' AND c.modo = 'completa')
                           AS completa_interrompida
                FROM tabela_tuss t
                ORDER BY t.numero::integer
                """)
            )
            return [
                SituacaoTabela(
                    linha.codigo,
                    max(1, math.ceil((linha.total_fonte or 0) / POR_PAGINA)),
                    linha.carregada,
                    linha.ultima_completa_em,
                    linha.completa_interrompida,
                )
                for linha in linhas
            ]
    finally:
        await engine.dispose()
