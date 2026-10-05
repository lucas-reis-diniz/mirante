# AD 3 — Dados derivados e disciplina epistêmica

## Decisão

As tabelas `rule_run`, `signal`, `signal_actor` e `signal_evidence` **não vêm
de fonte nenhuma**. São afirmações do nosso código sobre dados já coletados, e
são marcadas como tal.

Três restrições são aplicadas por código, não por convenção:

1. **Severidade só existe como `low`, `medium`, `high`.** Constraint no banco e
   validação em `Signal.__post_init__`. Não há como gravar "confirmado",
   "irregular" ou "fraude" — o vocabulário simplesmente não existe.
2. **Sinal sem evidência é rejeitado em tempo de execução.** Toda evidência
   aponta para uma linha real de tabela de fato, que por sua vez tem seu
   próprio `provenance_id`. A cadeia vai do sinal até a URL e o hash do CSV.
3. **Toda regra registra sua versão e seus parâmetros** em `rule_run`. Um
   sinal gerado com limiar de 15x e um gerado com 30x não são comparáveis, e o
   banco sabe qual é qual.

## Por quê

O risco de um projeto assim não é técnico, é epistêmico. O pipeline funciona,
os números são grandes, as visualizações ficam convincentes — e aí alguém lê
"sinal de alta severidade" como "pegamos um ladrão". A distância entre indício
e prova é onde o projeto pode causar dano real a pessoas reais.

Tornar essa distância uma questão de disciplina pessoal não funciona: sob
pressão de uma descoberta boa, disciplina cede. Então ela virou constraint.
`Signal(severity="confirmado")` levanta `ValueError`. Sinal sem evidência
levanta `ValueError`. O caminho fácil é o caminho correto.

## Limitações declaradas, não escondidas

Cada regra carrega no `detail` de cada sinal o que ela **não** sabe:

- **`disproportionate_expense`**: o TSE não publica quantidade neste arquivo,
  só o valor total. A regra não calcula preço unitário. R$ 50 mil em adesivo
  pode ser lote de um milhão de unidades, descrição incompleta ou erro de
  digitação.
- **`circular_donations`**: ciclo no grafo não é crime. Coligação partilha
  fornecedor, ressarcimento volta pela mesma via, militante que doou também
  presta serviço. O valor reportado é o **gargalo** do ciclo, não a soma das
  arestas — somar inflaria o número em ordens de grandeza.
- **`sanctioned_counterparty`**: a sanção pode ser posterior ao pagamento.
  Pagar em 2022 para empresa sancionada em 2025 não é irregularidade nenhuma.
  Os campos `paid_during_sanction` e `sanction_after_payment` separam os dois
  casos, e só o primeiro chega a `high`.

Essas notas vão no `detail` de cada sinal, não num rodapé do site. Quem
exportar a tabela leva a limitação junto.

## Consequências

- Rodar uma regra apaga os sinais anteriores *dela mesma* (`DELETE FROM signal
  WHERE rule = ...`). Sinais não acumulam entre execuções.
- Severidade alta nunca significa "pior crime". Significa "mais difícil de
  explicar por acaso" — ciclo curto, pagamento durante sanção vigente, desvio
  maior da mediana.
- A headline de um sinal é frase factual ("Despesa de R$ X na categoria Y,
  Nx a mediana"), nunca imputação. Se a frase só faz sentido assumindo
  má-fé, está errada.
