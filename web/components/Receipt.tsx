import type { Provenance } from "@/lib/queries";
import { dateTime } from "@/lib/format";

/**
 * Uma linha de livro-razão com o recibo do valor.
 *
 * É o elemento central da interface: nenhum número aparece sem a origem a um
 * clique de distância, e o que a origem mostra é exatamente o necessário para
 * NÃO precisar confiar em nós — URL para re-baixar, SHA-256 para comparar,
 * data do acesso.
 *
 * A LINHA INTEIRA é o <summary>, não um botão no canto. Duas razões:
 *
 *  1. Uso: o alvo de clique passa a ser a linha toda, e a pergunta "de onde
 *     veio isso?" se faz onde o olho já está — sobre o valor.
 *  2. Mecânica: o Chrome mantém uma caixa anônima em volta do conteúdo do
 *     <details>, então `display: contents` não promove o corpo a item da
 *     grade da linha — verificado medindo a largura computada, que ficava
 *     presa na primeira coluna. Com a linha dentro do <details>, não existe
 *     grade externa para o corpo precisar atravessar.
 *
 * <details> nativo: abre sem JavaScript, navegável por teclado de graça.
 */
export function Row({
  label,
  value,
  numeric = false,
  source,
}: {
  label: string;
  value: React.ReactNode;
  numeric?: boolean;
  source?: Provenance;
}) {
  const valueClass = numeric ? "cell__value cell__value--num" : "cell__value";

  // Recibo ausente é informação, não vazio.
  //
  // A premissa do projeto é que todo valor tem origem. Quando um não tem, o
  // certo é dizer isso em voz alta — e não renderizar nada, que foi como um
  // bug de coerção de tipo (o driver devolve bigint como string, e
  // `Number.isFinite("1")` é false) apagou TODOS os recibos da página sem
  // levantar erro algum. Vazio silencioso é o modo de falha que esta
  // ferramenta existe para não ter.
  if (!source) {
    return (
      <div className="row">
        <div className="cell">
          <span className="cell__label">{label}</span>
          <span className={valueClass}>{value ?? "—"}</span>
          <span
            className="chip chip--missing"
            title="Valor exibido sem cadeia de proveniência resolvida. Trate com desconfiança."
          >
            sem origem
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="row">
      <details className="receipt">
        <summary className="cell">
          <span className="cell__label">{label}</span>
          <span className={valueClass}>{value ?? "—"}</span>
          <span className="chip">origem</span>
        </summary>
        <ReceiptBody source={source} />
      </details>
    </div>
  );
}

/** Linha sem recibo: nota, explicação, texto que não é dado de fonte. */
export function NoteRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="row">
      <div className="cell">
        <span className="cell__label">{label}</span>
        <span className="cell__value">{children}</span>
      </div>
    </div>
  );
}

export function ReceiptBody({ source }: { source: Provenance }) {
  return (
    <div className="receipt__body">
      <dl>
        <dt>órgão</dt>
        <dd>{source.source_name}</dd>

        <dt>arquivo</dt>
        <dd>
          <a href={source.url} target="_blank" rel="noopener noreferrer">
            {source.url}
          </a>
        </dd>

        <dt>dentro do zip</dt>
        <dd>{source.inner_path}</dd>

        <dt>SHA-256</dt>
        <dd>{source.sha256}</dd>

        <dt>baixado em</dt>
        <dd>{dateTime(source.accessed_at)}</dd>

        <dt>leitor</dt>
        <dd>
          {source.crawler} / {source.parser}
        </dd>
      </dl>
      <p className="receipt__note">
        Baixe o arquivo e compare o hash. Se bater, este valor saiu de lá sem
        passar por edição nossa.
      </p>
    </div>
  );
}

/** Recibo avulso, para onde o rótulo e o valor já estão na página. */
export function Receipt({ source }: { source: Provenance | undefined }) {
  if (!source) {
    return (
      <span className="chip chip--missing" title="Sem cadeia de proveniência resolvida.">
        sem origem
      </span>
    );
  }
  return (
    <details className="receipt">
      <summary className="receipt__standalone">
        <span className="chip">origem</span>
      </summary>
      <ReceiptBody source={source} />
    </details>
  );
}
