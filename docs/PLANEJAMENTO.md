# TUSS API — Planejamento

Sep 29, 2026 · @Silas

## Visão geral

Uma API de consulta às 65 tabelas TUSS com histórico de versões, vigência por data e feed de mudanças, alimentada pela fonte oficial da ANS. É um projeto de portfólio, sem cobrança: o código é público no GitHub, mas a API em execução é privada (só o dono acessa). A prioridade é ser estável, seguro e rápido com o menor número de peças possível: cada componente precisa se justificar.

O que ela resolve:

- Saber se um código era válido em uma data específica, não só se existe hoje.
- Ver o que mudou entre duas cargas, sem comparar planilhas na mão.
- Consultar o que mudou desde uma data (feed de mudanças), em vez de descobrir pela glosa.
- Buscar procedimentos por texto, tolerando acento e erro de digitação.

Escopo do MVP: todas as tabelas publicadas pela ANS no OCL, incluindo as grandes (19 e 64).

Fora do escopo: valores e preços (CBHPM/AMB têm direitos autorais), dados de beneficiários ou guias reais, interface web (só API e documentação OpenAPI), validação completa de guia TISS, webhooks e servidor MCP (ficam para depois do MVP).

## A fonte oficial (ANS)

A ANS entrega só o estado atual, sem histórico: versionamento e vigência são construídos do nosso lado, a partir de snapshots de cada coleta. Detalhes medidos em `docs/fonte-ans.md` e nas fixtures de `tests/fixtures/ans/`.

| Recurso | O que entrega | O que a Fase 0 mostrou |
| --- | --- | --- |
| `GET /ANS/source` | Catálogo: `Codigo`, `Descricao`, `Total_sources` | Rápido (0,3 s). `Total_sources` pode estar desatualizado: tuss-20 informa 43.376, a API pagina ~44,5 mil |
| `GET /ANS/concepts/{tabela}?page=&q=` | Conceitos paginados (25 por página; total de páginas no cabeçalho `pages`) | ~2 a 3 min por requisição, mesmo numa tabela de 2 itens. `q` filtra por texto e por código, não por data. `limit` e `updatedSince` são ignorados. Página 1 traz os conceitos mais recentes; a ordem é estável entre leituras, mas não é ordem exata de código |
| Download no portal (`/p/ocl`) | JSON ou CSV por tabela | Tabelas pequenas: gerado na hora. Tabelas 19 e 20: ZIP pré-gerado de 26/05/2026, desatualizado em relação à API. Sem URL fixa (sessão Mendix, link de uso único) |

Formato de um conceito (igual na API e no download): `id` (texto), `source`, `display_name` e `extras` com `inicio_vigencia`, `fim_vigencia` (`-` = aberto), `fim_implantacao` e campos específicos por tabela (ex.: `laboratorio`, `apresentacao`, `registro_anvisa` na 20; `fabricante`, `modelo`, `classe_risco` na 19). As datas oficiais de vigência existem, então o critério padrão é o oficial.

Estratégia:

- Carga inicial: importar o arquivo baixado do portal por um comando de CLI (`tuss importar arquivo.zip`). Download feito à mão, uma vez por tabela: sem automatizar a sessão Mendix, que é frágil.
- Atualização: API paginada, uma requisição por vez por tabela. Para tabelas grandes, coleta incremental: ler a partir da página 1 (mais recentes) até encontrar só conceitos já conhecidos. Varredura completa, que detecta alterações e remoções, fica em ciclo longo ou por reimportação de arquivo novo.
- Toda coleta vira snapshot bruto com SHA-256. Nenhuma consulta de cliente chama a ANS.
- Cliente educado: backoff com jitter, timeout de 5 min por requisição e User-Agent com nome do projeto.

## Arquitetura

Dois processos e um banco, em Docker Compose, num servidor Linux pequeno (VPS) que pode ser compartilhado com outros serviços:

```
você ──túnel SSH──> API (FastAPI, só leitura, 127.0.0.1:8100) ──> PostgreSQL <── worker (coleta e publica) ──> ANS
                                                                      CLI (importar, aprovar, reprocessar)
```

