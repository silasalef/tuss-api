# Runbook — carga retida pelo limite de anomalia

## Quando acontece

Uma atualização de tabela que remove mais de 2% dos conceitos, ou faz a tabela encolher mais de 1%, não é publicada sozinha (`src/tuss/domain/anomalia.py`). A carga fica com status `retida`, o motivo aparece em `GET /v1/status` (campo `erro` da última carga) e a API continua servindo a carga anterior. Enquanto houver carga retida, nenhuma outra carga dessa tabela começa.

A ANS muda pouco de uma vez. Remoção em massa quase sempre é problema na coleta (fonte devolvendo lista pela metade, arquivo cortado), não decisão da ANS.

## O que fazer

1. Ver o que a carga faria (não muda nada):

   ```bash
   docker compose run --rm tuss tuss aprovar <carga>   # mostra o resumo e pergunta; responda "n"
   ```

2. Conferir se as remoções são reais: pegar alguns códigos que sairiam e procurar no portal da ANS ou na API oficial (`/ANS/concepts/tuss-NN?q=<código>`). Uma nota técnica ou publicação da ANS no período ajuda a confirmar.

3. Decidir:
   - remoções reais: `docker compose run --rm tuss tuss aprovar <carga>`. A carga é publicada, os eventos `removido` entram no feed e o `erro` da carga passa a começar com "aprovada manualmente".
   - problema na coleta: `docker compose run --rm tuss tuss descartar <carga>`. Nada é publicado; a carga vira `falhou` com "descartada" no `erro`, e a tabela volta a aceitar cargas.

## Se a aprovação disser que o rascunho se perdeu

O rascunho (`stg_conceito`) é uma tabela UNLOGGED: mais rápida, mas esvaziada se o Postgres cair. Descarte a carga e refaça a importação ou a coleta.
