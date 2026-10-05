/**
 * Formatação para leitura. O banco guarda dinheiro em centavos como bigint,
 * e o driver devolve bigint como string para não perder precisão — então a
 * conversão acontece aqui, uma vez, e nunca com `parseFloat` sobre centavos.
 */

export function brl(cents: string | number | null | undefined): string {
  if (cents === null || cents === undefined || cents === "") return "—";
  const value = typeof cents === "string" ? BigInt(cents) : BigInt(Math.round(cents));
  const negative = value < 0n;
  const abs = negative ? -value : value;
  const reais = abs / 100n;
  const centavos = abs % 100n;
  const formatted = reais.toLocaleString("pt-BR");
  return `${negative ? "-" : ""}R$ ${formatted},${centavos.toString().padStart(2, "0")}`;
}

export function count(n: string | number | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  return Number(n).toLocaleString("pt-BR");
}

export function date(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function dateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("pt-BR", { timeZone: "UTC" })} ${d.toLocaleTimeString(
    "pt-BR",
    { hour: "2-digit", minute: "2-digit", timeZone: "UTC" },
  )} UTC`;
}

export function cnpj(value: string | null | undefined): string {
  if (!value || value.length !== 14) return value ?? "—";
  return value.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5");
}

/** Rótulos em português para os nomes internos das regras. */
export const RULE_LABELS: Record<string, string> = {
  disproportionate_expense: "Despesa desproporcional",
  circular_donations: "Doação circular",
  sanctioned_counterparty: "Contraparte sancionada",
};

export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? rule;
}

export const SEVERITY_LABELS: Record<string, string> = {
  high: "alta",
  medium: "média",
  low: "baixa",
};

/**
 * Sigla de proposição para leitura. Só as que aparecem como objeto principal
 * de votação; o resto fica com a sigla da Câmara.
 */
export const PROPOSITION_KINDS: Record<string, string> = {
  PEC: "Proposta de Emenda à Constituição",
  PLP: "Projeto de Lei Complementar",
  PL: "Projeto de Lei",
  MPV: "Medida Provisória",
  PLV: "Projeto de Lei de Conversão",
  PDL: "Projeto de Decreto Legislativo",
  PRC: "Projeto de Resolução",
  REQ: "Requerimento",
  PAR: "Parecer",
};

export function propositionLabel(
  kind: string | null,
  number: number | null,
  year: number | null,
): string | null {
  if (!kind) return null;
  const name = PROPOSITION_KINDS[kind] ?? kind;
  if (number === null) return name;
  return year ? `${name} ${number}/${year}` : `${name} nº ${number}`;
}

/** Página humana da proposição na Câmara (a URL da API devolve JSON). */
export function camaraPropositionUrl(id: string | null): string | null {
  if (!id || !/^\d+$/.test(id)) return null;
  return `https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao=${id}`;
}

export const UFS = [
  "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
  "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
] as const;
