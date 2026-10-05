import { SEVERITY_LABELS } from "@/lib/format";

/**
 * Severidade de um sinal.
 *
 * Três valores, só três: baixa, média, alta. O vocabulário é restrito por
 * constraint no banco e por validação no Python — e aqui a tradução também
 * é fechada. Não existe "confirmado" nem "irregular" em lugar nenhum do
 * sistema, de propósito.
 *
 * Alta severidade significa "mais difícil de explicar por acaso", não
 * "crime pior". O rótulo acessível diz isso.
 */
export function Severity({ level }: { level: "low" | "medium" | "high" }) {
  const label = SEVERITY_LABELS[level] ?? level;
  return (
    <span
      className={`sev sev--${level}`}
      title="Quão difícil é explicar o padrão por acaso. Não é juízo sobre gravidade."
    >
      severidade {label}
    </span>
  );
}

/**
 * A ressalva. Aparece ANTES do conteúdo, não depois: é condição de leitura
 * de tudo o que vem a seguir, não um aviso legal de rodapé.
 */
export function Caveat({ children }: { children?: React.ReactNode }) {
  return (
    <div className="caveat">
      <p>
        <strong>Indício não é prova.</strong> O que está aqui são padrões fora da
        curva encontrados em dados públicos. Nada disso é acusação, e nenhum
        sinal foi apurado por pessoa alguma.
      </p>
      {children}
    </div>
  );
}
