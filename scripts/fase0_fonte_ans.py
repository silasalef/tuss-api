"""Fase 0: descoberta da fonte oficial (API OCL da ANS).

Chama GET /ANS/source e GET /ANS/concepts/tuss-22?page=1, grava as respostas
brutas em tests/fixtures/ans/ e gera docs/fonte-ans.md com os campos reais de
um conceito, o tamanho da página e o tempo de resposta.

Só biblioteca padrão. Uso: python scripts/fase0_fonte_ans.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://consulta-ocl.apps.sa-1a.mendixcloud.com/rest/oclservice"
USER_AGENT = "tuss-api-fase0/0.1 (projeto de portfolio; sem fins comerciais)"
TIMEOUT_S = 240  # a fonte já levou ~75 s para uma página
TENTATIVAS = 2
TABELA = "tuss-22"

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = RAIZ / "tests" / "fixtures" / "ans"
DOC = RAIZ / "docs" / "fonte-ans.md"

RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def chamar(caminho: str) -> dict:
    """GET com medição de tempo; devolve status, cabeçalhos, corpo e tempos."""
    url = f"{BASE_URL}{caminho}"
    ultimo_erro = ""
    for tentativa in range(1, TENTATIVAS + 1):
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        inicio = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                corpo = resp.read()
                return {
                    "url": url,
                    "status": resp.status,
                    "cabecalhos": {k.lower(): v for k, v in resp.getheaders()},
                    "corpo": corpo,
                    "segundos": time.perf_counter() - inicio,
                    "tentativa": tentativa,
                    "falhas_anteriores": ultimo_erro,
                }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            duracao = time.perf_counter() - inicio
            ultimo_erro += f"tentativa {tentativa}: {exc!r} após {duracao:.1f}s; "
            print(f"[aviso] {url}: {exc!r} após {duracao:.1f}s", file=sys.stderr)
    raise SystemExit(f"Falha ao chamar {url}: {ultimo_erro}")


def milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def tipo(valor: object) -> str:
    return type(valor).__name__ if valor is not None else "null"


def descrever_campos(itens: list[dict]) -> list[tuple[str, str, str, str]]:
    """(campo, tipos, preenchidos, exemplo) para as chaves do conceito e de `extras`."""
    linhas = []
    chaves: list[str] = []
    for item in itens:
        for k in item:
            if k not in chaves:
                chaves.append(k)

    def resumo(nome: str, valores: list[object]) -> tuple[str, str, str, str]:
        tipos = ", ".join(sorted({tipo(v) for v in valores}))
        preenchidos = sum(1 for v in valores if v not in (None, "", "-"))
        exemplo = next((v for v in valores if v not in (None, "")), None)
        texto = json.dumps(exemplo, ensure_ascii=False)
        if len(texto) > 70:
            texto = texto[:67] + "..."
        return nome, tipos, f"{preenchidos}/{len(valores)}", texto

    for k in chaves:
        if k == "extras":
            continue
        linhas.append(resumo(k, [i.get(k) for i in itens]))
    extras = [i.get("extras") or {} for i in itens]
    chaves_extras: list[str] = []
    for e in extras:
        for k in e:
            if k not in chaves_extras:
                chaves_extras.append(k)
    for k in chaves_extras:
        linhas.append(resumo(f"extras.{k}", [e.get(k) for e in extras]))
    return linhas


def analisar_datas(itens: list[dict]) -> dict[str, dict[str, int]]:
    """Para cada campo de data em `extras`: quantos são ISO, vazios ('-') ou outro formato."""
    saida: dict[str, dict[str, int]] = {}
    for item in itens:
        for k, v in (item.get("extras") or {}).items():
            if not k.endswith(("vigencia", "implantacao")):
                continue
            cont = saida.setdefault(k, Counter())
            if isinstance(v, str) and RE_DATA.match(v):
                cont["ISO 8601 (AAAA-MM-DD)"] += 1
            elif v in ("-", "", None):
                cont["ausente (`-`, vazio ou nulo)"] += 1
            else:
                cont[f"outro formato (ex.: {v!r})"] += 1
    return {k: dict(v) for k, v in saida.items()}


def gerar_doc(fonte: dict, conceitos: dict, lista: list, itens: list[dict]) -> str:
    total_fonte = next((t.get("Total_sources") for t in lista if t.get("Codigo") == TABELA), None)
    tam_pagina = len(itens)
    paginas = conceitos["cabecalhos"].get("pages")
    ids = [i.get("id") for i in itens]
    campos = descrever_campos(itens)
    datas = analisar_datas(itens)
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    tabelas_ordenadas = sorted(lista, key=lambda t: t.get("Total_sources", 0), reverse=True)

    out: list[str] = []
    out.append("# Fonte ANS (API OCL) — resultado da Fase 0\n")
    out.append(
        f"Gerado por `scripts/fase0_fonte_ans.py` em {agora}. "
        "Não editar à mão: rode o script de novo. "
        "Respostas brutas em `tests/fixtures/ans/`.\n"
    )

    out.append("## Chamadas feitas\n")
    out.append("| Chamada | HTTP | Tempo | Bytes | Tentativas |")
    out.append("| --- | --- | --- | --- | --- |")
    for c in (fonte, conceitos):
        caminho = c["url"].removeprefix(BASE_URL)
        out.append(
            f"| `GET {caminho}` | {c['status']} | {c['segundos']:.2f} s | "
            f"{milhar(len(c['corpo']))} | {c['tentativa']} |"
        )
    out.append("")
    out.append(
        "Uma única medição por chamada: serve como ordem de grandeza, não como distribuição. "
        "Repita o script em horários diferentes antes de fixar timeouts e a janela de coleta.\n"
    )

    out.append("## Paginação\n")
    out.append(f"- Registros na página 1 de `{TABELA}`: **{tam_pagina}**.")
    out.append(f"- Cabeçalho `pages` da resposta: **{paginas}**.")
    out.append(f"- `Total_sources` em `/ANS/source` para `{TABELA}`: **{total_fonte}**.")
    if paginas and total_fonte and tam_pagina:
        try:
            cap = int(paginas) * tam_pagina
            out.append(
                f"- `pages` × tamanho da página = {cap}; "
                f"{'consistente' if cap >= total_fonte > cap - tam_pagina else 'NÃO bate'} "
                f"com o total ({total_fonte})."
            )
        except ValueError:
            pass
    out.append(
        "- A resposta é um array JSON puro; o número de páginas vem no cabeçalho `pages`, "
        "não no corpo. Não há parâmetro de tamanho de página testado.\n"
    )
    out.append("Cabeçalhos da resposta de conceitos:\n")
    out.append("```")
    for k, v in sorted(conceitos["cabecalhos"].items()):
        out.append(f"{k}: {v}")
    out.append("```\n")

    out.append("## Campos reais de um conceito\n")
    out.append(f"Analisados os {tam_pagina} conceitos da página 1 de `{TABELA}`.\n")
    out.append("| Campo | Tipo(s) | Preenchidos | Exemplo |")
    out.append("| --- | --- | --- | --- |")
    for nome, tipos, preench, exemplo in campos:
        out.append(f"| `{nome}` | {tipos} | {preench} | `{exemplo}` |")
    out.append("")
    out.append("Exemplo completo (primeiro conceito da página):\n")
    out.append("```json")
    out.append(json.dumps(itens[0], ensure_ascii=False, indent=2))
    out.append("```\n")

    out.append("## Datas de vigência\n")
    if datas:
        out.append("| Campo | Distribuição na página 1 |")
        out.append("| --- | --- |")
        for k, dist in datas.items():
            out.append(f"| `extras.{k}` | " + "; ".join(f"{n} × {r}" for r, n in dist.items()) + " |")
        out.append("")
    else:
        out.append("Nenhum campo de data encontrado em `extras`.\n")
    out.append(
        "Isto responde a pergunta central do plano (a fonte traz datas oficiais de vigência?) "
        "apenas para a amostra acima. Ausência de fim de vigência (`-`) deve ser lida como "
        "período aberto, mas confirme com mais páginas e outras tabelas antes de decidir o modelo.\n"
    )

    out.append("## Integridade da amostra\n")
    out.append(f"- Ids únicos na página: {len(set(ids))} de {len(ids)}.")
    out.append(f"- Tipo do `id`: {', '.join(sorted({tipo(i) for i in ids}))} (código TUSS como texto).")
    out.append(
        f"- Valores de `source`: {', '.join(sorted({str(i.get('source')) for i in itens}))}."
    )
    out.append("")

    out.append("## Catálogo (`/ANS/source`)\n")
    out.append(f"{len(lista)} tabelas; soma de `Total_sources`: {milhar(sum(t.get('Total_sources', 0) for t in lista))}.")
    out.append("")
    out.append("Maiores tabelas:\n")
    out.append("| Código | Descrição | Total |")
    out.append("| --- | --- | --- |")
    for t in tabelas_ordenadas[:8]:
        out.append(f"| `{t['Codigo']}` | {t['Descricao']} | {milhar(t['Total_sources'])} |")
    out.append("")

    out.append("## Ainda não verificado\n")
    out.append("- Comportamento do parâmetro `q`.")
    out.append("- Existência de URL estável para downloads em lote (tabelas 19 e 64).")
    out.append("- Tamanho de página nas demais tabelas e estabilidade da ordem entre chamadas.")
    out.append("- Limites de taxa e comportamento sob carga.")
    out.append("- Schema de `extras` nas outras tabelas (pode variar por tabela).")
    return "\n".join(out) + "\n"


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    DOC.parent.mkdir(parents=True, exist_ok=True)

    print("GET /ANS/source ...")
    fonte = chamar("/ANS/source")
    print(f"  {fonte['status']} em {fonte['segundos']:.2f}s")
    print(f"GET /ANS/concepts/{TABELA}?page=1 ...")
    conceitos = chamar(f"/ANS/concepts/{TABELA}?page=1")
    print(f"  {conceitos['status']} em {conceitos['segundos']:.2f}s")

    # Corpo bruto gravado byte a byte, antes de qualquer interpretação.
    (FIXTURES / "source.json").write_bytes(fonte["corpo"])
    (FIXTURES / f"concepts_{TABELA}_page1.json").write_bytes(conceitos["corpo"])

    meta = {
        c["url"].removeprefix(BASE_URL): {
            "status": c["status"],
            "segundos": round(c["segundos"], 3),
            "bytes": len(c["corpo"]),
            "cabecalhos": c["cabecalhos"],
            "coletado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for c in (fonte, conceitos)
    }
    (FIXTURES / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lista = json.loads(fonte["corpo"])
    itens = json.loads(conceitos["corpo"])
    if not isinstance(lista, list) or not isinstance(itens, list) or not itens:
        raise SystemExit("Formato inesperado: esperava arrays JSON não vazios.")

    DOC.write_text(gerar_doc(fonte, conceitos, lista, itens), encoding="utf-8")
    print(f"Fixtures em {FIXTURES}\nDocumento em {DOC}")


if __name__ == "__main__":
    main()
