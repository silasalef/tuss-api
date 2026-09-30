# ADR 0002 — Carga inicial não gera eventos de mudança

Data: 2026-09-30 · Status: aceita

## Contexto

O feed `GET /v1/mudancas` responde "o que a ANS mudou desde a data D": inclusões, alterações, remoções e reativações de códigos. Os eventos (`evento_mudanca`) são gravados na mesma transação que publica cada carga.

A primeira carga de uma tabela (`tuss importar`) não é uma mudança feita pela ANS: é o momento em que o projeto começa a observar aquela tabela. Se ela gerasse eventos, o feed começaria com uma "inclusão" para cada código já existente (49.343 só nas tabelas 22 e 20), todas com a data da importação, e não com a data em que a ANS incluiu o código.

## Decisão

A carga inicial de uma tabela publica conceitos e versões, mas **não grava eventos**. Ela é a linha de base do histórico. Quantos conceitos entraram fica registrado em `carga.incluidos`.

Eventos passam a ser gerados a partir da segunda carga publicada da tabela, comparando com a anterior (Fase 3).

## Consequências

- O feed mostra só mudanças reais observadas entre duas cargas, sem ruído de milhares de "inclusões" artificiais.
- Quem quiser saber quando um código entrou na lista da ANS usa `inicio_vigencia` (data oficial), não o feed.
- Uma tabela recém-importada tem feed vazio até a próxima carga com mudança. `GET /v1/status` mostra a data da carga inicial, para deixar isso claro.
