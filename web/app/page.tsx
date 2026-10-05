import Link from "next/link";
import { search, baseCounts } from "@/lib/queries";
import { count } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * A busca é a página inicial porque a busca é o produto: o ato de procurar
 * alguém e receber de volta um número com a origem dele.
 *
 * Formulário GET, sem JavaScript de cliente. O termo vive na URL, então um
 * resultado é compartilhável e citável — o que importa quando o público-alvo
 * é jornalista e auditor.
 */
export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const term = (q ?? "").trim();

  const [results, counts] = await Promise.all([
    term.length >= 3 ? search(term) : Promise.resolve({ rows: [], error: null }),
    baseCounts(),
  ]);

  const populated = counts.rows.some((r) => Number(r.n) > 0);

  return (
    <>
      <section className="lede">
        <h1>Toda linha aponta para o arquivo público de onde saiu.</h1>
        <p>
          Candidaturas, dinheiro de campanha, cota parlamentar e sanções
          administrativas, cruzados por CPF e CNPJ. Procure por nome, CPF ou
          título eleitoral.
        </p>

        <form className="query" action="/" method="get" role="search">
          <input
            type="search"
            name="q"
            defaultValue={term}
            placeholder="nome, CPF ou título eleitoral"
            aria-label="Nome, CPF ou título eleitoral"
            autoComplete="off"
            minLength={3}
          />
          <button type="submit">Procurar</button>
        </form>
        <p className="hint">Três caracteres no mínimo.</p>

        {counts.error === null && populated && (
          <table className="counts">
            <caption className="hint" style={{ textAlign: "left", paddingBottom: ".4rem" }}>
              No banco agora
            </caption>
            <tbody>
              {counts.rows
                .filter((r) => Number(r.n) > 0)
                .map((r) => (
                  <tr key={r.label}>
                    <th scope="row">{r.label}</th>
                    <td>{count(r.n)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </section>

      {counts.error && <SetupNotice error={counts.error} />}

      {term.length >= 3 && counts.error === null && (
        <section>
          {results.rows.length === 0 ? (
            <div className="empty">
              <h2>Nada encontrado para “{term}”.</h2>
              <p>
                Pode ser grafia, pode ser que o ano dessa candidatura ainda não
                tenha sido coletado. A base cobre só os anos que você carregou.
              </p>
            </div>
          ) : (
            <>
              <p className="hint">
                {count(results.rows.length)}{" "}
                {results.rows.length === 1 ? "candidatura" : "candidaturas"}
              </p>
              <ul className="results">
                {results.rows.map((hit) => (
                  <li key={hit.politician_history_id}>
                    <Link href={`/candidato/${hit.politician_history_id}`}>
                      <span className="results__name">{hit.full_name}</span>
                      <span className="results__meta">
                        <span>{hit.election_year}</span>
                        <span>{hit.office}</span>
                        {hit.uf && <span>{hit.uf}</span>}
                        {hit.party_acronym && <span>{hit.party_acronym}</span>}
                        {hit.result && <span>{hit.result}</span>}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {!counts.error && !populated && <EmptyBase />}
    </>
  );
}

function SetupNotice({ error }: { error: string }) {
  return (
    <div className="empty">
      <h2>Sem conexão com o banco.</h2>
      <p>
        Defina <code>MIRANTE_DATABASE_URL</code> no ambiente e aplique o schema
        com <code>mirante init-db</code>.
      </p>
      <p className="hint">{error}</p>
    </div>
  );
}

function EmptyBase() {
  return (
    <div className="empty">
      <h2>O banco está vazio.</h2>
      <p>
        O schema existe, mas nenhum crawler rodou ainda. A rodada mais rápida,
        que termina em minutos:
      </p>
      <p>
        <code>mirante camara-ceap --years 2026</code>
        <br />
        <code>mirante transparencia-sanctions</code>
        <br />
        <code>mirante rule-sanctioned-counterparty</code>
      </p>
    </div>
  );
}
