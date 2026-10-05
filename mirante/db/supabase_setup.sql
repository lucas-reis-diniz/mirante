-- Configuração específica de Supabase. NÃO faz parte de `mirante init-db`:
-- roda uma vez, no SQL Editor, como postgres.
--
-- Duas credenciais, de propósito:
--   postgres     -> pipeline (escreve). Só no segredo do GitHub Actions.
--   mirante_web  -> interface (só lê). Só na variável de ambiente da Vercel.
-- A garantia de "a interface não escreve" é do servidor, não do código.

-- 1. Papel só-leitura para a interface.
--    Troque a senha antes de rodar; ela não vai para o repositório.
-- CREATE ROLE mirante_web LOGIN PASSWORD 'troque-esta-senha';
GRANT USAGE ON SCHEMA public TO mirante_web;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mirante_web;
-- Tabelas criadas depois pelo pipeline (que roda como postgres) também.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO mirante_web;

-- 2. Garantias que sobrevivem ao pooler. O parâmetro de conexão `options`
--    não passa pelo Supavisor; configuração por papel passa.
ALTER ROLE mirante_web SET default_transaction_read_only = on;
ALTER ROLE mirante_web SET statement_timeout = '15s';

-- 3. Tabelas de votação: RLS ligado, leitura só para a interface. A API REST
--    do Supabase (anon/authenticated) não vê nada. O pipeline é dono das
--    tabelas e não é afetado.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['legislator', 'vote_session', 'vote_subject', 'vote_orientation', 'vote_cast'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS leitura_mirante_web ON public.%I', t);
    EXECUTE format('CREATE POLICY leitura_mirante_web ON public.%I FOR SELECT TO mirante_web USING (true)', t);
  END LOOP;
END $$;

-- Pendente de decisão: as 25 tabelas anteriores estão com RLS desligado.
-- Com RLS desligado, quem tiver a chave pública do projeto consegue ler e
-- escrever nelas pela API REST. Para fechar, aplique o mesmo bloco acima com
-- a lista de todas as tabelas de `public`.
