import { tryQuery } from "./db";

/**
 * Consultas da interface. Todas parametrizadas — nenhuma interpolação de
 * string em SQL, nem para o termo de busca.
 *
 * Toda consulta que devolve dado traz o `provenance_id` junto. Não é opcional:
 * valor sem recibo não deveria chegar à tela, e a forma de garantir isso é
 * carregar o recibo desde a consulta.
 */

export type Provenance = {
  provenance_id: number;
  parser: string;
  inner_path: string;
  url: string;
  sha256: string;
  accessed_at: string;
  crawler: string;
  source_name: string;
};

export type SearchHit = {
  politician_history_id: number;
  full_name: string;
  ballot_name: string | null;
  election_year: number;
  office: string;
  uf: string | null;
  party_acronym: string | null;
  result: string | null;
};

export type CandidateRecord = SearchHit & {
  person_id: number | null;
  sequential_id: string;
  coalition: string | null;
  municipality: string | null;
  registration_status: string | null;
  education: string | null;
  occupation: string | null;
  provenance_id: number;
};

export type MoneySummary = {
  donation_cents: string | null;
  donation_count: string;
  expense_cents: string | null;
  expense_count: string;
  asset_cents: string | null;
  campaign_cnpj: string | null;
  money_provenance_id: number | null;
};

export type SignalRow = {
  id: number;
  rule: string;
  severity: "low" | "medium" | "high";
  headline: string;
  amount_cents: string | null;
  reference_year: number | null;
  actors: string[] | null;
};

export type SignalDetail = SignalRow & {
  detail: Record<string, unknown>;
  rule_version: string;
  params: Record<string, unknown>;
  started_at: string;
};

export type EvidenceRow = {
  table_name: string;
  row_id: number;
  note: string | null;
} & Provenance;

const PROVENANCE_COLUMNS = `
  p.provenance_id, p.parser, p.inner_path, p.url, p.sha256,
  p.accessed_at, p.crawler, p.source_name
`;

/**
 * Busca por nome, CPF ou CNPJ.
 *
 * O `%` com índice trigram (pg_trgm) é o que torna busca por fragmento de
 * nome viável em mais de um milhão de candidaturas. Sem a extensão isso vira
 * varredura sequencial.
 */
export async function search(term: string, limit = 40) {
  const digits = term.replace(/\D/g, "");
  return tryQuery<SearchHit>(
    `
    SELECT h.id AS politician_history_id, h.full_name, h.ballot_name,
           h.election_year, h.office, h.uf, h.party_acronym, h.result
    FROM politician_history h
    LEFT JOIN people pe ON pe.id = h.person_id
    WHERE ($2 <> '' AND (pe.cpf = $2 OR pe.voter_id = $2))
       OR (h.full_name ILIKE '%' || $1 || '%')
       OR (h.ballot_name ILIKE '%' || $1 || '%')
    ORDER BY h.election_year DESC, h.full_name
    LIMIT $3
    `,
    [term, digits, limit],
  );
}

export async function candidate(id: number) {
  return tryQuery<CandidateRecord>(
    `
    SELECT h.id AS politician_history_id, h.person_id, h.full_name, h.ballot_name,
           h.election_year, h.sequential_id, h.office, h.uf, h.municipality,
           h.party_acronym, h.coalition, h.registration_status, h.result,
           h.education, h.occupation, h.provenance_id
    FROM politician_history h
    WHERE h.id = $1
    `,
    [id],
  );
}

/**
 * Agregados de dinheiro. Usa `campaign_expense` (competência), não
 * `campaign_expense_payment` (caixa): somar as duas contaria duas vezes a
 * despesa parcelada.
 */
export async function money(politicianHistoryId: number) {
  return tryQuery<MoneySummary>(
    `
    WITH org AS (
      SELECT o.id, c.cnpj, o.provenance_id
      FROM campaign_org o
      JOIN companies c ON c.id = o.company_id
      WHERE o.politician_history_id = $1
      LIMIT 1
    )
    SELECT
      (SELECT sum(amount_cents)::text FROM campaign_donation WHERE campaign_org_id = org.id) AS donation_cents,
      (SELECT count(*)::text        FROM campaign_donation WHERE campaign_org_id = org.id) AS donation_count,
      (SELECT sum(amount_cents)::text FROM campaign_expense  WHERE campaign_org_id = org.id) AS expense_cents,
      (SELECT count(*)::text        FROM campaign_expense  WHERE campaign_org_id = org.id) AS expense_count,
      (SELECT sum(value_cents)::text  FROM declared_asset    WHERE politician_history_id = $1) AS asset_cents,
      org.cnpj AS campaign_cnpj,
      org.provenance_id AS money_provenance_id
    FROM org
    `,
    [politicianHistoryId],
  );
}