- O tuss-api não publica nenhuma porta na internet: a API escuta só em `127.0.0.1` da VPS e o Postgres não publica porta alguma. O acesso é por túnel SSH (`ssh -L 8100:localhost:8100 ...`), criptografado e autenticado pela mesma chave do SSH. Não há VPN nem proxy: para uso por uma pessoa só, o túnel SSH resolve com uma peça a menos.
- Cada container tem limite de memória, para uma importação pesada não tirar memória de outros serviços do servidor.
- A API nunca chama a ANS: responde da última carga publicada e informa a data dela. A lentidão da fonte não chega ao cliente.
- O worker roda as coletas agendadas; o CLI faz as operações manuais. Não existe rota HTTP de administração, o que tira uma superfície de ataque inteira.
- Os eventos de mudança são gravados na mesma transação da publicação: nenhuma mudança fica sem evento.

## Pipeline de ingestão

Coleta ou importação → snapshot bruto (arquivo local comprimido + SHA-256) → staging → validação → limite de anomalia → publicação atômica. Só a publicação escreve no que a API lê.

- Coleta retomável: checkpoint por página na `carga`; se a fonte cair, retoma de onde parou.
- Sem mudança: hash do snapshot igual ao da última carga só registra a verificação.
- Validação que falha alto: campos obrigatórios, tipos, datas válidas, códigos duplicados. Comparação com `Total_sources` só como aviso, porque o catálogo pode estar desatualizado.
- Limite de anomalia: remoções acima de 2% da tabela ou queda de contagem acima de 1% retêm a carga; aprovação pelo CLI. Dentro do limite, publica sozinho.
- Publicação atômica: fecha as versões alteradas e removidas, insere as novas e grava os eventos numa única transação.
- Só uma carga por tabela por vez (advisory lock no Postgres).

## Modelo de dados

O modelo é bitemporal e nada é apagado: cada versão guarda a vigência oficial (datas da ANS) e o período em que esteve publicada na nossa base.

| Tabela no banco | Para que serve | Campos principais |
| --- | --- | --- |
| `tabela_tuss` | Catálogo das 65 tabelas | `codigo` (tuss-22), `numero`, `descricao`, `total_fonte`, `ultima_sync_em`, `carga_atual_id` |
| `carga` | Cada coleta ou importação | `status`, `origem` (api ou arquivo), `sha256_snapshot`, `checkpoint`, `total`, `incluidos`, `alterados`, `removidos`, `erro` |
| `conceito` | Identidade estável de um código | `tabela_id`, `codigo` (texto), único por tabela |
| `conceito_versao` | Cada estado de um conceito no tempo | `descricao`, `atributos` (jsonb com os extras da fonte), `hash_conteudo`, `inicio_vigencia`, `fim_vigencia`, `fim_implantacao`, `publicado_de`, `publicado_ate`, `carga_id` |
| `evento_mudanca` | Inclusões, alterações, remoções e reativações | `tipo`, `campos_alterados`, `antes`, `depois`, `carga_id` |
| `stg_conceito` | Rascunho da carga em andamento (UNLOGGED) | `carga_id`, `codigo`, `bruto` (jsonb), campos normalizados |

Regras do modelo:

- Código é texto, nunca número: zeros à esquerda importam.
- Versão atual é a que tem `publicado_ate` nulo. Uma restrição de exclusão (`btree_gist`) impede duas versões sobrepostas do mesmo conceito.
- Mudança é `hash_conteudo` diferente (SHA-256 dos campos normalizados, em ordem fixa). Espaço extra ou caixa diferente não contam.
- Remoção fecha `publicado_ate` e gera evento `removido`. Se o código voltar, nasce nova versão com evento `reativado`.
- "Vigente em D" usa `inicio_vigencia` e `fim_vigencia`; sem datas, usa o período publicado. A resposta informa `criterio: oficial` ou `observado`.

## Design da API

API REST em `/v1`, somente leitura, privada (token obrigatório), com OpenAPI gerado pelo FastAPI.

| Rota | O que faz |
| --- | --- |
| `GET /v1/tabelas` | Lista tabelas com contagem e última sincronização |
| `GET /v1/tabelas/{tabela}/conceitos` | Lista e busca (`q`, `vigente_em`, `cursor`, `limite`) |
| `GET /v1/tabelas/{tabela}/conceitos/{codigo}` | Estado atual, ou numa data com `?em=AAAA-MM-DD` |
| `GET /v1/tabelas/{tabela}/conceitos/{codigo}/historico` | Todas as versões do código |
| `POST /v1/validacoes` | Até 100 pares tabela + código + data: vigente ou não, e por quê |
| `GET /v1/mudancas?desde=&tabela=&tipo=` | Feed de mudanças, paginado por cursor |
| `GET /v1/status` | Última sincronização por tabela |
| `/health/live`, `/health/ready` | Saúde, só na rede interna |

