# ADR 0005 — Erro de digitação na busca: corrigir palavra por palavra

Data: 2026-10-04 · Status: aceita

## Contexto

A busca por texto tem dois passos: primeiro quem tem todas as palavras (full-text), depois as descrições parecidas com a frase inteira (trigramas), para tolerar erro de digitação. Cada passo entrega no máximo 1.000 candidatos para a ordenação.

Nas tabelas 19 e 64 (1,5 e 1,8 milhão de conceitos), uma frase com erro de digitação, como "fressa tungstenio", não acha nada pelas palavras e acha milhares de parecidas. As 1.000 que entravam eram quaisquer, e "Brocas de tungstênio" vinha antes de "Fresas de tungstênio". Além disso, o passo dos parecidos leva até 0,7 s nessas tabelas.

## Opções medidas

- **Índice GiST de trigramas**, que devolve as descrições já na ordem de parecença: 956 MB e de 1,7 a 11 s por busca. Com descrições longas (~75 letras), o índice quase não consegue descartar nada e lê quase meio gigabyte por busca. Descartada.
- **Subir o limite de parecença até sobrarem poucos candidatos**: a contagem pula de mais de 1.000 para zero entre um limite e o seguinte ("fressa tungstenio" de 0,6 para 0,7), e cada tentativa custa de 100 a 500 ms. Descartada.
- **Corrigir cada palavra pelo vocabulário da própria tabela** (como num corretor ortográfico): escolhida.

## Decisão

- A tabela `vocabulario` (migration 0011) guarda as palavras de cada tabela TUSS e quantas vezes aparecem nas descrições atuais. Entram só palavras de letras, com 3 ou mais. Nas tabelas com mais de 50 mil conceitos, só as que aparecem 3 vezes ou mais: as raras são quase todas nomes de modelo ou erros da própria fonte. Isso deixou a correção 5 vezes mais rápida (~20 ms por palavra) sem piorar o resultado.
- A publicação refaz o vocabulário da tabela quando a carga muda algo, na mesma transação (`tuss_reconstroi_vocabulario`).
- Na busca, cada palavra que não existe no vocabulário vira, entre as 20 mais parecidas, a que melhor combina parecença e frequência: "parafuzo" vira "parafuso" (comum), não "parafu" (rara e mais parecida). A busca por palavras procura o termo como veio **ou** corrigido.
- O passo dos parecidos continua como reserva, mas só roda quando a busca por palavras acha menos resultados do que o pedido. Quem tem as palavras vem sempre antes na ordem, então com resultados suficientes nenhum parecido entraria na resposta.

## Consequências

- Medido nas tabelas 19 e 64: "fressa tungstenio" traz as fresas primeiro, e "cateter venozo sentral" o cateter venoso central. A maioria das buscas fica entre 0,05 e 0,2 s, quando antes levava até 0,9 s.
- Quando a busca por palavras acha menos de 50 resultados, o passo dos parecidos roda e a busca leva de 0,5 a 0,7 s nas tabelas gigantes. Isso fica acima da meta de 400 ms, mas dentro do limite de 2 s.
- Erro de digitação que também existe na fonte não é corrigido: "titaneo" aparece 17 vezes nas descrições da ANS, então para o corretor ele é uma palavra válida. O resultado sai certo pelo passo dos parecidos, só que mais devagar.
- Siglas ("RM" para ressonância) continuam fora, como já estava anotado.
- Os 1.000 candidatos da busca por palavras ainda são quaisquer quando passam disso. Isso só pesa com termos muito comuns nas tabelas gigantes.
