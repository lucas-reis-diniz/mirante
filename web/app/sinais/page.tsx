import Link from "next/link";
import { signals } from "@/lib/queries";
import { brl, count, ruleLabel, RULE_LABELS } from "@/lib/format";
import { Severity, Caveat } from "@/components/Severity";

export const dynamic = "force-dynamic";

const SEVERITIES = ["high", "medium", "low"] as const;

export default async function Signals({
  searchParams,
}: {
  searchParams: Promise<{ regra?: string; severidade?: string }>;
}) {
  const { regra, severidade } = await searchParams;

  const rule = regra && regra in RULE_LABELS ? regra : undefined;
  const severity =
    severidade && (SEVERITIES as readonly string[]).includes(severidade)
      ? severidade
      : undefined;

  const found = await signals({ rule, severity, limit: 100 });

  return (
    <>
      <section className="record">
        <h1>Sinais de alerta</h1>
        <div className="record__meta">
          <span>Padrões fora da curva encontrados nos dados coletados</span>
        </div>
      </section>

      <Caveat>
        <p>
          Severidade alta quer dizer “mais difícil de explicar por acaso” — ciclo
          curto, pagamento durante sanção vigente, desvio maior da mediana. Não é
          juízo sobre gravidade, e cada sinal carrega a limitação da regra que o
          gerou.
        </p>
      </Caveat>

      <nav aria-label="Filtros" className="ledger">
        <h2>Filtrar</h2>
        <div className="row">
          <div className="cell">
            <span className="cell__label">Regra</span>
            <span className="cell__value">
            <FilterLinks
              current={rule}
              param="regra"
              severity={severity}
              options={Object.entries(RULE_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
            </span>
          </div>
        </div>
        <div className="row">
          <div className="cell">
            <span className="cell__label">Severidade</span>
            <span className="cell__value">
            <FilterLinks
              current={severity}
              param="severidade"
              rule={rule}
              options={SEVERITIES.map((value) => ({
                value,
                label: value === "high" ? "alta" : value === "medium" ? "média" : "baixa",
              }))}
            />
            </span>
          </div>
        </div>
      </nav>

      {found.error ? (
        <div className="empty">
          <h2>Sem conexão com o banco.</h2>
          <p className="hint">{found.error}</p>
        </div>
      ) : found.rows.length === 0 ? (
        <div className="empty">
          <h2>Nenhum sinal.</h2>
          <p>
            Ou as regras não rodaram ainda, ou nada passou do limiar com os
            filtros atuais.
          </p>
          <p>
            <code>mirante rule-sanctioned-counterparty</code>
            <br />
            <code>mirante rule-disproportionate-expense</code>
          </p>
        </div>
      ) : (
        <>
          <p className="hint">
            {count(found.rows.length)} {found.rows.length === 1 ? "sinal" : "sinais"}
            {found.rows.length === 100 && " (limite da página)"}
          </p>
          <ul className="results">
            {found.rows.map((sig) => (
              <li key={sig.id}>
                <Link href={`/sinais/${sig.id}`}>
                  <span className="results__name">{sig.headline}</span>
                  <span className="results__meta">
                    <Severity level={sig.severity} />
                    <span>{ruleLabel(sig.rule)}</span>
                    {sig.reference_year && <span>{sig.reference_year}</span>}
                    {sig.amount_cents && <span>{brl(sig.amount_cents)}</span>}
                    {sig.actors && sig.actors.length > 0 && (
                      <span>{sig.actors.slice(0, 2).join(", ")}</span>
                    )}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function FilterLinks({
  options,
  current,
  param,
  rule,
  severity,
}: {
  options: { value: string; label: string }[];
  current?: string;
  param: "regra" | "severidade";
  rule?: string;
  severity?: string;
}) {
  const build = (value?: string) => {
    const next = new URLSearchParams();
    const merged = {
      regra: param === "regra" ? value : rule,
      severidade: param === "severidade" ? value : severity,
    };
    if (merged.regra) next.set("regra", merged.regra);
    if (merged.severidade) next.set("severidade", merged.severidade);
    const qs = next.toString();
    return qs ? `/sinais?${qs}` : "/sinais";
  };

  return (
    <span style={{ display: "flex", flexWrap: "wrap", gap: ".25rem .9rem" }}>
      <Link
        href={build(undefined)}
        style={{ fontWeight: current ? 400 : 600, textDecoration: current ? undefined : "none" }}
      >
        todas
      </Link>
      {options.map((opt) => {
        const active = current === opt.value;
        return (
          <Link
            key={opt.value}
            href={build(active ? undefined : opt.value)}
            aria-current={active ? "true" : undefined}
            style={{ fontWeight: active ? 600 : 400, textDecoration: active ? "none" : undefined }}
          >
            {opt.label}
          </Link>
        );
      })}
    </span>
  );
}
