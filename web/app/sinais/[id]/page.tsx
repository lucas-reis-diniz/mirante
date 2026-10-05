import { notFound } from "next/navigation";
import { signalDetail, evidence } from "@/lib/queries";
import { brl, dateTime, ruleLabel } from "@/lib/format";
import { Row, NoteRow } from "@/components/Receipt";
import { Severity, Caveat } from "@/components/Severity";

export const dynamic = "force-dynamic";

/**
 * O detalhe de um sinal. É a página que mais importa do ponto de vista ético,
 * porque é onde alguém pode decidir publicar algo sobre uma pessoa real.
 *
 * Por isso ela mostra, na mesma tela e com o mesmo peso visual:
 *   - o que a regra achou
 *   - o que a regra NÃO sabe (a limitação, vinda do `detail` do sinal)
 *   - qual versão da regra e com quais parâmetros rodou
 *   - cada linha de evidência, com a URL e o hash do arquivo de origem
 *
 * A limitação não é rodapé. Quem exportar a tabela leva a ressalva junto,
 * porque ela é gravada no próprio sinal pelo código que o gerou.
 */
export default async function SignalPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const signalId = Number(id);
  if (!Number.isInteger(signalId) || signalId <= 0) notFound();

  const [found, proof] = await Promise.all([signalDetail(signalId), evidence(signalId)]);

  if (found.error) {
    return (
      <div className="empty">
        <h2>Sem conexão com o banco.</h2>
        <p className="hint">{found.error}</p>
      </div>
    );
  }

  const sig = found.rows[0];
  if (!sig) notFound();

  const detail = (sig.detail ?? {}) as Record<string, unknown>;
  const caveat = typeof detail.caveat === "string" ? detail.caveat : null;

  // A ressalva já aparece em bloco próprio; o resto do detail vai na tabela.
  const facts = Object.entries(detail).filter(([k]) => k !== "caveat");

  return (
    <>
      <section className="record">
        <h1>{sig.headline}</h1>
        <div className="record__meta">
          <Severity level={sig.severity} />
          <span>{ruleLabel(sig.rule)}</span>
          {sig.reference_year && <span>{sig.reference_year}</span>}
          {sig.amount_cents && <span>{brl(sig.amount_cents)}</span>}
        </div>
      </section>

      <Caveat>
        {caveat && (
          <p>
            <strong>O que esta regra não sabe:</strong> {caveat}
          </p>
        )}
      </Caveat>

      {sig.actors && sig.actors.length > 0 && (
        <section className="ledger">
          <h2>Quem aparece</h2>
          {sig.actors.map((name) => (
            <NoteRow label="Mencionado" key={name}>
              {name}
            </NoteRow>
          ))}
          <NoteRow label="Nota">
            <span className="hint">
              Aparecer aqui não implica irregularidade de parte alguma.
            </span>
          </NoteRow>
        </section>
      )}

      {facts.length > 0 && (
        <section className="ledger">
          <h2>Como a regra chegou aqui</h2>
          {facts.map(([key, value]) => (
            <NoteRow label={key} key={key}>
              <span className="cell__value--num">{renderValue(value)}</span>
            </NoteRow>
          ))}
        </section>
      )}

      <section className="ledger">
        <h2>Execução da regra</h2>
        <NoteRow label="Regra">
          <span className="cell__value--num">
            {sig.rule} v{sig.rule_version}
          </span>
        </NoteRow>
        <NoteRow label="Rodou em">
          <span className="cell__value--num">{dateTime(sig.started_at)}</span>
        </NoteRow>
        <NoteRow label="Parâmetros">
          <span className="cell__value--num">{renderValue(sig.params)}</span>
        </NoteRow>
        <NoteRow label="Nota">
          <span className="hint">
            Sinal gerado com limiar de 15x não é comparável a um gerado com 30x.
            Por isso a versão e os parâmetros ficam gravados.
          </span>
        </NoteRow>
      </section>

      <section className="ledger">
        <h2>
          Evidência {proof.rows.length > 0 && `(${proof.rows.length})`}
        </h2>
        {proof.rows.length === 0 ? (
          <NoteRow label="Evidência">
            Nenhuma evidência registrada. Isso não deveria acontecer: o código
            recusa gravar sinal sem evidência.
          </NoteRow>
        ) : (
          proof.rows.map((ev, i) => (
            <Row
              key={`${ev.table_name}-${ev.row_id}-${i}`}
              label={ev.note ?? "linha de origem"}
              value={`${ev.table_name} #${ev.row_id}`}
              numeric
              source={ev}
            />
          ))
        )}
      </section>
    </>
  );
}

function renderValue(value: unknown): React.ReactNode {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "sim" : "não";
  if (typeof value === "number" || typeof value === "string") return String(value);
  if (Array.isArray(value)) {
    return value.length === 0 ? "—" : value.map((v) => String(v)).join(", ");
  }
  return (
    <code style={{ fontSize: ".8rem", overflowWrap: "anywhere" }}>
      {JSON.stringify(value)}
    </code>
  );
}
