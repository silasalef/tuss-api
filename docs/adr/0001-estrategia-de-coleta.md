# ADR 0001 — Estratégia de coleta da fonte ANS

Data: 2026-09-29 · Status: aceita

## Contexto

A ANS publica as 65 tabelas TUSS de duas formas, e nenhuma serve sozinha (medições em `docs/fonte-ans.md` e fixtures em `tests/fixtures/ans/`):

- **API OCL** (`/ANS/concepts/{tabela}?page=&q=`): dados atuais, mas cada requisição leva de 2 a 3,5 minutos, independentemente do tamanho da tabela. Páginas de 25 itens, sem parâmetro para aumentar o tamanho nem para filtrar por data (`limit` e `updatedSince` são ignorados; `q` filtra só por texto e código). Varrer a tabela 19 (1,39 milhão de conceitos, ~55,6 mil páginas) levaria meses.
- **Download no portal** (`/p/ocl`): tabela inteira em segundos (a 19 vem num ZIP de 25 MB), mas os ZIPs das tabelas grandes são pré-gerados (26/05/2026) e já não têm as inclusões de 01/08/2026 que a API mostra. Não há URL fixa: o arquivo sai de uma sessão Mendix, por link de uso único.

O catálogo `/ANS/source` responde rápido, mas o `Total_sources` pode estar desatualizado (tuss-20: 43.376 no catálogo contra ~44,5 mil paginados na API).

Sobre a ordem da API, observamos em 29/09/2026: a página 1 traz as inclusões mais recentes; releituras com 1 a 3 horas de intervalo devolveram exatamente o mesmo conteúdo, na mesma ordem; todo código da página 1 é maior que os da página 2, mas dentro de uma página há pequenas inversões, então a ordem não é por código.

## Decisão

1. **Carga inicial por arquivo.** O dono baixa o JSON/ZIP de cada tabela no portal e o importa com `tuss importar <arquivo>`. Não automatizamos a sessão Mendix: é interface interna, sem contrato, e quebraria sem aviso.
2. **Atualização pela API, incremental.** O worker lê a partir da página 1, uma requisição por vez por tabela, e para depois de **2 páginas seguidas só com conceitos já conhecidos e sem alteração**. A regra não usa comparação de códigos, porque a ordem não é por código.
3. **Varredura completa em ciclo longo.** Alterações e remoções no meio da tabela só aparecem numa varredura completa. Tabelas pequenas e médias são varridas por inteiro periodicamente; as tabelas 19 e 64 dependem de reimportação quando a ANS regerar os arquivos.
4. **`Total_sources` é só aviso**, nunca bloqueio da validação.

## Consequências

- A base fica atualizada nas inclusões (o caso mais comum) sem varrer milhões de linhas.
- Alterações e remoções nas tabelas 19 e 64 podem demorar a aparecer. As respostas informam `sincronizado_em` para deixar isso explícito.
- A carga inicial depende de um passo manual por tabela, feito uma vez.
- **Premissa confirmada em 30/09/2026** (`docs/ordem-da-fonte.md`, gerado por `scripts/ordem_da_fonte.py`). Comparando a API com o arquivo da tuss-20 de 26/05/2026: os ~1.200 códigos incluídos depois dele ocupam exatamente as primeiras 48 páginas, do mais novo para o mais antigo (vigência 08/2026, depois 06/2026, depois 04/2026), e a fronteira é limpa: na página 48, os 23 primeiros são novos e os 2 últimos já estavam no arquivo. A página 500 não tem nenhum código novo. Isso bate com a conta: 1.783 páginas na API contra 43.376 códigos no arquivo.
- Não está confirmado se um código antigo **alterado** também sobe para o topo. Por isso a varredura completa em ciclo longo continua necessária para alterações e remoções. O plano B (varredura completa das pequenas e médias, reimportação de arquivo para 19 e 64) fica para o caso de a ANS mudar esse comportamento.
