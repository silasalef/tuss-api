# TUSS API

API de consulta às 65 tabelas TUSS da ANS com **histórico de versões** e **vigência por data**: responde não só se um código existe hoje, mas se era válido numa data específica e o que mudou entre as publicações.

> Projeto de portfólio em construção. O código é aberto; a API em execução é privada.

## Por que existe

A ANS publica as tabelas TUSS, mas só o estado atual: não guarda histórico. A API oficial leva de 2 a 3 minutos por página de 25 itens, e os arquivos em lote das tabelas grandes ficam desatualizados. Este projeto coleta, valida e versiona esses dados para que uma consulta como "o código X estava vigente em 01/03/2026?" responda em milissegundos.

A investigação da fonte está em [`docs/fonte-ans.md`](docs/fonte-ans.md) e as decisões em [`docs/adr/`](docs/adr/) (estratégia de coleta, carga inicial, vigência numa data). O desenho completo está em [`docs/PLANEJAMENTO.md`](docs/PLANEJAMENTO.md).

## Estado

- [x] Fase 0: descoberta da fonte
- [x] Fase 1: fundação (modelo de dados e importação de arquivos)
- [x] Fase 2: API de leitura
- [x] Fase 3: histórico, vigência e feed de mudanças
- [ ] Fase 4: atualização automática
- [ ] Fase 5: produção

## A API

Contrato completo em [`docs/openapi.json`](docs/openapi.json) (OpenAPI 3.1). Toda rota `/v1` exige `Authorization: Bearer <token>`.

| Rota | O que faz |
| --- | --- |
| `GET /v1/tabelas` | Tabelas carregadas, com contagem e data da última sincronização |
| `GET /v1/tabelas/{tabela}/conceitos` | Lista paginada por cursor; com `q`, busca por código ou texto; com `vigente_em`, só os vigentes numa data |
| `GET /v1/tabelas/{tabela}/conceitos/{codigo}` | Um código, com vigência e atributos; com `?em=AAAA-MM-DD`, como era nessa data |
| `GET /v1/tabelas/{tabela}/conceitos/{codigo}/historico` | Todas as versões do código e quando cada uma valeu |
| `POST /v1/validacoes` | Até 100 itens de tabela + código + data: vigente ou não, e por quê |
| `GET /v1/mudancas` | Feed de inclusões, alterações, remoções e reativações (`desde`, `tabela`, `tipo`) |
| `GET /v1/status` | Última carga de cada tabela e o que ela mudou, inclusive falhas |

Exemplo real (`GET /v1/tabelas/22/conceitos/10101012?em=2026-03-01`):

```json
{
  "codigo": "10101012",
  "descricao": "Consulta em consultório (no horário normal ou preestabelecido)",
  "inicio_vigencia": "2009-02-13",
  "fim_vigencia": null,
  "fim_implantacao": "2010-10-15",
  "atributos": {},
  "criterio": "oficial",
  "tabela": "tuss-22",
  "carga_id": 1,
  "sincronizado_em": "2026-09-30T13:10:04.245439Z",
  "em": "2026-03-01",
  "vigente": true
}
```

`criterio: oficial` indica que a vigência vem das datas da ANS; sem elas, vale o período em que o código apareceu nas cargas (`observado`). Como a ANS retira códigos da lista em vez de preencher a data de fim, cada nova carga é comparada com a anterior: nada é apagado, a versão antiga é fechada e a mudança entra no feed. A regra completa está no [ADR 0003](docs/adr/0003-vigencia-numa-data.md).

A busca (`q`) aceita código ou começo de código (`1010`, `1.01.01.01-2`) e texto sem acento, tolerando erro de digitação: `consluta` encontra "Consulta em consultório".

### Desempenho

Teste de carga com k6 (`scripts/teste_de_carga.sh`), na VPS de desenvolvimento (4 GB, ARM64, compartilhada), com as tabelas 20 e 22 (49 mil conceitos), 30/09/2026:

| Chamada | Meta (p95) | Medido (p95) |
| --- | --- | --- |
| Consulta por código | < 150 ms | 48 ms |
| Busca por texto ou código | < 400 ms | 52 ms |

Cerca de 130 requisições por segundo, sem erros em 8 mil chamadas.

## Rodando localmente

Requer [uv](https://docs.astral.sh/uv/) e Docker.

```bash
uv sync
cp .env.example .env                           # e troque as senhas
uv run tuss token                              # gera um token; o hash vai em TUSS_API_TOKENS_SHA256 no .env
docker compose run --rm --build tuss alembic upgrade head  # sobe o PostgreSQL e cria as tabelas
docker compose run --rm tuss tuss importar /dados/arquivo.json  # carga inicial ou atualização (arquivo em ./dados)
docker compose up -d --build                   # sobe a API em 127.0.0.1:8100 (documentação em /docs)

uv run pytest                                  # todos os testes (os de integração sobem um Postgres temporário)
uv run pytest -m "not integracao"             # só os rápidos, sem Docker
uv run tuss inspecionar caminho/do/arquivo.zip # valida um arquivo baixado do portal da ANS
scripts/teste_de_carga.sh                      # mede o desempenho contra as metas (k6)
uv run tuss openapi > docs/openapi.json        # atualiza o contrato da API
```

## Fonte dos dados

Terminologia Unificada da Saúde Suplementar (TUSS), publicada pela [Agência Nacional de Saúde Suplementar (ANS)](https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/codigos-da-tuss). Este projeto não é afiliado à ANS e não armazena valores ou preços de procedimentos.
