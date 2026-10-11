CREATE TABLE estado (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
) STRICT;

CREATE TABLE agendamento_visto (
    agenda_id TEXT PRIMARY KEY,
    atualizado_em TEXT NOT NULL
) STRICT;

CREATE TABLE cliente_espelho (
    customer_id TEXT PRIMARY KEY,
    nome_norm TEXT NOT NULL,
    tel_hash TEXT
) STRICT;
CREATE INDEX cliente_espelho_nome ON cliente_espelho (nome_norm);
