# Mirante — cruzamento de dados públicos sobre política brasileira

Mirante monta, a partir de **fontes oficiais e públicas**, uma base consolidada
sobre candidaturas, dinheiro de campanha, cota parlamentar e sanções
administrativas — cruzada por CPF e CNPJ — para levantar **indícios** de
padrões que merecem apuração.

> **Indício não é prova.** Nada aqui é acusação. O sistema gera *sinais de
> alerta* para serem checados por quem tem competência para isso. Toda linha
> exibida aponta para o arquivo público de onde saiu, e qualquer pessoa pode
> re-baixar esse arquivo e conferir o hash.

Backend: pipeline de coleta em Python. Banco: **Postgres hospedado** (Supabase,
Neon, RDS ou qualquer Postgres) — não um arquivo local, para que a base seja
consultável de qualquer lugar e por mais de uma pessoa ao mesmo tempo.
Interface: app Next.js só-leitura em [`/web`](web/README.md) — busca uma
candidatura e mostra a ficha com a fonte de cada campo.

---

## Começar

Requisitos: Python 3.11+ e um Postgres acessível.

```bash
git clone <seu-repo> mirante && cd mirante
pip install -e ".[dev]"

export MIRANTE_DATABASE_URL='postgresql://user:senha@host:5432/postgres'
mirante init-db
```

Para ver tudo funcionando em segundos, com dados **sintéticos** (nomes
fictícios, origem apontando para `exemplo.invalid`, marcados como demonstração
em toda parte):

```bash
mirante seed-demo
mirante rule-sanctioned-counterparty
mirante rule-disproportionate-expense
mirante rule-circular-donations

cd web && npm install && npm run dev     # http://localhost:3000
```

Isso existe porque sem ele ninguém consegue mexer na interface sem antes
baixar dezenas de GB do TSE.

A primeira rodada útil com dados **reais**, que termina em minutos e não em
horas:

```bash
mirante camara-ceap --years 2026        # cota parlamentar: base pequena, ciclo completo
mirante transparencia-sanctions         # CEIS/CNEP: snapshot do dia
mirante rule-sanctioned-counterparty    # o primeiro cruzamento com valor real
mirante status
```

Depois, quando quiser a base grande (horas de download, dezenas de GB):

```bash
mirante tse-candidates --years 2022 2026
mirante tse-accounts   --years 2022 2026     # a etapa mais longa
mirante rule-disproportionate-expense
mirante rule-circular-donations
```

Para testar o pipeline inteiro sem esperar, limite a uma UF:

```bash
mirante tse-candidates --years 2022 --uf SP
mirante tse-accounts   --years 2022 --uf SP
```

---

## A ideia central

**Nenhuma linha de dado existe sem apontar para a coleta que a originou.**

```
source  ->  collection  ->  collection_file  ->  parse  ->  cada linha de fato
(órgão)     (execução)      (URL + SHA-256)     (CSV)      (provenance_id)
```

"De onde veio isso?" é um `JOIN`, não uma questão de confiança:

```sql
SELECT e.description, e.amount_cents, p.url, p.sha256, p.accessed_at
FROM campaign_expense e
JOIN provenance_chain p ON p.provenance_id = e.provenance_id
WHERE e.id = 12345;
```

Não guardamos o arquivo bruto — guardamos **URL + data + SHA-256** no
`manifest.json`, que vai para o git. O histórico público do repositório passa
a provar o que foi coletado e quando. Quem duvidar re-baixa e compara o hash.

Cada crawler é **rewrite-only**: apaga as tabelas que possui e reconstrói do
zero. O banco é artefato descartável; a prova de integridade é o manifesto
commitado mais um build determinístico — não trigger, não hash chain.

---

## O que entra no banco