Convenções:

- Paginação por cursor (keyset em `codigo`), `limite` padrão 50 e máximo 200. Offset não escala em 1,6 milhão de linhas.
- Erros em Problem Details (RFC 9457), com `request_id`.
- Toda resposta de conceito informa `carga_id`, `sincronizado_em` e `criterio`.
- `ETag` e `Cache-Control` nos GETs; `If-None-Match` devolve 304. As tabelas mudam pouco, então o cache rende bastante.
- Vigências são `date` (sem fuso); carimbos de sistema em UTC.
- `{tabela}` aceita `tuss-22` ou `22`; fora do catálogo, 404.

Exemplo (valores ilustrativos):

```json
{
  "tabela": "tuss-22",
  "codigo": "10101012",
  "em": "2026-08-01",
  "vigente": true,
  "criterio": "oficial",
  "periodo": { "inicio": "2020-01-01", "fim": null },
  "carga": { "id": 1842, "sincronizado_em": "2026-09-29T06:10:00Z" }
}
```

## Limites e desempenho

A API é privada, então os limites não existem para barrar o público: protegem o servidor de um script seu em loop e mostram o controle funcionando. Valem por token.

| Limite | Valor inicial |
| --- | --- |
| Consultas por token | 60 por minuto |
| Busca (`q`) e validação em lote por token | 20 por minuto |
| `limite` por página | máx. 200 |
| `q` | 3 a 100 caracteres, sempre dentro de uma tabela |
| Corpo de requisição | máx. 64 KB |
| Tempo de consulta no banco | `statement_timeout` de 2 s no papel da API |

- Rate limit em memória no processo da API (limitador próprio em `api/limites.py`, janela deslizante de 60 s por token e por tipo de chamada), com a API rodando em um processo só. A slowapi foi descartada na Fase 2: ela limita por rota, e busca e listagem são a mesma rota (muda só o `q`). Se um dia precisar de mais processos, o contador passa para o Redis; até lá, é uma peça a menos.
- Excedeu: 429 com `Retry-After`.
- Metas: consulta por código p95 < 150 ms; busca p95 < 400 ms. Medidas com um teste de carga (k6) antes do deploy e na Fase 4.
- Busca no próprio Postgres, sem Elasticsearch: tsvector `portuguese` + `unaccent` (via wrapper `IMMUTABLE`), `pg_trgm` para erro de digitação e `text_pattern_ops` para prefixo de código. Ranking: código exato, prefixo, full-text, trigrama.

## Segurança

Baseada no OWASP API Security Top 10 (2023). Defesa em camadas: mesmo que uma falhe, a seguinte segura.

- Rede: firewall do provedor (fora da VM, então vale também para portas abertas pelo Docker) fecha tudo o que não for necessário; API e banco do tuss-api nem chegam a publicar porta. SSH só com chave, senha desligada.
- Acesso: token obrigatório em toda rota (`Authorization: Bearer`), guardado na API só como hash; comparação em tempo constante. Um token por dispositivo, revogável trocando a variável de ambiente.

- Superfície mínima: só GETs e uma validação em lote; nenhuma rota de escrita ou de administração exposta.
- Papéis de banco separados: `api` só lê (não consegue escrever em nada) e `ingestao` escreve. Migrations rodam com o dono do banco.
- Entrada validada: schemas de saída explícitos (nunca o modelo do banco); entrada com `extra="forbid"`. `POST /v1/validacoes` aceita só tabela, código e data, para ninguém mandar dado de beneficiário por engano (LGPD).
- Consumo limitado: rate limit, tamanho de página, tamanho de corpo, `statement_timeout`.
- Fonte tratada como entrada não confiável: allowlist de host, TLS verificado, limite de tamanho e de descompressão do ZIP, validação de schema.
- SSH só com chave (senha desligada), usuário sem root, atualizações automáticas de segurança do sistema.
- Deploy: sem proxy nem TLS próprios (o túnel SSH já criptografa), CORS fechado, container com usuário não-root, Postgres sem porta publicada.
- Segredos só em variáveis de ambiente; `.env.example` no repositório; gitleaks no pre-commit e no CI.
- Dependências travadas no `uv.lock`, Dependabot e pip-audit.

