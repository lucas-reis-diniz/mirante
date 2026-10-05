-- Mirante — schema Postgres
--
-- Princípio central: nenhuma linha de dado existe sem apontar para a coleta
-- que a originou. Toda tabela de fato carrega provenance_id -> parse ->
-- collection -> source. "De onde veio isso?" é sempre um JOIN, nunca uma
-- questão de confiança.
--
-- Convenções (ver ADs/banco.md):
--   - datas e timestamps em UTC, tipo timestamptz
--   - dinheiro SEMPRE em centavos, bigint. Nunca float.
--   - nomes de tabela e coluna em inglês, singular
--   - identificadores naturais (cpf, cnpj) são texto, só dígitos, zero à esquerda preservado

-- ---------------------------------------------------------------------------
-- 1. CAMADA DE PROVENIÊNCIA
-- ---------------------------------------------------------------------------

-- Um órgão/portal que publica dados. Estável, cadastrado uma vez.
CREATE TABLE IF NOT EXISTS source (
    id              bigserial PRIMARY KEY,
    slug            text        NOT NULL UNIQUE,   -- 'tse', 'portal_transparencia', 'camara'
    name            text        NOT NULL,
    base_url        text        NOT NULL,
    legal_basis     text,                          -- lei/resolução que torna o dado público
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Uma execução de crawler. Agrupa os arquivos baixados naquela rodada.
CREATE TABLE IF NOT EXISTS collection (
    id              bigserial PRIMARY KEY,
    source_id       bigint      NOT NULL REFERENCES source(id),
    crawler         text        NOT NULL,          -- 'tse-candidates'
    crawler_version text        NOT NULL,          -- versão do código que rodou
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    status          text        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'failed')),
    args            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    note            text
);

CREATE INDEX IF NOT EXISTS collection_crawler_idx ON collection (crawler, started_at DESC);

-- Um arquivo concreto baixado da fonte. A âncora de não repúdio:
-- guardamos URL + quando + SHA-256, não o arquivo. Qualquer pessoa re-baixa e confere.
CREATE TABLE IF NOT EXISTS collection_file (
    id              bigserial PRIMARY KEY,
    collection_id   bigint      NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    url             text        NOT NULL,
    accessed_at     timestamptz NOT NULL,
    http_status     int,
    byte_size       bigint,
    sha256          text        NOT NULL,
    media_type      text,
    note            text
);

CREATE INDEX IF NOT EXISTS collection_file_sha_idx ON collection_file (sha256);