| Tabela | O que é | Crawler |
| --- | --- | --- |
| `politician_history` | Uma linha por candidatura por eleição | `tse-candidates` |
| `people` | Pessoa (chave surrogate). Todo JOIN por pessoa passa aqui, nunca por CPF cru | compartilhada |
| `companies` | Empresa por CNPJ (`kind`: campaign/donor/supplier/sanctioned/contractor) | compartilhada |
| `campaign_org` | O CNPJ que cada candidatura abre para a campanha | `tse-accounts` |
| `campaign_donation` | Cada doação recebida: quem, quanto, quando, por qual via | `tse-accounts` |
| `campaign_expense` | Cada despesa **contratada** (competência) | `tse-accounts` |
| `campaign_expense_payment` | Quando o dinheiro de fato saiu (**caixa**) | `tse-accounts` |
| `parliamentary_expense` | Cota parlamentar (CEAP), com link da nota fiscal | `camara-ceap` |
| `sanction` | CEIS/CNEP: impedidos de contratar ou punidos por corrupção | `transparencia-sanctions` |
| `declared_asset` | Bens declarados no registro da candidatura | `tse-assets` (a fazer) |
| `earmark` | Emendas parlamentares: autor, beneficiário, empenhado/pago | `transparencia-earmarks` (a fazer) |
| `rejected_cpf` | CPFs descartados por ambiguidade, **e o motivo** | `tse-candidates` |
| `source` / `collection` / `collection_file` / `parse` | Proveniência | todos |
| `rule_run` / `signal` / `signal_actor` / `signal_evidence` | Sinais de alerta — **não vêm de fonte nenhuma** | as regras |

Confundir `campaign_expense` com `campaign_expense_payment` infla os números:
uma despesa parcelada aparece nas duas. Competência e caixa são coisas
diferentes e ficam em tabelas diferentes de propósito.

---

## Sinais de alerta

Estas tabelas são **afirmações nossas**, não das fontes. Vocabulário
deliberadamente contido: severidade é `low`/`medium`/`high`, nunca
"confirmado", nunca "fraude". E `Signal` recusa em tempo de execução qualquer
sinal sem evidência apontando para uma linha real — sinal sem lastro é
opinião, e o código não deixa gravar.

**`disproportionate_expense`** — item tipicamente barato (18 categorias:
caneta, adesivo, crachá...) com valor muito acima do normal *para aquela
categoria*. `medium` a partir de 15x a mediana da categoria, `high` a partir
de 30x, com piso de R$ 1.000. Categorias com menos de 20 observações não têm
mediana confiável e usam piso fixo.
*Limitação declarada:* o TSE não publica quantidade neste arquivo, só o valor
total. A regra não calcula preço unitário. Pode ser lote grande, item não
detalhado ou erro de digitação.

**`circular_donations`** — grafo dirigido (doador → campanha → fornecedor) e
Tarjan (componentes fortemente conexas) + DFS com profundidade limitada para
achar ciclos: dinheiro que sai de uma campanha e volta para a mesma cadeia. O
valor do ciclo é o **gargalo** — o menor elo da corrente, que é o máximo que
pode ter efetivamente circulado.
*Limitação declarada:* ciclo pode ser coligação, ressarcimento, ou fornecedor
que também é militante e doou.

**`sanctioned_counterparty`** — empresa no CEIS/CNEP que recebeu dinheiro de
campanha ou de cota parlamentar. É a regra que justifica coletar fontes
diferentes: cada base sozinha é inócua, cruzadas por CNPJ viram pergunta.
*Nuance temporal:* a sanção pode ser posterior ao pagamento. Pagar em 2022
para empresa sancionada em 2025 não é irregularidade. Só pagamento durante
sanção vigente chega a `high`; o resto fica `low`, com as duas datas no
`detail`.

---

## Identidade

Identidade é **afirmação nossa**, não dado da fonte.

- Match determinístico por título eleitoral e CPF. Título é mais confiável:
  o TSE mascarou CPF em 2024 por LGPD e reverteu em 2026, então a série
  histórica só reconcilia por título.
- CPF com dígito verificador inválido é descartado no parser — lixo de
  digitação não vira "pessoa".
- **Na dúvida, não afirma.** Mesmo CPF sob títulos eleitorais diferentes é
  ambiguidade: o CPF é removido de `people` e registrado em `rejected_cpf`
  com o motivo. O que não afirmamos faz parte da prova.

---

## Fontes

