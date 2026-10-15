# Runbook — Restaurar o backup da VPS

## O que é o backup

O backup é o diário da própria Hetzner: uma cópia do disco inteiro do servidor, guardada fora dele, com as últimas 7 cópias. Ela inclui o volume do Postgres (`pgdata`), os snapshots brutos das coletas (`snapshots`), o repositório e o `.env`. Não existe outro backup, nem `pg_dump`.

A cópia é feita com o Postgres ligado. Ao subir a partir dela, o Postgres age como depois de uma queda de energia: refaz o que estava no WAL e fica pronto em menos de 1 s. Isso foi conferido no teste de 15/10/2026, descrito abaixo.

## Quando usar

- O disco do servidor se perdeu ou o banco ficou corrompido.
- Um erro apagou ou estragou dados que nem o histórico do próprio banco resolve.
- Para testar o backup (recomendado a cada 6 meses e depois de mudanças grandes no banco).

## Restauração de verdade (o servidor de produção)

1. Painel da Hetzner → servidor → aba **Backups** → escolher o backup → restaurar sobre o servidor. **Isso apaga o disco atual.** Se ainda der para acessar o servidor, guarde antes o que quiser manter.
2. Os serviços sobem sozinhos (`restart: unless-stopped`). Conferir com os comandos de verificação abaixo.
3. O que mudou na ANS depois do backup volta no próximo ciclo do worker, porque as incrementais leem o topo da lista até achar códigos conhecidos. Para não esperar a madrugada: `docker compose run --rm tuss tuss ciclo`. Os eventos refeitos ficam com a data da nova coleta, não com a original.
4. Perda máxima: o que foi publicado desde o backup (até 24 h) até o ciclo seguinte trazer de volta. Cargas aprovadas à mão nesse intervalo (`tuss aprovar`) precisam ser aprovadas de novo.

## Teste sem tocar na produção

O servidor de teste é uma cópia exata e, ao ligar, sobe tudo sozinho: worker, recuperação e os projetos do Coolify. **Ele precisa nascer sem acesso à internet.** Senão ele coleta da ANS, avisa o heartbeat (o que esconderia uma falha real da produção) e roda as tarefas dos outros projetos em dobro.

1. Criar um firewall `teste-backup` no painel da Hetzner: entrada só com TCP 22; saída com uma única regra (ICMP). Havendo regra de saída, a Hetzner bloqueia todo o resto que sai.
2. Criar um servidor CAX11 na mesma localização, com a imagem do backup e o firewall `teste-backup` **marcado na criação**. A Hetzner cobra por hora: o teste custa centavos se o servidor for apagado logo depois.
3. Entrar por SSH (mesmo usuário e chave) e rodar os comandos de verificação abaixo.
4. Apagar o servidor, o firewall e o snapshot (se o backup precisou virar snapshot para criar o servidor).

## Verificação

```bash
cd ~/tuss-api
curl -s -m 10 -o /dev/null -w 'internet: %{http_code}\n' https://www.ans.gov.br || echo "internet: bloqueada (ok)"   # só no teste
docker compose ps -a --format 'table {{.Service}}\t{{.Status}}'
docker compose logs postgres 2>&1 | grep -E 'not properly shut down|redo|ready to accept' | tail -4
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -X -A -t' <<'SQL'
select 'tabelas', count(*) from tabela_tuss;
select 'conceitos_atuais', count(*) from conceito_versao where publicado_ate is null;
select 'versoes_total', count(*) from conceito_versao;
select 'eventos', count(*), max(id) from evento_mudanca;
SQL
curl -s -o /dev/null -w 'api: %{http_code}\n' -H "Authorization: Bearer $(cat ~/.config/tuss-api/token)" http://127.0.0.1:8100/v1/tabelas/22/conceitos/40103072
```

Para comparar, rode a mesma consulta SQL na produção. Se a ANS não publicou nada desde o backup, os números são iguais. Se publicou, o backup tem menos, e a diferença deve bater com os eventos posteriores à data do backup.

## Teste de 15/10/2026

Backup de 14/10/2026, restaurado num CAX11 isolado. Do clique em criar até o banco pronto, levou menos de 1 minuto. Os 4 serviços subiram sozinhos e ficaram saudáveis. O Postgres refez o WAL (`redo done`, 0,00 s) e ficou pronto. A API respondeu 200. Tudo bateu com a produção:

| Item | Produção | Backup restaurado |
| --- | --- | --- |
| Tabelas | 65 | 65 |
| Conceitos atuais | 3.378.272 | 3.378.272 |
| Versões | 3.378.837 | 3.378.837 |
| Eventos (maior id) | 296.720 | 296.720 |