-- A leitura de UM arquivo interno (um CSV dentro do zip) por UM parser.
-- É isto que as linhas de dado referenciam.
CREATE TABLE IF NOT EXISTS parse (
    id                  bigserial PRIMARY KEY,
    collection_file_id  bigint      NOT NULL REFERENCES collection_file(id) ON DELETE CASCADE,
    inner_path          text        NOT NULL,      -- 'consulta_cand_2022_SP.csv'
    inner_sha256        text,                      -- hash do CSV de dentro do zip
    parser              text        NOT NULL,      -- 'tse.consulta_cand.v1'
    reference_year      int,
    rows_read           bigint      NOT NULL DEFAULT 0,
    rows_written        bigint      NOT NULL DEFAULT 0,
    rows_rejected       bigint      NOT NULL DEFAULT 0,
    parsed_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS parse_file_idx ON parse (collection_file_id);

-- ---------------------------------------------------------------------------
-- 2. IDENTIDADE
-- ---------------------------------------------------------------------------
-- Identidade é AFIRMAÇÃO NOSSA, não dado da fonte (ver ADs/identidade.md).
-- Match determinístico por título eleitoral e CPF. Na dúvida, não afirma.

CREATE TABLE IF NOT EXISTS people (
    id              bigserial PRIMARY KEY,
    cpf             text UNIQUE CHECK (cpf IS NULL OR cpf ~ '^[0-9]{11}$'),
    voter_id        text UNIQUE,                   -- título eleitoral
    canonical_name  text        NOT NULL,
    provenance_id   bigint      NOT NULL REFERENCES parse(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (cpf IS NOT NULL OR voter_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS people_name_idx ON people USING gin (canonical_name gin_trgm_ops);

-- CPFs descartados por ambiguidade. Registrar o descarte é parte da prova:
-- o que NÃO afirmamos, e por quê.
CREATE TABLE IF NOT EXISTS rejected_cpf (
    id              bigserial PRIMARY KEY,
    cpf             text        NOT NULL,
    reason          text        NOT NULL,          -- 'cpf_in_multiple_voter_ids'
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    provenance_id   bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS rejected_cpf_cpf_idx ON rejected_cpf (cpf);

CREATE TABLE IF NOT EXISTS companies (
    id              bigserial PRIMARY KEY,
    cnpj            text        NOT NULL UNIQUE CHECK (cnpj ~ '^[0-9]{14}$'),
    legal_name      text,
    trade_name      text,
    kind            text[]      NOT NULL DEFAULT '{}',  -- campaign|donor|supplier|sanctioned|contractor
    provenance_id   bigint      NOT NULL REFERENCES parse(id),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS companies_name_idx ON companies USING gin (legal_name gin_trgm_ops);

-- Cadastro e quadro societário vindos da Receita (incremental, não rewrite-only).
CREATE TABLE IF NOT EXISTS company_registry (
    company_id      bigint PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    opened_on       date,
    status          text,
    status_date     date,
    legal_nature    text,
    main_activity   text,
    share_capital_cents bigint,
    municipality    text,
    uf              text,
    provenance_id   bigint      NOT NULL REFERENCES parse(id),
    refreshed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_partner (
    id              bigserial PRIMARY KEY,
    company_id      bigint      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    partner_name    text        NOT NULL,
    partner_cpf_masked text,                       -- Receita entrega mascarado: ***123456**
    role            text,
    joined_on       date,
    provenance_id   bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS company_partner_company_idx ON company_partner (company_id);
CREATE INDEX IF NOT EXISTS company_partner_name_idx ON company_partner USING gin (partner_name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 3. CANDIDATURAS E MANDATOS
-- ---------------------------------------------------------------------------
-- Não existe "o político X". Existe "a candidatura de X na eleição de N".
-- Agregar as duas coisas é decisão de quem consulta, não do banco.

CREATE TABLE IF NOT EXISTS politician_history (
    id                  bigserial PRIMARY KEY,
    person_id           bigint REFERENCES people(id),
    election_year       int         NOT NULL,
    election_round      int,
    sequential_id       text        NOT NULL,      -- SQ_CANDIDATO do TSE
    ballot_name         text,
    full_name           text        NOT NULL,
    party_number        int,
    party_acronym       text,
    coalition           text,
    office              text        NOT NULL,      -- DEPUTADO FEDERAL, PREFEITO...
    uf                  text,
    municipality        text,
    registration_status text,                      -- DEFERIDO, INDEFERIDO...
    result              text,                      -- ELEITO, NAO ELEITO, SUPLENTE
    birth_date          date,
    education           text,
    occupation          text,
    photo_url           text,
    provenance_id       bigint      NOT NULL REFERENCES parse(id),
    UNIQUE (election_year, sequential_id)
);

CREATE INDEX IF NOT EXISTS politician_history_person_idx ON politician_history (person_id);
CREATE INDEX IF NOT EXISTS politician_history_name_idx ON politician_history USING gin (full_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS politician_history_office_idx ON politician_history (election_year, office, uf);

-- Mandato em exercício (Câmara/Senado). Diferente de "foi eleito":
-- cobre suplente que assumiu, licença, cassação.
CREATE TABLE IF NOT EXISTS mandate (
    id              bigserial PRIMARY KEY,
    person_id       bigint REFERENCES people(id),
    house           text        NOT NULL CHECK (house IN ('camara', 'senado')),
    external_id     text        NOT NULL,          -- id do deputado na API da Câmara
    full_name       text        NOT NULL,
    party_acronym   text,
    uf              text,
    started_on      date,
    ended_on        date,
    status          text,
    provenance_id   bigint      NOT NULL REFERENCES parse(id),
    UNIQUE (house, external_id, started_on)
);

CREATE INDEX IF NOT EXISTS mandate_person_idx ON mandate (person_id);

-- ---------------------------------------------------------------------------
-- 4. DINHEIRO DE CAMPANHA
-- ---------------------------------------------------------------------------

-- Cada candidatura abre um CNPJ próprio para a campanha (natureza 409-4).
CREATE TABLE IF NOT EXISTS campaign_org (
    id              bigserial PRIMARY KEY,
    politician_history_id bigint REFERENCES politician_history(id),
    company_id      bigint      NOT NULL REFERENCES companies(id),
    election_year   int         NOT NULL,
    provenance_id   bigint      NOT NULL REFERENCES parse(id),
    UNIQUE (company_id, election_year)
);

CREATE INDEX IF NOT EXISTS campaign_org_history_idx ON campaign_org (politician_history_id);

CREATE TABLE IF NOT EXISTS campaign_donation (
    id                  bigserial PRIMARY KEY,
    campaign_org_id     bigint      NOT NULL REFERENCES campaign_org(id) ON DELETE CASCADE,
    election_year       int         NOT NULL,
    donor_cpf_cnpj      text,
    donor_name          text,
    donor_person_id     bigint REFERENCES people(id),
    donor_company_id    bigint REFERENCES companies(id),
    amount_cents        bigint      NOT NULL,
    donated_on          date,
    resource_origin     text,                      -- FUNDO ELEITORAL, RECURSOS PROPRIOS...
    resource_kind       text,
    receipt_number      text,
    provenance_id       bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS campaign_donation_org_idx    ON campaign_donation (campaign_org_id);
CREATE INDEX IF NOT EXISTS campaign_donation_donor_idx  ON campaign_donation (donor_cpf_cnpj);
CREATE INDEX IF NOT EXISTS campaign_donation_year_idx   ON campaign_donation (election_year);

CREATE TABLE IF NOT EXISTS campaign_expense (
    id                  bigserial PRIMARY KEY,
    campaign_org_id     bigint      NOT NULL REFERENCES campaign_org(id) ON DELETE CASCADE,
    election_year       int         NOT NULL,
    supplier_cpf_cnpj   text,
    supplier_name       text,
    supplier_person_id  bigint REFERENCES people(id),
    supplier_company_id bigint REFERENCES companies(id),
    amount_cents        bigint      NOT NULL,
    contracted_on       date,
    description         text,
    expense_kind        text,
    document_number     text,
    provenance_id       bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS campaign_expense_org_idx      ON campaign_expense (campaign_org_id);
CREATE INDEX IF NOT EXISTS campaign_expense_supplier_idx ON campaign_expense (supplier_cpf_cnpj);
CREATE INDEX IF NOT EXISTS campaign_expense_desc_idx     ON campaign_expense USING gin (description gin_trgm_ops);

-- Regime de caixa: quando o dinheiro de fato saiu (pode ser em parcelas).
CREATE TABLE IF NOT EXISTS campaign_expense_payment (
    id                  bigserial PRIMARY KEY,
    campaign_expense_id bigint REFERENCES campaign_expense(id) ON DELETE CASCADE,
    campaign_org_id     bigint      NOT NULL REFERENCES campaign_org(id) ON DELETE CASCADE,
    amount_cents        bigint      NOT NULL,
    paid_on             date,
    payment_method      text,
    provenance_id       bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS campaign_payment_expense_idx ON campaign_expense_payment (campaign_expense_id);

CREATE TABLE IF NOT EXISTS declared_asset (
    id                  bigserial PRIMARY KEY,
    politician_history_id bigint    NOT NULL REFERENCES politician_history(id) ON DELETE CASCADE,
    election_year       int         NOT NULL,
    asset_kind          text,
    description         text,
    value_cents         bigint      NOT NULL,
    provenance_id       bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS declared_asset_history_idx ON declared_asset (politician_history_id);

CREATE TABLE IF NOT EXISTS social_media (
    id                  bigserial PRIMARY KEY,
    politician_history_id bigint    NOT NULL REFERENCES politician_history(id) ON DELETE CASCADE,
    network             text,                      -- facebook|instagram|x|tiktok|site
    url                 text        NOT NULL,
    handle              text,
    provenance_id       bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS social_media_history_idx ON social_media (politician_history_id);

-- ---------------------------------------------------------------------------
-- 5. DINHEIRO PÚBLICO FORA DA CAMPANHA
-- ---------------------------------------------------------------------------

-- Cota parlamentar (CEAP). Base pequena, mensal, excelente para detecção.
CREATE TABLE IF NOT EXISTS parliamentary_expense (
    id              bigserial PRIMARY KEY,
    mandate_id      bigint REFERENCES mandate(id),
    house           text        NOT NULL,
    external_id     text        NOT NULL,          -- id do parlamentar na origem
    full_name       text        NOT NULL,
    party_acronym   text,
    uf              text,
    reference_year  int         NOT NULL,
    reference_month int         NOT NULL CHECK (reference_month BETWEEN 1 AND 12),
    category        text,                          -- DIVULGACAO DA ATIVIDADE PARLAMENTAR...
    supplier_name   text,
    supplier_cpf_cnpj text,
    supplier_company_id bigint REFERENCES companies(id),
    document_number text,
    issued_on       date,
    amount_cents    bigint      NOT NULL,
    reimbursed_cents bigint,
    document_url    text,
    provenance_id   bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS parl_expense_person_idx   ON parliamentary_expense (house, external_id, reference_year);
CREATE INDEX IF NOT EXISTS parl_expense_supplier_idx ON parliamentary_expense (supplier_cpf_cnpj);
CREATE INDEX IF NOT EXISTS parl_expense_category_idx ON parliamentary_expense (category);

-- Emendas parlamentares: quem destinou quanto, para quem, e quanto saiu de fato.
CREATE TABLE IF NOT EXISTS earmark (
    id                  bigserial PRIMARY KEY,
    code                text        NOT NULL,
    reference_year      int         NOT NULL,
    author_name         text,
    author_person_id    bigint REFERENCES people(id),
    author_kind         text,                      -- individual|bancada|comissao|relator
    function            text,
    subfunction         text,
    beneficiary_name    text,
    beneficiary_cnpj    text,
    beneficiary_company_id bigint REFERENCES companies(id),
    uf                  text,
    municipality        text,
    committed_cents     bigint      NOT NULL DEFAULT 0,  -- empenhado
    settled_cents       bigint      NOT NULL DEFAULT 0,  -- liquidado
    paid_cents          bigint      NOT NULL DEFAULT 0,  -- pago
    provenance_id       bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS earmark_author_idx      ON earmark USING gin (author_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS earmark_beneficiary_idx ON earmark (beneficiary_cnpj);
CREATE INDEX IF NOT EXISTS earmark_year_idx        ON earmark (reference_year);

-- CEIS/CNEP: quem está impedido de contratar com o governo ou punido por corrupção.
CREATE TABLE IF NOT EXISTS sanction (
    id              bigserial PRIMARY KEY,
    registry        text        NOT NULL CHECK (registry IN ('CEIS', 'CNEP')),
    cpf_cnpj        text,
    person_id       bigint REFERENCES people(id),
    company_id      bigint REFERENCES companies(id),
    sanctioned_name text        NOT NULL,
    sanction_kind   text,
    legal_basis     text,
    started_on      date,
    ended_on        date,
    sanctioning_body text,
    process_number  text,
    provenance_id   bigint      NOT NULL REFERENCES parse(id)
);

CREATE INDEX IF NOT EXISTS sanction_doc_idx ON sanction (cpf_cnpj);

-- ---------------------------------------------------------------------------
-- 6. DADOS DERIVADOS — SINAIS DE ALERTA
-- ---------------------------------------------------------------------------
-- ATENÇÃO: estas tabelas NÃO vêm de nenhuma fonte. São afirmações do nosso
-- código sobre dados já coletados. Mesma disciplina de proveniência: toda
-- evidência aponta para uma linha real, e severity só existe como
-- low/medium/high. NUNCA "confirmado", NUNCA "fraude". Indício não é prova.

CREATE TABLE IF NOT EXISTS rule_run (
    id              bigserial PRIMARY KEY,
    rule            text        NOT NULL,          -- 'disproportionate_expense'
    rule_version    text        NOT NULL,
    params          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    rows_scanned    bigint      NOT NULL DEFAULT 0,
    signals_emitted bigint      NOT NULL DEFAULT 0,
    status          text        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'failed'))
);

CREATE INDEX IF NOT EXISTS rule_run_rule_idx ON rule_run (rule, started_at DESC);

CREATE TABLE IF NOT EXISTS signal (
    id              bigserial PRIMARY KEY,
    rule_run_id     bigint      NOT NULL REFERENCES rule_run(id) ON DELETE CASCADE,
    rule            text        NOT NULL,
    severity        text        NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    headline        text        NOT NULL,          -- frase factual, nunca acusatória
    amount_cents    bigint,
    reference_year  int,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS signal_run_idx      ON signal (rule_run_id);
CREATE INDEX IF NOT EXISTS signal_severity_idx ON signal (rule, severity, amount_cents DESC);

-- Quem aparece no sinal. Papel explícito: estar aqui não implica culpa.
CREATE TABLE IF NOT EXISTS signal_actor (
    id              bigserial PRIMARY KEY,
    signal_id       bigint      NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    role            text        NOT NULL,          -- candidate|donor|supplier|intermediary
    person_id       bigint REFERENCES people(id),
    company_id      bigint REFERENCES companies(id),
    politician_history_id bigint REFERENCES politician_history(id),
    display_name    text        NOT NULL
);

CREATE INDEX IF NOT EXISTS signal_actor_signal_idx ON signal_actor (signal_id);
CREATE INDEX IF NOT EXISTS signal_actor_person_idx ON signal_actor (person_id);

-- Cada evidência aponta para uma linha REAL de uma tabela de fato,
-- que por sua vez tem seu próprio provenance_id.
CREATE TABLE IF NOT EXISTS signal_evidence (
    id              bigserial PRIMARY KEY,
    signal_id       bigint      NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    table_name      text        NOT NULL,          -- 'campaign_expense'
    row_id          bigint      NOT NULL,
    provenance_id   bigint      NOT NULL REFERENCES parse(id),
    note            text
);

CREATE INDEX IF NOT EXISTS signal_evidence_signal_idx ON signal_evidence (signal_id);

-- Vínculo possível, NÃO confirmado: candidato que aparece no quadro societário
-- de empresa que recebeu dinheiro de campanha. O CPF do sócio vem mascarado,
-- então o match é nome + 6 dígitos visíveis. Fica fora de people/signal_actor
-- de propósito: é hipótese, não identidade.
CREATE TABLE IF NOT EXISTS candidate_supplier_partner (
    id                  bigserial PRIMARY KEY,
    politician_history_id bigint    NOT NULL REFERENCES politician_history(id),
    company_id          bigint      NOT NULL REFERENCES companies(id),
    match_basis         text        NOT NULL,      -- 'name_exact+cpf6'
    confidence          text        NOT NULL DEFAULT 'possible'
                        CHECK (confidence IN ('possible')),
    paid_by_own_campaign boolean    NOT NULL DEFAULT false,
    total_received_cents bigint     NOT NULL DEFAULT 0,
    rule_run_id         bigint      NOT NULL REFERENCES rule_run(id) ON DELETE CASCADE,
    UNIQUE (politician_history_id, company_id, rule_run_id)
);

-- ---------------------------------------------------------------------------
-- 7. VISÕES DE CONVENIÊNCIA
-- ---------------------------------------------------------------------------

-- "De onde veio esta linha?" resolvido em um SELECT.
CREATE OR REPLACE VIEW provenance_chain AS
SELECT
    p.id            AS provenance_id,
    p.parser,
    p.inner_path,
    p.reference_year,
    cf.url,
    cf.sha256,
    cf.accessed_at,
    c.crawler,
    c.crawler_version,
    s.slug          AS source_slug,
    s.name          AS source_name
FROM parse p
JOIN collection_file cf ON cf.id = p.collection_file_id
JOIN collection      c  ON c.id  = cf.collection_id
JOIN source          s  ON s.id  = c.source_id;
