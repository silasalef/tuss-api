# TUSS API

API de consulta às 65 tabelas TUSS da ANS com **histórico de versões** e **vigência por data**: responde não só se um código existe hoje, mas se era válido numa data específica e o que mudou entre as publicações.

> Projeto de portfólio em construção. O código é aberto; a API em execução é privada.

## Por que existe

A ANS publica as tabelas TUSS, mas só o estado atual: não guarda histórico. A API oficial leva de 2 a 3 minutos por página de 25 itens, e os arquivos em lote das tabelas grandes ficam desatualizados. Este projeto coleta, valida e versiona esses dados para que uma consulta como "o código X estava vigente em 01/03/2026?" responda em milissegundos.

A investigação da fonte está em [`docs/fonte-ans.md`](docs/fonte-ans.md) e as decisões em [`docs/adr/`](docs/adr/) (estratégia de coleta, carga inicial). O desenho completo está em [`docs/PLANEJAMENTO.md`](docs/PLANEJAMENTO.md).

## Estado

- [x] Fase 0: descoberta da fonte
- [x] Fase 1: fundação (modelo de dados e importação de arquivos)
- [ ] Fase 2: API de leitura
- [ ] Fase 3: histórico, vigência e feed de mudanças
- [ ] Fase 4: atualização automática
- [ ] Fase 5: produção

## Rodando localmente

Requer [uv](https://docs.astral.sh/uv/) e Docker.

```bash
uv sync
cp .env.example .env                           # e troque as senhas
uv run tuss token                              # gera um token; o hash vai em TUSS_API_TOKENS_SHA256 no .env
docker compose run --rm --build tuss alembic upgrade head  # sobe o PostgreSQL e cria as tabelas
docker compose run --rm tuss tuss importar /dados/arquivo.json  # importa (arquivo em ./dados)
docker compose up -d --build                   # sobe a API em 127.0.0.1:8100 (documentação em /docs)

uv run pytest                                  # todos os testes (os de integração sobem um Postgres temporário)
uv run pytest -m "not integracao"             # só os rápidos, sem Docker
uv run tuss inspecionar caminho/do/arquivo.zip # valida um arquivo baixado do portal da ANS
```

## Fonte dos dados

Terminologia Unificada da Saúde Suplementar (TUSS), publicada pela [Agência Nacional de Saúde Suplementar (ANS)](https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/codigos-da-tuss). Este projeto não é afiliado à ANS e não armazena valores ou preços de procedimentos.
