# ADR 0003 — Como responder "o código estava vigente na data D"

Data: 2026-09-30 · Status: aceita

## Contexto

A pergunta central da API é "o código X valia na data D?", usada para conferir o código de uma guia contra a data do atendimento. Existem dois relógios para responder:

- **datas oficiais da ANS** (`inicio_vigencia`, `fim_vigencia`), que vêm no próprio registro;
- **o período publicado** na nossa base (`publicado_de`, `publicado_ate`), que registra quando vimos cada versão.

As datas da ANS não bastam sozinhas. Nos arquivos das tabelas 19 e 20, nenhum conceito tem `fim_vigencia`: a ANS retira o código da lista em vez de preencher a data de fim. Algumas tabelas também podem vir sem data de início. E nossa base só observa a ANS a partir da primeira carga (30/09/2026), mas as perguntas podem ser sobre anos antes.

## Decisão

A regra fica escrita uma vez, em `src/tuss/domain/vigencia.py`, em dois passos:

1. **Qual versão vale em D**: a que estava publicada na nossa base naquele dia (a última do dia, se houve mais de uma). Se D é anterior à primeira carga, vale a versão mais antiga que conhecemos, que é o melhor retrato disponível. Se em D o código já tinha saído da lista, nenhuma versão vale.
2. **Se está vigente**: com data de início da ANS, `inicio_vigencia <= D <= fim_vigencia` (fim vazio = sem fim), critério `oficial`. Sem data da ANS, vale o período em que vimos o código, critério `observado`. Código fora da lista em D não está vigente, com critério `observado`, porque quem viu a retirada fomos nós.

Os dias do período publicado são contados em UTC. A resposta sempre informa o `criterio` e, na validação em lote, o `motivo` (`vigente`, `antes_do_inicio`, `apos_o_fim`, `fora_da_lista`, `inexistente`).

A listagem com `vigente_em` repete a regra em SQL, por desempenho. Um teste de integração confere, em várias datas, que as duas respostas são iguais.

## Consequências

- A retirada de um código conta a partir da carga em que percebemos a ausência, não da data real da ANS. Quanto mais frequente a coleta (Fase 4), menor essa diferença.
- Para datas anteriores à primeira carga, a descrição devolvida é a mais antiga que temos, que pode não ser exatamente a daquela época. As datas oficiais da ANS continuam valendo para dizer se o código estava vigente.
- A listagem com `vigente_em` numa data em que quase nada valia percorre a tabela inteira (0,5 s na tuss-20). Na tuss-19 (1,4 milhão de códigos), isso pode passar do limite de 2 s por consulta; é preciso rever antes de carregar essa tabela.
