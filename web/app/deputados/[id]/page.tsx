import Link from "next/link";
import { notFound } from "next/navigation";
import { legislatorRecord, provenance, quotaOf, votesOf, type VoteRow } from "@/lib/queries";
import { brl, camaraPropositionUrl, count, date, propositionLabel } from "@/lib/format";
import { NoteRow, Receipt, Row } from "@/components/Receipt";

export const dynamic = "force-dynamic";

type Filter = "todos" | "plenario" | "contra-partido";

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "todos", label: "Todos os votos" },
  { key: "plenario", label: "Só plenário" },
  { key: "contra-partido", label: "Diferente do partido" },
];

/**
 * A ficha de um deputado: o que ele fez com o voto.
 *
 * Disciplina de linguagem: a página descreve, não julga. "Votou diferente da
 * orientação do partido" é um fato verificável na fonte; "traiu o partido" ou
 * "foi independente" seriam leituras nossas, e ficam de fora. O mesmo vale
 * para a cor: nenhum voto é verde ou vermelho.
 */
export default async function Deputy({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ filtro?: string }>;
}) {
  const { id } = await params;
  const { filtro } = await searchParams;
  if (!/^[A-Za-z0-9-]{1,20}$/.test(id)) notFound();

  const filter: Filter = FILTERS.some((f) => f.key === filtro) ? (filtro as Filter) : "todos";

  const record = await legislatorRecord(id);
  if (record.error) {
    return (
      <div className="empty">
        <h2>Sem conexão com o banco.</h2>
        <p className="hint">{record.error}</p>
      </div>
    );
  }
  const deputy = record.rows[0];
  if (!deputy) notFound();

  const [votes, quota] = await Promise.all([
    votesOf(deputy.id, {
      plenaryOnly: filter === "plenario",
      againstPartyOnly: filter === "contra-partido",
    }),
    quotaOf(deputy.external_id),
  ]);

  // Um SELECT resolve os recibos da página inteira. Votos do mesmo arquivo
  // anual compartilham o mesmo provenance_id, então isto é pequeno.
  const sources = await provenance([
    deputy.provenance_id,
    ...votes.rows.map((v) => v.provenance_id),
    ...quota.rows.map((q) => q.provenance_id),
  ]);
  const byId = new Map(sources.rows.map((s) => [Number(s.provenance_id), s]));
  const registry = byId.get(Number(deputy.provenance_id));

  const compared = Number(deputy.compared_to_party);
  const against = Number(deputy.against_party);

  return (
    <>
      <section className="record">
        {deputy.photo_url && (
          // Foto oficial da própria Câmara, servida por ela.
          // eslint-disable-next-line @next/next/no-img-element
          <img className="portrait" src={deputy.photo_url} alt={`Foto oficial de ${deputy.full_name}`} />
        )}
        <h1>{deputy.full_name}</h1>
        <div className="record__meta">
          <span>Câmara dos Deputados</span>
          {deputy.last_party && <span>{deputy.last_party}</span>}
          {deputy.last_uf && <span>{deputy.last_uf}</span>}
        </div>
        <p className="hint" style={{ marginTop: ".5rem" }}>
          Partido e estado do voto mais recente coletado. Quem trocou de partido
          aparece, em cada voto abaixo, com o partido daquele dia.
        </p>
      </section>

      <section className="ledger">
        <h2>Resumo do período coletado</h2>
        <Row
          label="Votos nominais registrados"
          value={`${count(deputy.votes)}, de ${date(deputy.first_vote)} a ${date(deputy.last_vote)}`}
          numeric
          source={registry}
        />
        <Row label="Em plenário" value={count(deputy.plenary_votes)} numeric source={registry} />
        <Row
          label="Diferente da orientação do partido"
          value={
            compared > 0
              ? `${count(against)} de ${count(compared)} votos (${pct(against, compared)})`
              : "nenhum voto com orientação comparável"
          }
          numeric
          source={registry}
        />
        <NoteRow label="Como ler">
          <span className="hint">
            Só entram na comparação os votos em que a bancada do partido (ou a
            federação ou bloco de que ele faz parte) orientou Sim ou Não.
            Liberação e obstrução ficam de fora. Ausência não aparece aqui: o
            arquivo da Câmara lista só quem registrou voto.
          </span>
        </NoteRow>
      </section>

      {quota.rows.length > 0 && (
        <section className="ledger">
          <h2>Cota parlamentar</h2>
          {quota.rows.map((q) => (
            <Row
              key={q.reference_year}
              label={String(q.reference_year)}
              value={`${brl(q.total_cents)} em ${count(q.entries)} ${
                Number(q.entries) === 1 ? "lançamento" : "lançamentos"
              }`}
              numeric
              source={byId.get(Number(q.provenance_id))}
            />
          ))}
        </section>
      )}

      <section>
        <h2 style={{ fontSize: "1.05rem", margin: "2rem 0 .75rem" }}>Votos</h2>

        <nav className="filters" aria-label="Filtrar votos">
          {FILTERS.map((f) => (
            <Link
              key={f.key}
              href={f.key === "todos" ? `/deputados/${id}` : `/deputados/${id}?filtro=${f.key}`}
              aria-current={f.key === filter ? "page" : undefined}
            >
              {f.label}
            </Link>
          ))}
        </nav>

        {votes.error ? (
          <p className="caveat" style={{ borderLeftColor: "var(--crimson)" }}>
            <strong>A consulta de votos falhou.</strong> Isto não quer dizer que
            não há votos, quer dizer que não foi possível verificar.
            <br />
            <span className="hint">{votes.error}</span>
          </p>
        ) : votes.rows.length === 0 ? (
          <p className="hint">Nenhum voto neste filtro no período coletado.</p>
        ) : (
          <>
            {votes.rows.length >= 300 && (
              <p className="hint" style={{ marginBottom: ".75rem" }}>
                Mostrando os 300 votos mais recentes.
              </p>
            )}
            <ul className="votes">
              {votes.rows.map((v) => (
                <VoteItem key={v.session_id} vote={v} source={byId.get(Number(v.provenance_id))} />
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  );
}

function VoteItem({
  vote: v,
  source,
}: {
  vote: VoteRow;
  source: Parameters<typeof Receipt>[0]["source"];
}) {
  const diverged =
    (v.party_orientation === "Sim" || v.party_orientation === "Não") &&
    v.vote !== v.party_orientation;
  const subject = propositionLabel(v.subject_kind, v.subject_number, v.subject_year);
  const camaraPage = camaraPropositionUrl(v.subject_proposition_id);

  return (
    <li className={diverged ? "vote vote--diverged" : "vote"}>
      <div className="vote__when">
        {date(v.voted_on)}
        <br />
        {v.body_acronym === "PLEN" ? "Plenário" : v.body_acronym}
      </div>
      <div>
        {subject && <p className="vote__subject">{subject}</p>}
        {v.subject_summary && <p className="vote__summary">{v.subject_summary}</p>}
        <p className="vote__desc">{v.description}</p>

        <dl className="vote__facts">
          <div>
            <dt>Votou</dt>
            <dd>{v.vote}</dd>
          </div>
          {v.party_orientation && (
            <div>
              <dt title={`Bancada usada na comparação: ${v.party_bloc}`}>
                Orientação de {v.party_bloc}
              </dt>
              <dd>{v.party_orientation}</dd>
            </div>
          )}
          {v.government_orientation && (
            <div>
              <dt>Governo orientou</dt>
              <dd>{v.government_orientation}</dd>
            </div>
          )}
          {v.approved !== null && (
            <div>
              <dt>Resultado</dt>
              <dd>
                {v.approved ? "aprovada" : "rejeitada"}
                {v.yes_count !== null && v.no_count !== null && (
                  <span style={{ fontWeight: 400 }}>
                    {" "}
                    ({v.yes_count} sim, {v.no_count} não)
                  </span>
                )}
              </dd>
            </div>
          )}
        </dl>

        <div className="vote__links">
          {diverged && <span className="hint">Diferente da orientação do partido</span>}
          {camaraPage && (
            <a href={camaraPage} target="_blank" rel="noopener noreferrer">
              Tramitação na Câmara
            </a>
          )}
          {v.source_url && (
            <a href={v.source_url} target="_blank" rel="noopener noreferrer">
              Votação na API
            </a>
          )}
        </div>
        <div style={{ marginTop: ".4rem" }}>
          <Receipt source={source} />
        </div>
      </div>
    </li>
  );
}

function pct(part: number, whole: number): string {
  if (whole === 0) return "—";
  return `${((part / whole) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
}
