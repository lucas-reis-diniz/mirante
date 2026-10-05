import Link from "next/link";
import { legislators, voteCoverage } from "@/lib/queries";
import { count, date, UFS } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Ponto de entrada de "como votou": a pessoa escolhe o estado e acha o
 * deputado dela. O estado vem primeiro porque é como o eleitor pensa — ele
 * votou em alguém do estado dele, e muitas vezes não lembra o nome exato.
 *
 * Formulário GET, como na busca: o filtro vive na URL e o resultado é
 * compartilhável.
 */
export default async function Deputies({
  searchParams,
}: {
  searchParams: Promise<{ uf?: string; q?: string }>;
}) {
  const { uf: rawUf, q } = await searchParams;
  const uf = rawUf && (UFS as readonly string[]).includes(rawUf) ? rawUf : undefined;
  const term = (q ?? "").trim() || undefined;

  const [found, coverage] = await Promise.all([
    uf || term ? legislators({ uf, term }) : Promise.resolve({ rows: [], error: null }),
    voteCoverage(),
  ]);
  const cov = coverage.rows[0];
  const empty = !coverage.error && (!cov || Number(cov.sessions) === 0);

  return (
    <>
      <section className="lede">
        <h1>Como votou o seu deputado.</h1>
        <p>
          Cada voto nominal registrado na Câmara, ao lado do que o partido dele
          e o Governo orientaram. Escolha o estado ou procure pelo nome.
        </p>

        <form className="query query--split" action="/deputados" method="get" role="search">
          <select name="uf" defaultValue={uf ?? ""} aria-label="Estado">
            <option value="">Estado</option>
            {UFS.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
          <input
            type="search"
            name="q"
            defaultValue={term ?? ""}
            placeholder="nome do deputado (opcional)"
            aria-label="Nome do deputado"
            autoComplete="off"
          />
          <button type="submit">Ver</button>
        </form>

        {cov && Number(cov.sessions) > 0 && (
          <p className="hint">
            {count(cov.votes)} votos em {count(cov.sessions)} votações, de{" "}
            {date(cov.first)} a {date(cov.last)}. Atualizado todo dia a partir dos
            dados abertos da Câmara.
          </p>
        )}
      </section>

      {coverage.error && (
        <div className="empty">
          <h2>Sem conexão com o banco.</h2>
          <p className="hint">{coverage.error}</p>
        </div>
      )}

      {empty && (
        <div className="empty">
          <h2>Nenhuma votação carregada ainda.</h2>
          <p>
            Rode <code>mirante camara-votes --years 2025 2026</code> ou espere a
            carga diária agendada.
          </p>
        </div>
      )}

      {(uf || term) && !coverage.error && !empty && (
        <section>
          {found.error ? (
            <p className="caveat" style={{ borderLeftColor: "var(--crimson)" }}>
              <strong>A consulta falhou.</strong>
              <br />
              <span className="hint">{found.error}</span>
            </p>
          ) : found.rows.length === 0 ? (
            <div className="empty">
              <h2>Nenhum deputado encontrado.</h2>
              <p>
                A lista mostra quem registrou voto nominal no período coletado.
                Confira a grafia, ou tente só o estado.
              </p>
            </div>
          ) : (
            <>
              <p className="hint">
                {count(found.rows.length)}{" "}
                {found.rows.length === 1 ? "deputado" : "deputados"}
                {uf ? ` com voto registrado por ${uf}` : ""}
              </p>
              <ul className="results">
                {found.rows.map((d) => (
                  <li key={d.external_id}>
                    <Link href={`/deputados/${d.external_id}`}>
                      <span className="results__name">{d.full_name}</span>
                      <span className="results__meta">
                        {d.last_party && <span>{d.last_party}</span>}
                        {d.last_uf && <span>{d.last_uf}</span>}
                        <span>
                          {count(d.votes)} {Number(d.votes) === 1 ? "voto" : "votos"}
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </>
  );
}