## Operação

- Logs estruturados em JSON, com `request_id` na API e `carga_id` no worker.
- `GET /v1/status` mostra a última sincronização de cada tabela: é a prova de que os dados estão atualizados.
- Alerta mínimo: o worker avisa um serviço de heartbeat externo (ex.: healthchecks.io) ao fim de cada ciclo; sem sinal em 48 h, chega e-mail. Como a saída é do servidor para fora, isso funciona sem abrir porta nenhuma.
- Backup: dump diário do Postgres e snapshots brutos em disco, copiados para fora do servidor. Os snapshots permitem reconstruir a base por replay; testar a restauração uma vez antes do deploy.
- Runbooks curtos em `docs/runbooks/`: fonte fora do ar, carga retida.

## Testes e qualidade

O que mais precisa de teste é a lógica temporal e o diff: é ali que um bug vira informação errada de vigência sem nenhum erro aparente.

| Camada | O que cobre | Ferramenta |
| --- | --- | --- |
| Unitário | Normalização, hash, diff, regras de vigência (mesmo dia, período aberto, reativação) | pytest |
| Propriedade | Aplicar o diff de A para B sobre A sempre resulta em B | hypothesis |
| Contrato com a fonte | Respostas reais da ANS gravadas como fixtures | pytest |
| Integração | Migrations, publicação SCD2 e restrição de exclusão num Postgres real | testcontainers |
| API | Paginação, Problem Details, limites (429), validação de entrada | httpx + pytest |
| Carga | Consulta e busca sob concorrência, contra as metas de p95 | k6 |

CI no GitHub Actions: ruff, mypy, testes, gitleaks e pip-audit; merge só com tudo verde. Meta de cobertura de 90% em `domain/` e `ingestion/`, sem meta global.

## Stack e estrutura do repositório

| Peça | Escolha | Por quê |
| --- | --- | --- |
| Linguagem e dependências | Python 3.13+, uv | Lockfile rápido e reprodutível |
| API | FastAPI + Pydantic v2 | Validação e OpenAPI sem esforço extra |
| Banco | PostgreSQL 17+ com `pg_trgm`, `unaccent`, `btree_gist` | Busca, temporalidade e integridade no mesmo lugar |
| Acesso a dados | SQLAlchemy 2 (async) + asyncpg, Alembic | Migrations versionadas |
| Rate limit | Limitador próprio em memória (~30 linhas) | Limite diferente para busca e consulta na mesma rota; sem Redis enquanto houver um processo |
| Cliente HTTP da fonte | httpx + tenacity | Timeout, retry com backoff e jitter |
| Agenda do worker | Laço simples em `worker.py` (esperar a hora, rodar, repetir) | Uma tarefa só, uma vez por dia; o próprio ciclo retoma o que ficou pela metade. O APScheduler foi descartado na Fase 4: seria uma dependência a mais sem ganho |
| CLI | Typer | Importar, aprovar e reprocessar à mão |

```
tuss-api/
├── src/tuss/
│   ├── api/            # rotas v1, schemas de entrada e saída, limites
│   ├── domain/         # vigência, diff, normalização (sem I/O)
│   ├── ingestion/      # cliente ANS, importação de arquivo, validação, publicação
│   ├── db/             # modelos, consultas, sessão
│   ├── worker.py       # agenda das coletas
│   ├── cli.py
│   └── settings.py
├── migrations/
├── tests/              # unit, integration, api, fixtures/ans
├── docs/               # plano, fonte, adr, runbooks
├── compose.yaml
├── Dockerfile
└── pyproject.toml
```

Regra de arquitetura: `domain/` não importa nada de banco, HTTP ou framework, o que mantém a lógica de vigência testável isolada. Decisões relevantes viram ADR curto em `docs/adr/`.

## Roadmap por fases

