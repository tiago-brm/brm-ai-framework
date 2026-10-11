CREATE TABLE unidade (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE CHECK (length(nome) BETWEEN 1 AND 80)
) STRICT;

CREATE TABLE profissional (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 120),
    nome_norm TEXT NOT NULL UNIQUE,
    email TEXT,
    external_ref TEXT UNIQUE,
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
) STRICT;

CREATE TABLE paciente_ref (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 160),
    nome_norm TEXT NOT NULL,
    unidade_id INTEGER REFERENCES unidade (id),
    profissional_id INTEGER REFERENCES profissional (id),
    valor_sessao_centavos INTEGER CHECK (valor_sessao_centavos IS NULL OR valor_sessao_centavos >= 0),
    external_ref TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo', 'a_confirmar'))
) STRICT;
CREATE INDEX paciente_ref_nome_norm ON paciente_ref (nome_norm);

CREATE TABLE meio_recebimento (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE CHECK (length(nome) BETWEEN 1 AND 60),
    tipo TEXT NOT NULL CHECK (tipo IN ('banco', 'maquininha', 'dinheiro')),
    vigente_de TEXT NOT NULL CHECK (date(vigente_de) = vigente_de),
    vigente_ate TEXT CHECK (vigente_ate IS NULL OR date(vigente_ate) = vigente_ate),
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    CHECK (vigente_ate IS NULL OR vigente_ate >= vigente_de)
) STRICT;

INSERT INTO meio_recebimento (nome, tipo, vigente_de) VALUES ('Caixa', 'dinheiro', '2000-01-01');

CREATE TABLE forma_pagamento (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE
) STRICT;
INSERT INTO forma_pagamento (nome) VALUES
    ('pix'), ('debito'), ('credito'), ('dinheiro'), ('ted'), ('acordo');

CREATE TABLE taxa_meio (
    id INTEGER PRIMARY KEY,
    meio_id INTEGER NOT NULL REFERENCES meio_recebimento (id),
    forma_id INTEGER NOT NULL REFERENCES forma_pagamento (id),
    parcelas INTEGER NOT NULL DEFAULT 1 CHECK (parcelas BETWEEN 1 AND 24),
    taxa_bp INTEGER NOT NULL CHECK (taxa_bp BETWEEN 0 AND 10000),
    vigente_de TEXT NOT NULL CHECK (date(vigente_de) = vigente_de),
    vigente_ate TEXT CHECK (vigente_ate IS NULL OR date(vigente_ate) = vigente_ate),
    criado_por TEXT NOT NULL,
    CHECK (vigente_ate IS NULL OR vigente_ate >= vigente_de)
) STRICT;

CREATE TABLE comissao_regra (
    id INTEGER PRIMARY KEY,
    profissional_id INTEGER NOT NULL REFERENCES profissional (id),
    unidade_id INTEGER REFERENCES unidade (id),
    servico TEXT,
    forma_id INTEGER REFERENCES forma_pagamento (id),
    tipo TEXT NOT NULL CHECK (tipo IN ('percentual', 'fixo')),
    valor_bp INTEGER CHECK (valor_bp IS NULL OR valor_bp BETWEEN 0 AND 10000),
    valor_centavos INTEGER CHECK (valor_centavos IS NULL OR valor_centavos >= 0),
    base TEXT NOT NULL CHECK (base IN ('bruto', 'liquido')),
    vigente_de TEXT NOT NULL CHECK (date(vigente_de) = vigente_de),
    vigente_ate TEXT CHECK (vigente_ate IS NULL OR date(vigente_ate) = vigente_ate),
    criado_por TEXT NOT NULL,
    CHECK (vigente_ate IS NULL OR vigente_ate >= vigente_de),
    CHECK ((tipo = 'percentual' AND valor_bp IS NOT NULL AND valor_centavos IS NULL)
        OR (tipo = 'fixo' AND valor_centavos IS NOT NULL AND valor_bp IS NULL))
) STRICT;

CREATE TABLE categoria_despesa (
    id INTEGER PRIMARY KEY,
    grupo TEXT NOT NULL CHECK (grupo IN ('variada', 'fixa', 'imposto', 'folha')),
    nome TEXT NOT NULL,
    UNIQUE (grupo, nome)
) STRICT;

