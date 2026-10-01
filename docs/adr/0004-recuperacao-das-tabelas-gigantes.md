# ADR 0004 — Recuperação das tabelas gigantes (19 e 64) fora do ciclo diário

Data: 2026-10-01 · Status: aceita

## Contexto

As tabelas 19 e 64 foram carregadas pelos arquivos ZIP do portal, de maio de 2026. Em 01/10/2026 a API da ANS informava 60.586 páginas na 19 e 70.551 na 64. Isso dá cerca de 125 mil códigos a mais em cada uma do que o arquivo trazia. O portal não tem arquivo mais novo.

A coleta incremental lê do topo da lista até achar códigos já conhecidos. Aqui isso significa umas 5 mil páginas por tabela, a ~2,5 min cada: mais de uma semana. Dentro do ciclo diário isso tinha três problemas: o worker ficava preso por dias e as outras tabelas paravam; a incremental não era retomada, então uma reinicialização perdia tudo; e nada era publicado até o fim.

## Decisão

- O ciclo diário do worker deixa de coletar as tabelas com mais de 2.000 páginas e passa a seguir da menor tabela para a maior.
- Um serviço separado, `recuperacao` (`tuss recuperar`, `src/tuss/recuperacao.py`), roda o tempo todo, com uma tarefa por tabela gigante, em paralelo. Continua valendo uma requisição por vez por tabela.
- A incremental passa a aceitar trechos (`max_paginas`). Cada trecho de 20 páginas (~50 min) é publicado, e a página seguinte fica em `carga.continua_em` (migration 0008). A próxima incremental começa dali. Quando ela encontra 2 páginas seguidas de códigos conhecidos, a tabela está em dia (`continua_em` nulo), e o serviço passa a ler só o topo uma vez por dia.
- O container tem limite de 256 MB e meia CPU. O trabalho é quase todo espera pela ANS.

## Consequências

- As tabelas pequenas e médias ficam prontas no mesmo dia, sem esperar as gigantes.
- A recuperação aparece aos poucos na API e no feed de mudanças, como inclusões, a cada trecho publicado. Se o processo parar, perde no máximo o trecho em andamento.
- Enquanto a recuperação lê páginas do meio da lista, a ANS pode incluir códigos novos no topo. A lista anda para baixo e o trecho seguinte relê alguns códigos, sem pular nenhum. Os novos do topo entram quando a tabela ficar em dia e a leitura voltar à página 1. Se a ANS retirar códigos no meio da leitura, a lista anda para cima e pode pular algum. Isso só seria corrigido com um arquivo novo do portal.
- Alterações e remoções no meio dessas tabelas continuam dependendo de arquivo do portal, porque a API não é lida inteira.
- As falhas do serviço de recuperação ficam só no log. O heartbeat cobre apenas o worker.