1. Fase 0: descoberta da fonte (concluída; decisão no ADR 0001, ordem da página 1 confirmada em 30/09/2026 em `docs/ordem-da-fonte.md`).
   - Saída: `docs/fonte-ans.md` atualizado, fixtures das tabelas 22, 23 e 20, ADR da estratégia de coleta.
2. Fase 1: fundação. Repositório, CI, Compose, migrations, modelo de dados e `tuss importar` para arquivos do portal.
   - Saída: tabelas 22 e 20 importadas e consultáveis por SQL, com snapshot e carga registrados.
3. Fase 2: API de leitura. Tabelas, conceitos, busca, cursor, Problem Details, limites e cache.
   - Saída: endpoints com testes, OpenAPI publicado e p95 dentro da meta num teste local.
4. Fase 3: tempo e mudança. Versionamento SCD2, diff, histórico, vigência por data, validação em lote e feed de mudanças.
   - Saída: duas cargas simuladas por fixture produzem histórico, eventos e respostas de vigência corretos.
5. Fase 4: atualização automática. Coleta pela API (incremental nas grandes), checkpoints, limite de anomalia, aprovação pelo CLI e heartbeat.
   - Saída: as 65 tabelas carregadas e sincronizando sozinhas por 7 dias seguidos, sem intervenção.
6. Fase 5: produção e vitrine. Deploy na VPS, backup testado, teste de carga e README de portfólio.
   - Saída: API rodando na VPS, acessível só por túnel SSH, com `/v1/status` mostrando as sincronizações; README com exemplos reais de resposta e o OpenAPI versionado no repositório, para quem visita o GitHub ver o resultado sem acessar o servidor.

Depois do MVP: servidor MCP (assistentes de IA consultando as tabelas), webhooks de aviso de mudança, siglas e sinônimos na busca, abrir a API ao público (com chaves por cliente) se fizer sentido, exportação como FHIR CodeSystem.

## Riscos e decisões em aberto

O maior risco é a própria fonte: lenta, sem histórico, com arquivos em lote desatualizados e catálogo que pode não bater com os dados.

| Risco | Impacto | Mitigação |
| --- | --- | --- |
| Fonte lenta (~2 min por página) ou fora do ar | Dados atrasados | Coleta incremental, checkpoint, backoff; a API responde da última carga e informa a data |
| Página 1 deixar de trazer os mais recentes | Coleta incremental perde inclusões | Confirmado em 30/09/2026 que traz (`docs/ordem-da-fonte.md`); a varredura completa periódica pega o que escapar; plano B é varredura completa das tabelas pequenas e médias e reimportação de arquivo para 19 e 64 |
| Alterações e remoções nas tabelas grandes | Só aparecem na varredura completa | Varredura em ciclo longo; datas de vigência da ANS continuam valendo entre varreduras |
| Schema do conceito muda sem aviso | Carga com dado errado | Validação estrita; carga fica retida em vez de publicar |
| Licença de uso dos dados | Exposição pública indevida | Portal gov.br usa CC BY-ND 3.0; creditar a ANS. Só vira questão se a API for aberta ao público; o código no GitHub e as fixtures pequenas não dependem disso |
| Direitos da CBHPM/AMB | Problema legal | Não armazenar nem servir valores |

Decisões em aberto:

- [x] Coleta incremental das tabelas grandes: a página 1 traz os mais recentes (confirmado em 30/09/2026 na tuss-20, `docs/ordem-da-fonte.md`).

Decisões tomadas em 29/09/2026: código público e API privada (túnel SSH + token), sem acesso de terceiros por enquanto; hospedagem em VPS própria, com Postgres local em Docker Compose (planos gratuitos de Postgres gerenciado limitam o banco a poucas centenas de MB); publicação automática dentro dos limites de anomalia, com aprovação manual acima deles; carga inicial por arquivo do portal; webhooks e MCP fora do MVP.

## Fontes

- [Códigos da TUSS — ANS](https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/codigos-da-tuss)
- [Portal de consulta OCL — ANS](https://consulta-ocl.apps.sa-1a.mendixcloud.com/p/ocl)
- [OclService — lista de tabelas (JSON)](https://consulta-ocl.apps.sa-1a.mendixcloud.com/rest/oclservice/ANS/source)
- [OclService — especificação OpenAPI](https://consulta-ocl.apps.sa-1a.mendixcloud.com/rest-doc/rest/oclservice/openapi.json)
