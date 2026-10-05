# AD 1 — Confiabilidade e imutabilidade

## Decisão

Nenhuma linha de dado existe sem apontar para a coleta que a originou. Não
guardamos o arquivo bruto: guardamos **URL + data de acesso + SHA-256**, e o
leitor re-baixa para conferir.

Os crawlers são **rewrite-only**: cada execução apaga as tabelas que o crawler
possui e reconstrói do zero a partir da fonte.

## Por quê

O problema de uma base de denúncia não é guardar dado — é provar que o dado
não foi editado no caminho. Duas saídas erradas foram descartadas:

**Guardar o arquivo original.** Resolveria a prova, mas custa dezenas de GB por
ano e desloca a confiança para nós: quem garante que o zip que guardamos é o
que o TSE publicou? O hash resolve isso sem o custo, e desloca a confiança
para a fonte, que é onde ela pertence.

**Hash chain ou trigger de imutabilidade no banco.** Dá sensação de segurança e
protege contra quase nada: quem pode escrever no banco pode recalcular a
cadeia. A garantia real vem de fora — o `manifest.json` commitado no git, cujo
histórico é público e assinado, mais um build determinístico que qualquer
pessoa roda.

Rewrite-only é consequência disso. Se o banco pode ser reconstruído do zero a
qualquer momento, "editar o passado" não compra nada: a próxima execução
desfaz. O banco vira artefato descartável e a prova mora no manifesto.

## Consequências

- `TRUNCATE ... RESTART IDENTITY CASCADE` apaga dado derivado junto. É
  intencional: sinal calculado sobre dado antigo não deve sobreviver ao dado.
- A tabela passa a conter **exatamente os anos pedidos** em `--years`. Rodar
  com `--years 2022` depois de `--years 2014 2022` deixa só 2022.
- O hash do zip não basta. O TSE reempacota, e dois zips de conteúdo idêntico
  podem ter hashes diferentes. Por isso guardamos também o SHA-256 de cada CSV
  interno — é o que de fato identifica o dado.
- Fonte que não é re-baixável de forma determinística (raspagem de página que
  muda, API sem versionamento) **não entra**. É o critério que exclui raspar
  consulta processual de tribunal.
