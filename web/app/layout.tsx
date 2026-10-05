import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

/**
 * Tipografia: uma só superfamília (IBM Plex) em três vozes atribuídas por
 * semântica — serif para título, sans para interface, mono para
 * identificador, hash e dinheiro, onde o alinhamento de dígito é informação.
 *
 * Carregada por stylesheet em runtime, não por `next/font/google`. O motivo é
 * prático: `next/font` baixa o arquivo em tempo de build, então o build passa
 * a exigir rede até o Google. Qualquer CI fechado, container com allowlist ou
 * máquina offline quebra. A fonte é progressiva: se não carregar, o
 * `font-family` cai para a pilha do sistema e a página continua legível.
 */
export const metadata: Metadata = {
  title: "Mirante — dados públicos sobre política brasileira",
  description:
    "Consulta a candidaturas, dinheiro de campanha e sanções, com a fonte de cada valor. Gera indícios, não conclusões.",
};

const FONT_HREF =
  "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Serif:wght@500;600&display=swap";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link rel="stylesheet" href={FONT_HREF} />
      </head>
      <body>
        <header className="topbar">
          <div className="topbar__inner">
            <Link href="/" className="wordmark">
              Mirante
            </Link>
            <nav>
              <Link href="/">Busca</Link>
              <Link href="/sinais">Sinais</Link>
              <a
                href="https://github.com/lucas-reis-diniz/mirante"
                target="_blank"
                rel="noopener noreferrer"
              >
                Código
              </a>
            </nav>
          </div>
        </header>

        <main className="shell">{children}</main>

        <footer className="site">
          <div className="shell">
            <p>
              Todas as fontes são públicas por determinação legal. Cada valor
              exibido aponta para o arquivo de onde saiu, com hash e data, e
              qualquer pessoa pode re-baixar e conferir.
            </p>
            <p>
              O resultado é indício, não prova. Nada aqui deve virar acusação
              pública sem apuração formal.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
