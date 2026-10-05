# Interface do Mirante

Aplicação Next.js **só-leitura** sobre o Postgres do pipeline. Busca uma
candidatura e mostra a ficha com **a fonte de cada valor**.

## Rodar

```bash
cd web
npm install
export MIRANTE_DATABASE_URL='postgresql://user:senha@host:5432/postgres'
npm run dev          # http://localhost:3000
```

Sem dados? O pipeline tem um conjunto sintético que sobe em segundos:

```bash
mirante init-db
mirante seed-demo
mirante rule-sanctioned-counterparty
mirante rule-disproportionate-expense
mirante rule-circular-donations
```

Os dados de `seed-demo` são inventados e marcados como tal: nomes fictícios,
origem apontando para `exemplo.invalid`, hash zerado. Servem para desenvolver
a interface sem baixar dezenas de GB do TSE.

## Por que não GitHub Pages

Pages serve arquivo estático. Esta interface consulta Postgres a cada
requisição, então precisa de servidor. Vercel, Render ou um contêiner
qualquer resolvem; Pages não.

Na Vercel: importe o repositório, defina a raiz do projeto como `web`, e
adicione `MIRANTE_DATABASE_URL` nas variáveis de ambiente. O build é o padrão
do Next.

## Decisões que valem conhecer antes de mexer

**Só-leitura em três camadas.** A conexão abre com
`default_transaction_read_only=on`, então qualquer escrita morre no banco e
não na revisão de código. Há `statement_timeout` de 15s, porque consulta mal
escrita sobre tabela de milhões de linhas derruba a instância inteira. E o
módulo `lib/db.ts` não expõe caminho de escrita. Em produção, use também um
ROLE sem privilégio de escrita — aí a garantia passa a ser do servidor:

```sql
CREATE ROLE mirante_web LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE postgres TO mirante_web;
GRANT USAGE ON SCHEMA public TO mirante_web;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mirante_web;
```

**Nada de JavaScript de cliente.** Busca é formulário GET, filtros são links,
e o recibo é `<details>` nativo. Consequências: o resultado de uma busca mora
na URL e é citável, a página funciona com JS desligado, e a navegação por
teclado vem de graça. Para um público de jornalista e auditor, URL citável
não é detalhe.

**Bigint vem do driver como string.** O `pg` devolve `int8` como string para
não perder precisão acima de 2^53. Isso já causou um bug real aqui:
`Number.isFinite("1")` é `false`, e um filtro escrito assim descartou todos os
ids de proveniência em silêncio — a página renderizou sem nenhum recibo e sem
erro algum. Ao comparar ou filtrar id vindo do banco, converta antes.

**Ausência de recibo é visível.** Valor sem proveniência resolvida mostra um
marcador tracejado em vermelho dizendo "sem origem", em vez de nada. Foi o
que teria tornado o bug acima óbvio em um segundo, e é coerente com a
premissa: se a promessa é que todo valor tem origem, a exceção precisa gritar.

**Erro de consulta nunca parece ausência de resultado.** "Nenhum sinal" e "a
consulta de sinais falhou" são telas diferentes. Numa ferramenta de
conferência, as duas parecerem a mesma coisa induz à conclusão errada — que
é pior do que um erro visível.

**A linha inteira é o botão do recibo.** Não é só estética: o Chrome mantém
uma caixa anônima em volta do conteúdo do `<details>`, então `display:
contents` não promove o corpo a item da grade da linha, e ele fica preso na
largura da primeira coluna. Com a linha dentro do `<details>`, não existe
grade externa para atravessar — e o alvo de clique passa a ser a linha toda.

## Estrutura

```
app/
  page.tsx               busca (formulário GET, resultado na URL)
  candidato/[id]/        ficha da candidatura, com recibo por valor
  sinais/                lista de sinais, com filtros por link
  sinais/[id]/           detalhe: o que a regra achou, o que ela NÃO sabe,
                         versão e parâmetros da execução, e cada evidência
                         com URL e hash do arquivo de origem
lib/
  db.ts                  pool só-leitura, TLS resolvido por host
  queries.ts             consultas; toda consulta traz provenance_id junto
  format.ts              centavos -> BRL sem passar por float
components/
  Receipt.tsx            a linha de livro-razão e o recibo
  Severity.tsx           severidade e a ressalva de indício
```

## O que a interface deliberadamente não faz

Não agrega as candidaturas de 2018 e 2022 numa "pessoa". O banco não afirma
isso — identidade é afirmação explícita, e agregar por nome produziria falso
positivo. Cada ficha é uma candidatura em uma eleição, que é o que a fonte
sustenta. Ver `ADs/identidade.md`.