/**
 * Resolve a cadeia de proveniência de vários ids numa consulta só.
 *
 * Aceita string e number porque o driver `pg` devolve coluna bigint como
 * STRING — para não perder precisão acima de 2^53. A primeira versão filtrava
 * com `Number.isFinite(id)` direto sobre o valor do driver, e como
 * `Number.isFinite("1")` é false (não faz coerção), descartava silenciosamente
 * todos os ids: a página renderizava sem nenhum recibo e sem erro algum.
 */
export async function provenance(ids: Array<number | string | null | undefined>) {
  const unique = [
    ...new Set(
      ids
        .map((id) => (id === null || id === undefined ? NaN : Number(id)))
        .filter((id) => Number.isInteger(id) && id > 0),
    ),
  ];
  if (unique.length === 0) return { rows: [], error: null };
  return tryQuery<Provenance>(
    `SELECT ${PROVENANCE_COLUMNS} FROM provenance_chain p WHERE p.provenance_id = ANY($1::bigint[])`,
    [unique],
  );
}

/**
 * Sinais que mencionam esta candidatura.
 *
 * Usa EXISTS e não `DISTINCT` com JOIN: um sinal pode ter vários atores, e o
 * JOIN duplicaria a linha. `SELECT DISTINCT` resolveria a duplicata, mas o
 * Postgres recusa `ORDER BY` com expressão fora da lista de seleção quando há
 * DISTINCT — e era exatamente esse o erro aqui antes. EXISTS não duplica nada,
 * então não há DISTINCT para entrar em conflito, e o índice em
 * signal_actor(person_id) ainda é usado.
 */
export async function signalsForCandidate(politicianHistoryId: number) {
  return tryQuery<SignalRow>(
    `
    SELECT s.id, s.rule, s.severity, s.headline,
           s.amount_cents::text, s.reference_year, NULL::text[] AS actors
    FROM signal s
    WHERE EXISTS (
      SELECT 1 FROM signal_actor a
      WHERE a.signal_id = s.id AND a.politician_history_id = $1
    )
    ORDER BY
      CASE s.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
      s.amount_cents DESC NULLS LAST
    LIMIT 50
    `,
    [politicianHistoryId],
  );
}

export async function signals(
  { rule, severity, limit = 50 }: { rule?: string; severity?: string; limit?: number } = {},
) {
  return tryQuery<SignalRow>(
    `
    SELECT s.id, s.rule, s.severity, s.headline, s.amount_cents::text,
           s.reference_year,
           array_remove(array_agg(DISTINCT a.display_name), NULL) AS actors
    FROM signal s
    LEFT JOIN signal_actor a ON a.signal_id = s.id
    WHERE ($1::text IS NULL OR s.rule = $1)
      AND ($2::text IS NULL OR s.severity = $2)
    GROUP BY s.id
    ORDER BY
      CASE s.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
      s.amount_cents DESC NULLS LAST
    LIMIT $3
    `,
    [rule ?? null, severity ?? null, limit],
  );
}

export async function signalDetail(id: number) {
  return tryQuery<SignalDetail>(
    `
    SELECT s.id, s.rule, s.severity, s.headline, s.amount_cents::text,
           s.reference_year, s.detail,
           r.rule_version, r.params, r.started_at,
           array_remove(array_agg(DISTINCT a.display_name), NULL) AS actors
    FROM signal s
    JOIN rule_run r ON r.id = s.rule_run_id
    LEFT JOIN signal_actor a ON a.signal_id = s.id
    WHERE s.id = $1
    GROUP BY s.id, r.rule_version, r.params, r.started_at
    `,
    [id],
  );
}

/** Evidências de um sinal, cada uma com a cadeia de proveniência resolvida. */
export async function evidence(signalId: number) {
  return tryQuery<EvidenceRow>(
    `
    SELECT e.table_name, e.row_id, e.note, ${PROVENANCE_COLUMNS}
    FROM signal_evidence e
    JOIN provenance_chain p ON p.provenance_id = e.provenance_id
    WHERE e.signal_id = $1
    ORDER BY e.id
    `,
    [signalId],
  );
}

export async function baseCounts() {
  return tryQuery<{ label: string; n: string }>(
    `
    SELECT 'candidaturas'      AS label, count(*)::text AS n FROM politician_history
    UNION ALL SELECT 'doações de campanha',   count(*)::text FROM campaign_donation
    UNION ALL SELECT 'despesas de campanha',  count(*)::text FROM campaign_expense
    UNION ALL SELECT 'despesas de cota parlamentar', count(*)::text FROM parliamentary_expense
    UNION ALL SELECT 'sanções CEIS/CNEP',     count(*)::text FROM sanction
    UNION ALL SELECT 'sinais de alerta',      count(*)::text FROM signal
    `,
  );
}