CREATE TABLE recebimento (
    id INTEGER PRIMARY KEY,
    data_pagamento TEXT NOT NULL CHECK (date(data_pagamento) = data_pagamento),
    data_competencia TEXT NOT NULL CHECK (date(data_competencia) = data_competencia),
    paciente_id INTEGER NOT NULL REFERENCES paciente_ref (id),
    forma_id INTEGER NOT NULL REFERENCES forma_pagamento (id),
    meio_id INTEGER REFERENCES meio_recebimento (id),
    parcelas INTEGER NOT NULL DEFAULT 1 CHECK (parcelas BETWEEN 1 AND 24),
    valor_centavos INTEGER NOT NULL CHECK (valor_centavos <> 0),
    profissional_id INTEGER REFERENCES profissional (id),
    unidade_id INTEGER REFERENCES unidade (id),
    secretaria TEXT,
    servico TEXT,
    tipo TEXT NOT NULL CHECK (tipo IN ('sessao', 'pacote', 'acordo', 'juros', 'estorno')),
    nf_status TEXT NOT NULL DEFAULT 'pendente' CHECK (nf_status IN ('emitida', 'sem_nf', 'pendente')),
    nf_numero TEXT,
    comprovante_ref TEXT,
    estorno_de INTEGER REFERENCES recebimento (id),
    observacao TEXT,
    origem TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'importacao', 'sistema')),
    lote_id INTEGER,
    operacao_id TEXT UNIQUE,
    pacote_id INTEGER,
    cobranca_prevista_id INTEGER,
    criado_por TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    CHECK ((tipo = 'estorno') = (estorno_de IS NOT NULL)),
    CHECK ((tipo = 'estorno' AND valor_centavos < 0) OR (tipo <> 'estorno' AND valor_centavos > 0))
) STRICT;
CREATE INDEX recebimento_data ON recebimento (data_pagamento, valor_centavos);
CREATE INDEX recebimento_competencia ON recebimento (data_competencia);
CREATE INDEX recebimento_paciente ON recebimento (paciente_id);
CREATE INDEX recebimento_estorno ON recebimento (estorno_de);

CREATE TABLE recebimento_sessao (
    id INTEGER PRIMARY KEY,
    recebimento_id INTEGER NOT NULL REFERENCES recebimento (id),
    data_sessao TEXT NOT NULL CHECK (date(data_sessao) = data_sessao),
    sessao_id INTEGER,
    UNIQUE (recebimento_id, data_sessao)
) STRICT;

CREATE TABLE despesa (
    id INTEGER PRIMARY KEY,
    data TEXT NOT NULL CHECK (date(data) = data),
    data_competencia TEXT NOT NULL CHECK (date(data_competencia) = data_competencia),
    descricao TEXT NOT NULL CHECK (length(descricao) BETWEEN 1 AND 200),
    categoria_id INTEGER NOT NULL REFERENCES categoria_despesa (id),
    forma_id INTEGER REFERENCES forma_pagamento (id),
    valor_centavos INTEGER NOT NULL CHECK (valor_centavos <> 0),
    recorrente INTEGER NOT NULL DEFAULT 0 CHECK (recorrente IN (0, 1)),
    comprovante_ref TEXT,
    estorno_de INTEGER REFERENCES despesa (id),
    origem TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'importacao', 'sistema')),
    lote_id INTEGER,
    operacao_id TEXT UNIQUE,
    criado_por TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    CHECK ((estorno_de IS NOT NULL AND valor_centavos < 0)
        OR (estorno_de IS NULL AND valor_centavos > 0))
) STRICT;
CREATE INDEX despesa_data ON despesa (data);
CREATE INDEX despesa_competencia ON despesa (data_competencia);

CREATE TABLE repasse (
    id INTEGER PRIMARY KEY,
    recebimento_id INTEGER NOT NULL UNIQUE REFERENCES recebimento (id),
    data_pagamento TEXT NOT NULL CHECK (date(data_pagamento) = data_pagamento),
    profissional_id INTEGER REFERENCES profissional (id),
    comissao_regra_id INTEGER REFERENCES comissao_regra (id),
    base TEXT CHECK (base IS NULL OR base IN ('bruto', 'liquido')),
    taxa_bp INTEGER NOT NULL CHECK (taxa_bp BETWEEN 0 AND 10000),
    taxa_centavos INTEGER NOT NULL,
    liquido_centavos INTEGER NOT NULL,
    repasse_centavos INTEGER NOT NULL,
    parte_clinica_centavos INTEGER NOT NULL,
    limitado INTEGER NOT NULL DEFAULT 0 CHECK (limitado IN (0, 1)),
    CHECK (repasse_centavos + parte_clinica_centavos = liquido_centavos)
) STRICT;
CREATE INDEX repasse_profissional ON repasse (profissional_id);
CREATE INDEX repasse_data ON repasse (
    data_pagamento, repasse_centavos, parte_clinica_centavos, taxa_centavos
);

