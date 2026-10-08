# Apresentação e roteiro para clínicas de psicologia

Fontes das duas páginas publicadas como artefatos privados no claude.ai:

| Arquivo | Conteúdo |
|---|---|
| `apresentacao-clinica.html` | Apresentação de rolagem única (problema, solução, normas do CFP, perfis, acesso por nota, simulador, conversa natural, carteira de pacientes, sugestões para ensino) |
| `roteiro-clinica.html` | Guia do apresentador (preparo, troca de perfil, cenas das demos, agendar pelo chat, descoberta, perguntas, limites, plano B) |

Os arquivos são **fragmentos** escritos para o publicador de artefatos (sem `<html>` nem `<body>`),
e usam dados fictícios. Para publicar de novo, use o mesmo arquivo com o publicador de artefatos
(mantém a URL quando se passa a URL do artefato existente).

Demos e verificações que o roteiro cita:

```
uv run python scripts/preflight_demo.py          # pré-voo das duas demos
uv run python scripts/demo_clinica.py            # 11 cenas (--parte1 para as 6 da clínica)
uv run python scripts/demo_casos_de_uso.py       # carteira de pacientes
uv run python scripts/preparar_demo_chat.py wagner
```

O agendamento pelo chat usa o MCP do BRM Agenda (repositório `agendapro4`, `apps/mcp`):
`npm run smoke:demo`.
