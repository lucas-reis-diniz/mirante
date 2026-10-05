import { Pool, type QueryResultRow } from "pg";

/**
 * Conexão só-leitura com o Postgres do Mirante.
 *
 * Três camadas de garantia, porque uma só não basta:
 *
 * 1. `default_transaction_read_only=on` na própria sessão. Qualquer INSERT,
 *    UPDATE ou DELETE que escape para cá morre no banco, não na revisão de
 *    código.
 * 2. `statement_timeout`. Consulta mal escrita sobre tabela de milhões de
 *    linhas derruba a instância inteira; com teto, derruba só a requisição.
 * 3. A API exposta por este módulo não tem caminho de escrita.
 *
 * O ideal em produção é também um ROLE sem privilégio de escrita:
 *
 *   CREATE ROLE mirante_web LOGIN PASSWORD '...';
 *   GRANT CONNECT ON DATABASE mirante TO mirante_web;
 *   GRANT USAGE ON SCHEMA public TO mirante_web;
 *   GRANT SELECT ON ALL TABLES IN SCHEMA public TO mirante_web;
 *
 * Aí a garantia passa a ser do servidor, não da aplicação.
 */

const connectionString = process.env.MIRANTE_DATABASE_URL;

/**
 * TLS resolvido em três níveis, nesta ordem:
 *
 *   1. `sslmode` explícito na URL ganha sempre. Quem escreveu `sslmode=disable`
 *      ou `sslmode=verify-full` sabe o que quer, e o driver já sabe aplicar.
 *   2. Host local (localhost, 127.0.0.1, ::1, *.localhost) -> sem TLS. Postgres
 *      de desenvolvimento normalmente não tem certificado.
 *   3. Qualquer outro host -> TLS.
 *
 * A primeira versão disto testava só a substring "localhost", e conectar em
 * 127.0.0.1 passava a exigir SSL contra um servidor local sem certificado.
 */
function isLocalHost(url: string): boolean {
  try {
    const host = new URL(url).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return (
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "::1" ||
      host.endsWith(".localhost")
    );
  } catch {
    return false;
  }
}

function sslSetting(url: string): false | { rejectUnauthorized: boolean } | undefined {
  // sslmode na URL: deixa o driver decidir, não sobrescreve.
  if (/[?&]sslmode=/i.test(url)) return undefined;
  if (isLocalHost(url)) return false;
  // Provedores gerenciados (Supabase, Neon, RDS) usam cadeia própria. Para
  // verificação estrita, passe a CA do provedor via sslmode=verify-full na URL.
  return { rejectUnauthorized: false };
}

const BEHIND_POOLER = /pooler\.supabase\.com|:6543(\/|$)/i.test(connectionString ?? "");

let pool: Pool | null = null;

function getPool(): Pool {
  if (!connectionString) {
    throw new Error("MIRANTE_DATABASE_URL não definida");
  }
  if (!pool) {
    pool = new Pool({
      connectionString,
      max: 4,
      idleTimeoutMillis: 30_000,
      connectionTimeoutMillis: 10_000,
      ssl: sslSetting(connectionString),
      // Atrás de pooler (Supavisor, PgBouncer) o parâmetro de inicialização
      // `options` é recusado ou se perde entre transações. Nesse caso a
      // mesma garantia vem do servidor: o papel mirante_web tem
      // default_transaction_read_only e statement_timeout definidos por
      // ALTER ROLE, além de não ter privilégio de escrita algum.
      ...(BEHIND_POOLER
        ? {}
        : { options: "-c default_transaction_read_only=on -c statement_timeout=15000" }),
    });
  }
  return pool;
}

/** `true` quando há banco configurado — a interface degrada sem derrubar. */
export const isConfigured = Boolean(connectionString);

export async function query<T extends QueryResultRow>(
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  const result = await getPool().query<T>(sql, params);
  return result.rows;
}

export async function queryOne<T extends QueryResultRow>(
  sql: string,
  params: unknown[] = [],
): Promise<T | null> {
  const rows = await query<T>(sql, params);
  return rows[0] ?? null;
}

/** Erro de conexão não deve virar tela branca: a página mostra o estado. */
export async function tryQuery<T extends QueryResultRow>(
  sql: string,
  params: unknown[] = [],
): Promise<{ rows: T[]; error: string | null }> {
  if (!isConfigured) {
    return { rows: [], error: "MIRANTE_DATABASE_URL não definida" };
  }
  try {
    return { rows: await query<T>(sql, params), error: null };
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    console.error("[mirante] consulta falhou:", message);
    return { rows: [], error: message };
  }
}
