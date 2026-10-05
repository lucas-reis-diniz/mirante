# AD 2 — Identidade

## Decisão

Identidade é **afirmação nossa**, não dado da fonte. Só afirmamos por match
determinístico (título eleitoral, CPF válido). Na dúvida, não afirmamos — e
registramos o descarte.

Não existe tabela "o político X". Existe `politician_history`: uma linha por
candidatura por eleição. Agregar as candidaturas numa pessoa é decisão de quem
consulta.

## Por quê

A tentação é óbvia: juntar "JOSÉ DA SILVA" de 2018 com "JOSE DA SILVA" de 2022
e chamar de mesma pessoa. Com nome, UF e partido batendo, acerta quase sempre.
"Quase sempre" é inaceitável numa base cuja saída pode virar denúncia — o custo
de um falso positivo é atribuir a alguém dinheiro que não é dele.

Hierarquia de confiança:

1. **Título eleitoral.** Único por eleitor e estável. O TSE mascarou CPF em
   2024 por LGPD e reverteu em 2026, então a série histórica 2014–2026 só
   reconcilia por título.
2. **CPF com dígito verificador válido.** Validamos no parser; CPF inválido
   é lixo de digitação e não vira pessoa.
3. **Nome.** Nunca sozinho. Serve para corroborar, jamais para afirmar.

Quando o mesmo CPF aparece sob títulos eleitorais diferentes, a fonte está
inconsistente. Não escolhemos um: removemos o CPF de `people` e gravamos em
`rejected_cpf` com o motivo. **O que não afirmamos faz parte da prova** — uma
base que só mostra o que concluiu esconde sua própria taxa de erro.

## O caso do sócio

A Receita entrega o CPF do sócio mascarado (`***123456**`). Um match por nome
canônico mais os 6 dígitos visíveis é forte, mas não é identidade: homônimos
existem, e 6 dígitos não são únicos.

Por isso `candidate_supplier_partner` é tabela separada, com `confidence`
restrito a `'possible'` por constraint, e fica **fora** de `people` e de
`signal_actor`. Um vínculo possível não pode vazar para dentro da estrutura
que o resto do sistema trata como fato. Matches ambíguos (duas ou mais pessoas
batendo) são descartados inteiros.

## Consequências

- Linhas com `person_id` nulo são normais e esperadas. Doador que nunca foi
  candidato não tem identidade afirmável — fica com o CPF no campo de texto e
  é buscável por índice trigram, sem virar `people`.
- `canonical_name` (maiúsculas, sem acento) é chave de **comparação**, nunca
  de identidade.
- O cache do `EntityResolver` detecta a ambiguidade durante a carga e, ao
  encontrá-la, invalida a afirmação que já tinha feito — `UPDATE people SET
  cpf = NULL`. Afirmação anterior que deixou de se sustentar não fica de pé.
