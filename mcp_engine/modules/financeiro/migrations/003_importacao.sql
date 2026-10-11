CREATE TABLE importacao_lote (
    id INTEGER PRIMARY KEY,
    arquivo_hash TEXT NOT NULL,
    arquivo_nome TEXT NOT NULL,
    layout TEXT NOT NULL CHECK (layout IN ('secretarias', 'financeiro')),
    criado_por TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    resumo TEXT
) STRICT;

CREATE TABLE importacao_linha (
    chave TEXT PRIMARY KEY,
    lote_id INTEGER NOT NULL REFERENCES importacao_lote (id),
    aba TEXT NOT NULL,
    linha INTEGER NOT NULL,
    recebimento_id INTEGER REFERENCES recebimento (id),
    despesa_id INTEGER REFERENCES despesa (id)
) STRICT;
CREATE INDEX importacao_linha_lote ON importacao_linha (lote_id);

CREATE TRIGGER importacao_linha_no_update BEFORE UPDATE ON importacao_linha
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
CREATE TRIGGER importacao_linha_no_delete BEFORE DELETE ON importacao_linha
BEGIN SELECT RAISE(ABORT, 'imutavel'); END;
