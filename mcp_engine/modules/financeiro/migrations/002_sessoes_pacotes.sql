CREATE TABLE modelo_pacote (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    modo_cobranca TEXT NOT NULL CHECK (modo_cobranca IN ('antecipado', 'por_sessao', 'parcelado', 'mensal')),
    n_sessoes INTEGER NOT NULL CHECK (n_sessoes >= 1),
    preco_total_centavos INTEGER CHECK (preco_total_centavos IS NULL OR preco_total_centavos > 0),
    preco_unitario_centavos INTEGER CHECK (preco_unitario_centavos IS NULL OR preco_unitario_centavos > 0),
    n_parcelas INTEGER NOT NULL DEFAULT 1 CHECK (n_parcelas BETWEEN 1 AND 24),
    intervalo_dias INTEGER NOT NULL DEFAULT 30 CHECK (intervalo_dias BETWEEN 1 AND 366),
    validade_dias INTEGER NOT NULL DEFAULT 90 CHECK (validade_dias BETWEEN 1 AND 1100),
    falta_consome INTEGER NOT NULL DEFAULT 0 CHECK (falta_consome IN (0, 1)),
    falta_cobrada INTEGER NOT NULL DEFAULT 0 CHECK (falta_cobrada IN (0, 1)),
    antecedencia_horas INTEGER NOT NULL DEFAULT 24 CHECK (antecedencia_horas >= 0),
    vigente_de TEXT NOT NULL CHECK (date(vigente_de) = vigente_de),
    vigente_ate TEXT CHECK (vigente_ate IS NULL OR date(vigente_ate) = vigente_ate),
    criado_por TEXT NOT NULL,
    CHECK (vigente_ate IS NULL OR vigente_ate >= vigente_de),
    CHECK (preco_total_centavos IS NOT NULL OR preco_unitario_centavos IS NOT NULL)
) STRICT;

CREATE TABLE pacote (
    id INTEGER PRIMARY KEY,
    paciente_id INTEGER NOT NULL REFERENCES paciente_ref (id),
    modelo_id INTEGER NOT NULL REFERENCES modelo_pacote (id),
    preco_acordado_centavos INTEGER NOT NULL CHECK (preco_acordado_centavos > 0),
    sessoes_totais INTEGER NOT NULL CHECK (sessoes_totais >= 1),
    inicio TEXT NOT NULL CHECK (date(inicio) = inicio),
    validade TEXT NOT NULL CHECK (date(validade) = validade),
    status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo', 'concluido', 'cancelado')),
    operacao_id TEXT UNIQUE,
    criado_por TEXT NOT NULL,
    criado_em TEXT NOT NULL
) STRICT;

CREATE TABLE sessao (
    id INTEGER PRIMARY KEY,
    agenda_id TEXT UNIQUE,
    paciente_id INTEGER NOT NULL REFERENCES paciente_ref (id),
    profissional_id INTEGER REFERENCES profissional (id),
    servico TEXT,
    inicio_utc TEXT NOT NULL,
    status_agenda TEXT NOT NULL DEFAULT 'SCHEDULED'
        CHECK (status_agenda IN ('SCHEDULED', 'COMPLETED', 'CANCELLED')),
    valor_previsto_centavos INTEGER CHECK (valor_previsto_centavos IS NULL OR valor_previsto_centavos >= 0),
    valor_previsto_origem TEXT CHECK (valor_previsto_origem IS NULL
        OR valor_previsto_origem IN ('paciente', 'pacote', 'servico')),
    pacote_id INTEGER REFERENCES pacote (id),
    sincronizado_em TEXT NOT NULL
) STRICT;
CREATE INDEX sessao_paciente ON sessao (paciente_id, inicio_utc);
CREATE INDEX sessao_pacote ON sessao (pacote_id);

CREATE TABLE sessao_ocorrencia (
    id INTEGER PRIMARY KEY,
    sessao_id INTEGER NOT NULL REFERENCES sessao (id),
    tipo TEXT NOT NULL CHECK (tipo IN ('falta', 'falta_justificada', 'cancelamento_tardio')),
    motivo TEXT,
    registrado_por TEXT NOT NULL,
    registrado_em TEXT NOT NULL,
    estorno_de INTEGER REFERENCES sessao_ocorrencia (id),
    operacao_id TEXT UNIQUE
) STRICT;
CREATE INDEX sessao_ocorrencia_sessao ON sessao_ocorrencia (sessao_id);

CREATE TABLE cobranca_prevista (
    id INTEGER PRIMARY KEY,
    pacote_id INTEGER NOT NULL REFERENCES pacote (id),
    sessao_id INTEGER REFERENCES sessao (id),
    vencimento TEXT NOT NULL CHECK (date(vencimento) = vencimento),
    valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
    origem TEXT NOT NULL CHECK (origem IN ('inicio', 'sessao', 'parcela', 'mensal'))
) STRICT;
CREATE UNIQUE INDEX cobranca_prevista_sessao ON cobranca_prevista (sessao_id)
    WHERE sessao_id IS NOT NULL;
CREATE INDEX cobranca_prevista_pacote ON cobranca_prevista (pacote_id);

CREATE TABLE pendencia_operacional (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    referencia TEXT,
    lote_id INTEGER,
    aba TEXT,
    linha INTEGER,
    motivo TEXT NOT NULL,
    sugestao TEXT,
    detalhe TEXT,
    status TEXT NOT NULL DEFAULT 'aberta' CHECK (status IN ('aberta', 'resolvida', 'descartada')),
    criada_em TEXT NOT NULL,
    resolvida_por TEXT,
    resolvida_em TEXT,
    resolucao TEXT
) STRICT;
CREATE INDEX pendencia_status ON pendencia_operacional (status, tipo);

CREATE TRIGGER modelo_pacote_no_update BEFORE UPDATE OF
    nome, modo_cobranca, n_sessoes, preco_total_centavos, preco_unitario_centavos, n_parcelas,
    intervalo_dias, validade_dias, falta_consome, falta_cobrada, antecedencia_horas, vigente_de,
    criado_por, id
ON modelo_pacote
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER modelo_pacote_vigencia BEFORE UPDATE OF vigente_ate ON modelo_pacote
WHEN OLD.vigente_ate IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER modelo_pacote_no_delete BEFORE DELETE ON modelo_pacote
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER pacote_no_update BEFORE UPDATE OF
    paciente_id, modelo_id, preco_acordado_centavos, sessoes_totais, inicio, validade,
    operacao_id, criado_por, criado_em, id
ON pacote
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER pacote_no_delete BEFORE DELETE ON pacote
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER sessao_ocorrencia_no_update BEFORE UPDATE ON sessao_ocorrencia
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER sessao_ocorrencia_no_delete BEFORE DELETE ON sessao_ocorrencia
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER cobranca_prevista_no_update BEFORE UPDATE ON cobranca_prevista
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER cobranca_prevista_no_delete BEFORE DELETE ON cobranca_prevista
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER pendencia_no_delete BEFORE DELETE ON pendencia_operacional
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER pendencia_no_update BEFORE UPDATE OF
    tipo, referencia, lote_id, aba, linha, motivo, sugestao, detalhe, criada_em, id
ON pendencia_operacional
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE VIEW ocorrencia_efetiva AS
SELECT o.* FROM sessao_ocorrencia o
WHERE o.estorno_de IS NULL
  AND NOT EXISTS (SELECT 1 FROM sessao_ocorrencia e WHERE e.estorno_de = o.id);