CREATE TABLE fechamento_mensal (
    id INTEGER PRIMARY KEY,
    mes TEXT NOT NULL CHECK (mes GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
    unidade_id INTEGER REFERENCES unidade (id),
    status TEXT NOT NULL CHECK (status IN ('fechado', 'reaberto')),
    aprovado_por TEXT NOT NULL,
    hash TEXT,
    motivo TEXT,
    criado_em TEXT NOT NULL
) STRICT;

CREATE VIEW fechamento_vigente AS
SELECT f.mes, f.unidade_id, f.status, f.hash, f.aprovado_por, f.criado_em
FROM fechamento_mensal f
WHERE f.id = (
    SELECT MAX(g.id) FROM fechamento_mensal g
    WHERE g.mes = f.mes AND COALESCE(g.unidade_id, 0) = COALESCE(f.unidade_id, 0)
);

CREATE TRIGGER recebimento_no_update BEFORE UPDATE OF
    data_pagamento, data_competencia, paciente_id, forma_id, meio_id, parcelas, valor_centavos,
    profissional_id, unidade_id, secretaria, servico, tipo, estorno_de, observacao, origem,
    lote_id, operacao_id, pacote_id, cobranca_prevista_id, criado_por, criado_em, id
ON recebimento
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER recebimento_no_delete BEFORE DELETE ON recebimento
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER recebimento_sessao_no_update BEFORE UPDATE OF recebimento_id, data_sessao, id
ON recebimento_sessao
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER recebimento_sessao_no_delete BEFORE DELETE ON recebimento_sessao
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER despesa_no_update BEFORE UPDATE OF
    data, data_competencia, descricao, categoria_id, forma_id, valor_centavos, recorrente,
    estorno_de, origem, lote_id, operacao_id, criado_por, criado_em, id
ON despesa
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER despesa_no_delete BEFORE DELETE ON despesa
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER repasse_no_update BEFORE UPDATE ON repasse
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER repasse_no_delete BEFORE DELETE ON repasse
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER fechamento_no_update BEFORE UPDATE ON fechamento_mensal
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER fechamento_no_delete BEFORE DELETE ON fechamento_mensal
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER taxa_meio_no_update BEFORE UPDATE OF
    meio_id, forma_id, parcelas, taxa_bp, vigente_de, criado_por, id
ON taxa_meio
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER taxa_meio_vigencia BEFORE UPDATE OF vigente_ate ON taxa_meio
WHEN OLD.vigente_ate IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER taxa_meio_no_delete BEFORE DELETE ON taxa_meio
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER comissao_regra_no_update BEFORE UPDATE OF
    profissional_id, unidade_id, servico, forma_id, tipo, valor_bp, valor_centavos, base,
    vigente_de, criado_por, id
ON comissao_regra
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER comissao_regra_vigencia BEFORE UPDATE OF vigente_ate ON comissao_regra
WHEN OLD.vigente_ate IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER comissao_regra_no_delete BEFORE DELETE ON comissao_regra
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER meio_no_update BEFORE UPDATE OF nome, tipo, vigente_de, id ON meio_recebimento
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER meio_vigencia BEFORE UPDATE OF vigente_ate ON meio_recebimento
WHEN OLD.vigente_ate IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER meio_no_delete BEFORE DELETE ON meio_recebimento
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;

CREATE TRIGGER recebimento_mes_fechado BEFORE INSERT ON recebimento
WHEN EXISTS (
    SELECT 1 FROM fechamento_vigente f
    WHERE f.mes = substr(NEW.data_competencia, 1, 7) AND f.status = 'fechado'
      AND (f.unidade_id IS NULL OR f.unidade_id = NEW.unidade_id)
)
BEGIN SELECT RAISE(ABORT, 'mes_fechado'); END;

CREATE TRIGGER despesa_mes_fechado BEFORE INSERT ON despesa
WHEN EXISTS (
    SELECT 1 FROM fechamento_vigente f
    WHERE f.mes = substr(NEW.data_competencia, 1, 7) AND f.status = 'fechado'
      AND f.unidade_id IS NULL
)
BEGIN SELECT RAISE(ABORT, 'mes_fechado'); END;

CREATE TRIGGER recebimento_estorno_valido BEFORE INSERT ON recebimento
WHEN NEW.estorno_de IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'estorno_original_invalido')
    WHERE NOT EXISTS (
        SELECT 1 FROM recebimento o
        WHERE o.id = NEW.estorno_de AND o.tipo <> 'estorno' AND o.paciente_id = NEW.paciente_id
    );
    SELECT RAISE(ABORT, 'estorno_acima_do_saldo')
    WHERE (SELECT -COALESCE(SUM(e.valor_centavos), 0) FROM recebimento e
           WHERE e.estorno_de = NEW.estorno_de) - NEW.valor_centavos
        > (SELECT valor_centavos FROM recebimento WHERE id = NEW.estorno_de);
END;

CREATE TRIGGER despesa_estorno_valido BEFORE INSERT ON despesa
WHEN NEW.estorno_de IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'estorno_original_invalido')
    WHERE NOT EXISTS (
        SELECT 1 FROM despesa o WHERE o.id = NEW.estorno_de AND o.estorno_de IS NULL
    );
    SELECT RAISE(ABORT, 'estorno_acima_do_saldo')
    WHERE (SELECT -COALESCE(SUM(e.valor_centavos), 0) FROM despesa e
           WHERE e.estorno_de = NEW.estorno_de) - NEW.valor_centavos
        > (SELECT valor_centavos FROM despesa WHERE id = NEW.estorno_de);
END;

CREATE TRIGGER repasse_coerente BEFORE INSERT ON repasse
BEGIN
    SELECT RAISE(ABORT, 'repasse_incoerente')
    WHERE NOT EXISTS (
        SELECT 1 FROM recebimento r
        WHERE r.id = NEW.recebimento_id
          AND r.valor_centavos - NEW.taxa_centavos = NEW.liquido_centavos
          AND r.data_pagamento = NEW.data_pagamento
    );
END;