| Fonte | O que traz | Estado |
| --- | --- | --- |
| TSE — `consulta_cand` | Cadastro de candidaturas 2014–2026 | implementado |
| TSE — prestação de contas | CNPJ de campanha, doações, despesas, pagamentos | implementado |
| Câmara — CEAP | Cota parlamentar com link da nota fiscal | implementado |
| Portal da Transparência — CEIS/CNEP | Impedidos de contratar; punidos por corrupção | implementado |
| TSE — `bem_candidato` | Bens declarados | a fazer |
| TSE — `rede_social_candidato` | Redes sociais declaradas (obrigatório desde 2018) | a fazer |
| Portal da Transparência — emendas | Emendas parlamentares | a fazer (schema pronto) |
| Senado — dados abertos | CEAPS, mandatos | a fazer |
| Receita (BrasilAPI) | Quadro societário de CNPJ | a fazer (schema pronto) |

**Não coletamos** (protegido ou sigiloso): endereço residencial, telefone e
e-mail pessoal; antecedentes fora de processo público; relatórios do COAF.

Sobre processos judiciais por candidato: a API Pública do DataJud (CNJ) **não
devolve nome nem CPF das partes** — só metadado processual. É proposital, por
sigilo. Sem identificação no índice, não dá para perguntar "todos os processos
do candidato X" por essa via. A alternativa seria raspar 90+ tribunais sem
padrão comum, que é exatamente o tipo de fonte não reprodutível que a
disciplina de proveniência deste projeto rejeita.

---

## Hospedar o banco

Qualquer Postgres 14+ serve. O que muda é o tamanho que você pretende carregar:

- **CEAP + sanções + uma UF do TSE**: alguns GB. Cabe em tier gratuito de
  Supabase ou Neon com folga.
- **TSE completo 2014–2026**: dezenas de GB. Precisa de instância paga ou
  máquina própria.

A estratégia que funciona é carregar primeiro o recorte pequeno, rodar as
regras, ver os sinais, e só então decidir se vale a conta da base completa.

Três extensões são criadas pelo `init-db`: `pg_trgm` e `btree_gin` (busca por
nome de doador e fornecedor que nunca foram candidatos) e `unaccent` (para
"ADESIVAÇÃO" casar com "ADESIVACAO"). Provedores gerenciados às vezes já as
trazem e bloqueiam `CREATE EXTENSION`; o código trata isso e segue.

---

## Desenvolvimento

```bash
pytest                 # testes, sem rede e sem banco
```

Os testes cobrem a normalização (CPF, CNPJ, dinheiro em centavos, datas), o
hashing de arquivos e membros de zip, o Tarjan iterativo — incluindo cadeia de
5 mil nós, que mata recursão ingênua — e a disciplina do `Signal`, que recusa
sinal sem evidência e severidade fora do vocabulário.

O schema e as três regras foram verificados contra Postgres 16 real, com
`seed-demo` carregado: 11 sinais gerados, 21 evidências, todas com cadeia de
proveniência resolvida.

Para a interface:

```bash
cd web && npm run typecheck && npm run build
```

### Um modo de falha que apareceu três vezes

Validação que rejeita silenciosamente. Um CNPJ com dígito verificador
inválido fazia o resolver devolver `None`; `Number.isFinite("1")` ser `false`
fazia o filtro de proveniência descartar todos os ids; e uma consulta com erro
de SQL renderizava como "nenhum sinal". Nos três casos o sistema seguiu
adiante produzindo resultado vazio e plausível, sem erro algum.

Numa ferramenta cuja saída pode virar denúncia, vazio silencioso é pior que
exceção. As três situações hoje falham alto: o seed levanta `RuntimeError`, a
interface mostra "sem origem" em vermelho tracejado, e erro de consulta tem
tela própria, distinta de ausência de resultado.

Quando um download der HTTP 403 (filtro anti-bot da fonte), baixe o `.zip` no
navegador, coloque em `dados_tmp/` com o nome que o log mostrou e rode o
crawler de novo: ele reaproveita o arquivo local e ainda assim calcula o hash
para o manifesto.

---

## Premissa legal e ética

- Todas as fontes são públicas por determinação legal.
- O resultado é **indício, não prova**. Nada aqui deve virar acusação pública
  sem apuração formal.
- Licença AGPL-3.0: quem rodar isto como serviço precisa abrir o código. Um
  projeto de transparência que fecha o próprio código seria uma contradição.
