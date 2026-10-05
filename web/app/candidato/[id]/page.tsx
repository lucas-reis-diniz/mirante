import Link from "next/link";
import { notFound } from "next/navigation";
import { candidate, money, provenance, signalsForCandidate } from "@/lib/queries";
import { brl, cnpj, count, ruleLabel } from "@/lib/format";
import { Row, NoteRow } from "@/components/Receipt";
import { Severity, Caveat } from "@/components/Severity";

export const dynamic = "force-dynamic";

/**
 * A ficha de uma candidatura.
 *
 * Note o que a página NÃO faz: não agrega as candidaturas de 2018 e 2022 numa
 * "pessoa". O banco não afirma isso, então a interface não inventa. Cada ficha
 * é uma candidatura em uma eleição — é o que a fonte sustenta.
 */
export default async function Candidate({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const historyId = Number(id);
  if (!Number.isInteger(historyId) || historyId <= 0) notFound();

  const [record, finances, alerts] = await Promise.all([
    candidate(historyId),
    money(historyId),
    signalsForCandidate(historyId),
  ]);

  const person = record.rows[0];
  if (record.error) return <ConnectionError error={record.error} />;
  if (!person) notFound();

  const cash = finances.rows[0];

  // Um único SELECT resolve os recibos de toda a página.
  const sources = await provenance([
    person.provenance_id,
    ...(cash?.money_provenance_id ? [cash.money_provenance_id] : []),
  ]);
  const byId = new Map(sources.rows.map((s) => [Number(s.provenance_id), s]));
  const registry = byId.get(Number(person.provenance_id));
  const cashSource = cash?.money_provenance_id
    ? byId.get(Number(cash.money_provenance_id))
    : undefined;

  return (
    <>
      <section className="record">
        <h1>{person.full_name}</h1>
        <div className="record__meta">
          <span>{person.election_year}</span>
          <span>{person.office}</span>
          {person.uf && <span>{person.uf}</span>}
          {person.party_acronym && <span>{person.party_acronym}</span>}
          {person.result && <span>{person.result}</span>}
        </div>
      </section>

      <section className="ledger">
        <h2>Registro de candidatura</h2>
        <Row label="Nome completo" value={person.full_name} source={registry} />
        <Row label="Nome na urna" value={person.ballot_name} source={registry} />
        <Row
          label="Partido"
          value={person.party_acronym}
          source={registry}
        />
        <Row label="Coligação" value={person.coalition} source={registry} />
        <Row label="Cargo" value={person.office} source={registry} />
        <Row
          label="Unidade eleitoral"
          value={person.municipality ?? person.uf}
          source={registry}
        />
        <Row
          label="Situação do registro"
          value={person.registration_status}
          source={registry}
        />
        <Row label="Resultado" value={person.result} source={registry} />
        <Row label="Escolaridade" value={person.education} source={registry} />
        <Row label="Ocupação declarada" value={person.occupation} source={registry} />
        <Row
          label="Identificador no TSE"
          value={<code>{person.sequential_id}</code>}
          numeric
          source={registry}
        />
      </section>

      {cash ? (
        <section className="ledger">
          <h2>Dinheiro de campanha</h2>
          <Row
            label="CNPJ da campanha"
            value={cnpj(cash.campaign_cnpj)}
            numeric
            source={cashSource}
          />
          <Row
            label="Recebido em doações"
            value={`${brl(cash.donation_cents)} em ${count(cash.donation_count)} ${
              Number(cash.donation_count) === 1 ? "doação" : "doações"
            }`}
            numeric
            source={cashSource}
          />
          <Row
            label="Despesas contratadas"
            value={`${brl(cash.expense_cents)} em ${count(cash.expense_count)} ${
              Number(cash.expense_count) === 1 ? "despesa" : "despesas"
            }`}
            numeric
            source={cashSource}
          />
          <Row
            label="Bens declarados no registro"
            value={brl(cash.asset_cents)}
            numeric
            source={registry}
          />
          <NoteRow label="Nota sobre os totais">
            <span className="hint">
              Despesa contratada é competência, não caixa. O valor pago pode
              estar parcelado em datas diferentes e fica em tabela separada,
              justamente para não ser somado duas vezes.
            </span>
          </NoteRow>
        </section>
      ) : (
        <section className="ledger">
          <h2>Dinheiro de campanha</h2>
          <NoteRow label="Prestação de contas">
            Não coletada para esta candidatura. Rode{" "}
            <code>mirante tse-accounts --years {person.election_year}</code>.
          </NoteRow>
        </section>
      )}

      <section>
        <h2 style={{ fontSize: "1.05rem", margin: "2rem 0 .75rem" }}>
          Sinais de alerta {alerts.rows.length > 0 && `(${alerts.rows.length})`}
        </h2>

        {/* Ausência de sinal e falha de consulta NÃO podem parecer a mesma
            coisa. Numa ferramenta de conferência, "não achei nada" quando na
            verdade a consulta quebrou é pior do que um erro visível: induz à
            conclusão errada. Por isso o erro vem antes e é explícito. */}
        {alerts.error ? (
          <p className="caveat" style={{ borderLeftColor: "var(--crimson)" }}>
            <strong>A consulta de sinais falhou.</strong> Isto não quer dizer que
            não há sinais — quer dizer que não foi possível verificar.
            <br />
            <span className="hint">{alerts.error}</span>
          </p>
        ) : alerts.rows.length === 0 ? (
          <p className="hint" style={{ maxWidth: "60ch" }}>
            Nenhum sinal para esta candidatura. Isso significa que as regras que
            você rodou não encontraram padrão fora da curva — não que esteja
            tudo em ordem.
          </p>
        ) : (
          <>
            <Caveat />
            <ul className="results">
              {alerts.rows.map((sig) => (
                <li key={sig.id}>
                  <Link href={`/sinais/${sig.id}`}>
                    <span className="results__name">{sig.headline}</span>
                    <span className="results__meta">
                      <Severity level={sig.severity} />
                      <span>{ruleLabel(sig.rule)}</span>
                      {sig.amount_cents && <span>{brl(sig.amount_cents)}</span>}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  );
}

function ConnectionError({ error }: { error: string }) {
  return (
    <div className="empty">
      <h2>Sem conexão com o banco.</h2>
      <p className="hint">{error}</p>
    </div>
  );
}
