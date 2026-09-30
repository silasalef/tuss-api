# Runbook — ANS fora do ar ou lenta demais

## Como aparece

- E-mail do serviço de heartbeat (healthchecks.io): o ciclo diário não avisou, ou avisou `/fail`.
- `GET /v1/status`: `sincronizado_em` antigo, ou última carga `falhou` / `em_andamento`.
- Log do worker (`docker compose logs worker`): eventos `fonte_indisponivel` e `ciclo_interrompido`.

## O que o sistema já faz sozinho

- Cada requisição tem timeout de 5 minutos e até 4 tentativas, com espera crescente e aleatória.
- Coleta completa interrompida fica `em_andamento` e é retomada da página seguinte no próximo ciclo.
- Coleta incremental interrompida é marcada como `falhou` e refeita no próximo ciclo.
- Se a ANS falhar em 3 tabelas seguidas, o ciclo para (insistir não ajuda) e tenta no dia seguinte.
- A API continua respondendo com a última carga publicada; nada precisa ser feito para ela.

## O que fazer

1. Conferir se a ANS está no ar: abrir `https://consulta-ocl.apps.sa-1a.mendixcloud.com/rest/oclservice/ANS/source` no navegador (responde em menos de 1 s quando está bem).
2. Se estiver no ar, rodar um ciclo à mão para não esperar o dia seguinte:

   ```bash
   docker compose run --rm tuss tuss ciclo
   ```

3. Se uma tabela específica falhar sempre com "dado inválido", a ANS mudou o formato: ver o `erro` da carga em `/v1/status` e a página guardada em `/var/lib/tuss/snapshots/<tabela>/carga-<id>/` (volume `snapshots`). Nesse caso o código precisa de ajuste; a tabela continua servindo a última carga boa.
