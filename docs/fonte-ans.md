# Fonte ANS (API OCL) — resultado da Fase 0

Gerado por `scripts/fase0_fonte_ans.py` em 2026-09-29 20:33 UTC. Não editar à mão: rode o script de novo. Respostas brutas em `tests/fixtures/ans/`.

## Chamadas feitas

| Chamada | HTTP | Tempo | Bytes | Tentativas |
| --- | --- | --- | --- | --- |
| `GET /ANS/source` | 200 | 0.28 s | 5.145 | 1 |
| `GET /ANS/concepts/tuss-22?page=1` | 200 | 162.89 s | 4.986 | 1 |

Uma única medição por chamada: serve como ordem de grandeza, não como distribuição. Repita o script em horários diferentes antes de fixar timeouts e a janela de coleta.

## Paginação

- Registros na página 1 de `tuss-22`: **25**.
- Cabeçalho `pages` da resposta: **239**.
- `Total_sources` em `/ANS/source` para `tuss-22`: **5964**.
- `pages` × tamanho da página = 5975; consistente com o total (5964).
- A resposta é um array JSON puro; o número de páginas vem no cabeçalho `pages`, não no corpo. Não há parâmetro de tamanho de página testado.

Cabeçalhos da resposta de conceitos:

```
connection: close
content-length: 4986
content-type: application/json
date: Tue, 29 Sep 2026 20:33:58 GMT
pages: 239
server: nginx
strict-transport-security: max-age=31536000
x-frame-options: sameorigin
```

## Campos reais de um conceito

Analisados os 25 conceitos da página 1 de `tuss-22`.

| Campo | Tipo(s) | Preenchidos | Exemplo |
| --- | --- | --- | --- |
| `id` | str | 25/25 | `"30918090"` |
| `source` | str | 25/25 | `"tuss-22"` |
| `display_name` | str | 25/25 | `"Ablação percutânea por cateter para tratamento de arritmias cardía...` |
| `extras.fim_vigencia` | str | 0/25 | `"-"` |
| `extras.fim_implantacao` | str | 25/25 | `"2026-10-31"` |
| `extras.inicio_vigencia` | str | 25/25 | `"2026-08-01"` |

Exemplo completo (primeiro conceito da página):

```json
{
  "extras": {
    "fim_vigencia": "-",
    "fim_implantacao": "2026-10-31",
    "inicio_vigencia": "2026-08-01"
  },
  "id": "30918090",
  "source": "tuss-22",
  "display_name": "Ablação percutânea por cateter para tratamento de arritmias cardíacas complexas (fibrilação atrial, taquicardia ventricular com modificação de cicatriz, taquicardias atriais macrorrentrantes com modificação de cicatriz), por energia de campo pulsado (PFA)"
}
```

## Datas de vigência

| Campo | Distribuição na página 1 |
| --- | --- |
| `extras.fim_vigencia` | 25 × ausente (`-`, vazio ou nulo) |
| `extras.fim_implantacao` | 25 × ISO 8601 (AAAA-MM-DD) |
| `extras.inicio_vigencia` | 25 × ISO 8601 (AAAA-MM-DD) |

Isto responde a pergunta central do plano (a fonte traz datas oficiais de vigência?) apenas para a amostra acima. Ausência de fim de vigência (`-`) deve ser lida como período aberto, mas confirme com mais páginas e outras tabelas antes de decidir o modelo.

## Integridade da amostra

- Ids únicos na página: 25 de 25.
- Tipo do `id`: str (código TUSS como texto).
- Valores de `source`: tuss-22.

## Catálogo (`/ANS/source`)

65 tabelas; soma de `Total_sources`: 3.082.114.

Maiores tabelas:

| Código | Descrição | Total |
| --- | --- | --- |
| `tuss-64` | Forma de envio de procedimentos e itens assistenciais para ANS | 1.637.708 |
| `tuss-19` | Materiais, Órteses, Próteses e Materiais Especiais (OPME) | 1.389.786 |
| `tuss-20` | Medicamentos | 43.376 |
| `tuss-22` | Procedimentos em saúde | 5.964 |
| `tuss-18` | Diárias, taxas e gases medicinais | 3.595 |
| `tuss-38` | Mensagens (glosas, negativas e outras) | 784 |
| `tuss-24` | Código Brasileiro de Ocupação (CBO) | 168 |
| `tuss-87` | Tabelas de domínio | 68 |

## Ainda não verificado

- Comportamento do parâmetro `q`.
- Existência de URL estável para downloads em lote (tabelas 19 e 64).
- Tamanho de página nas demais tabelas e estabilidade da ordem entre chamadas.
- Limites de taxa e comportamento sob carga.
- Schema de `extras` nas outras tabelas (pode variar por tabela).
