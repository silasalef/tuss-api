"""Os códigos novos aparecem sempre no começo da lista da API da ANS?

É a premissa da coleta incremental (ADR 0001): ler a partir da página 1 e parar
ao chegar em códigos já conhecidos. Teste com a tuss-20: o arquivo do portal
(`dados/tuss-2020260526.zip`, gerado em 26/05/2026) é a lista "antiga"; o que a
API tem a mais foi incluído depois. Se a premissa vale, todo código novo está nas
primeiras páginas e, dali em diante, só aparecem códigos do arquivo.

Lê uma amostra de páginas, uma requisição por vez, e gera docs/ordem-da-fonte.md
(não editar à mão). Respostas brutas em tests/fixtures/ans/ordem/.

Uso: uv run python scripts/ordem_da_fonte.py [arquivo.zip] [página ...]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from tuss.ingestion.lote import ler_lote

BASE_URL = "https://consulta-ocl.apps.sa-1a.mendixcloud.com/rest/oclservice"
USER_AGENT = "tuss-api-ordem/0.1 (projeto de portfolio; sem fins comerciais)"
TIMEOUT_S = 300
TENTATIVAS = 2
PAUSA_S = 5  # entre requisições: uma por vez, sem pressa
TABELA = "tuss-20"
PAGINAS_PADRAO = [1, 20, 40, 44, 45, 46, 47, 48, 500]

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO_PADRAO = RAIZ / "dados" / "tuss-2020260526.zip"
BRUTOS = RAIZ / "tests" / "fixtures" / "ans" / "ordem"
DOC = RAIZ / "docs" / "ordem-da-fonte.md"


def pagina(numero: int) -> tuple[list[dict[str, object]], int, float]:
    """Conceitos da página, total de páginas (cabeçalho `pages`) e segundos gastos."""
    url = f"{BASE_URL}/ANS/concepts/{TABELA}?page={numero}"
    falhas = ""
    for tentativa in range(1, TENTATIVAS + 1):
        pedido = urllib.request.Request(  # noqa: S310 (URL fixa, https)
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        inicio = time.perf_counter()
        try:
            with urllib.request.urlopen(pedido, timeout=TIMEOUT_S) as resposta:  # noqa: S310
                corpo = resposta.read()
                paginas = int(resposta.headers.get("pages", "0"))
            segundos = time.perf_counter() - inicio
            BRUTOS.mkdir(parents=True, exist_ok=True)
            (BRUTOS / f"{TABELA}_page{numero}.json").write_bytes(corpo)
            return json.loads(corpo), paginas, segundos
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            falhas += f"tentativa {tentativa}: {exc!r}; "
            print(f"[aviso] página {numero}: {exc!r}", file=sys.stderr, flush=True)
            time.sleep(30 * tentativa)
    raise SystemExit(f"Falha na página {numero}: {falhas}")


def main() -> None:
    arquivo = Path(sys.argv[1]) if len(sys.argv) > 1 else ARQUIVO_PADRAO
    numeros = [int(n) for n in sys.argv[2:]] or PAGINAS_PADRAO
    conhecidos = {conceito.codigo for conceito, _ in ler_lote(arquivo).itens}
    print(f"{len(conhecidos)} códigos no arquivo {arquivo.name}", flush=True)

    linhas = []
    total_paginas = 0
    for n in numeros:
        itens, total_paginas, segundos = pagina(n)
        novos = [i for i in itens if i["id"] not in conhecidos]
        datas = sorted(
            {str((i.get("extras") or {}).get("inicio_vigencia", "?")) for i in novos}  # type: ignore[union-attr]
        )
        linhas.append((n, len(itens), len(novos), datas, segundos))
        print(f"página {n}: {len(itens)} itens, {len(novos)} novos ({segundos:.0f} s)", flush=True)
        time.sleep(PAUSA_S)

    agora = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    tabela = "\n".join(
        f"| {n} | {itens} | {novos} | {', '.join(datas) or '—'} | {seg:.0f} s |"
        for n, itens, novos, datas, seg in linhas
    )
    DOC.write_text(
        f"# Ordem da lista na API da ANS\n\n"
        f"Gerado por `scripts/ordem_da_fonte.py` em {agora} UTC. Não editar à mão: rode o"
        f" script de novo. Respostas brutas em `tests/fixtures/ans/ordem/`.\n\n"
        f"Tabela `{TABELA}`: {total_paginas} páginas na API. Lista antiga: `{arquivo.name}`"
        f' ({len(conhecidos)} códigos). "Novos" são os códigos da página que não estão'
        f" nesse arquivo.\n\n"
        f"| Página | Itens | Novos | Início de vigência dos novos | Tempo |\n"
        f"| --- | --- | --- | --- | --- |\n{tabela}\n",
        encoding="utf-8",
    )
    print(f"Relatório em {DOC.relative_to(RAIZ)}", flush=True)


if __name__ == "__main__":
    main()
