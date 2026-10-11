# SilverBullet: segurança, multiusuário e somente leitura

Pesquisa feita em 2026-10-09 na documentação oficial atual (regra L-99). A imagem
fixada no `docker-compose.yml` é `ghcr.io/silverbulletmd/silverbullet:2.12.0-slim`.

## Conclusões

1. **Multiusuário existe, mas no modo multi-space.** Nesse modo (o padrão atual do
   SilverBullet) cada pessoa tem uma conta (usuário e senha) criada por um admin no
   Dashboard, e há tokens de API por conta. O modo de espaço único, com a variável
   `SB_USER=usuario:senha` (credencial única), é considerado **legado e depreciado** pela
   documentação. O compose atual usa `SB_USER`, portanto hoje o editor tem uma credencial
   só. Também há login único (OpenID Connect) e delegação a um proxy de autenticação.
2. **O controle de acesso é por espaço, não por pasta.** Cada espaço tem acesso para
   visitantes (`none`, `read`, `write`) e um papel por conta (`read` ou `write`). Não há
   permissão por pasta nem papel "edita mas não programa".
3. **Somente leitura de uma subpasta não existe no SilverBullet.** `SB_READ_ONLY` vale
   para o servidor inteiro e está depreciado (a alternativa é "congelar" o espaço
   inteiro no Dashboard). Para `sistema/` e `relatorios/` a proteção real é do sistema
   de arquivos: montar essas duas pastas como `:ro` por cima do volume do editor.
4. **Há risco de código a partir de notas.** Quem tem `write` escreve Space Lua e
   conteúdo dinâmico que roda no navegador de **quem abrir o espaço depois**, inclusive
   admins. A documentação diz que é preciso confiar em quem tem `write`. Endpoints que
   saem do espaço exigem `write`: o shell (`/.shell`, que roda comando no servidor, vem
   **desligado por padrão**), o proxy HTTP (`/.proxy`, busca URL na rede do servidor) e
   a Runtime API (`/.runtime`, avalia Lua em um Chromium headless; a imagem `-slim` não
   traz Chromium).

## O que o BRM faz a respeito

- O motor **nunca executa nada vindo do caderno**. Ele lê só frontmatter e o bloco
  `brm-config`, e só vale a versão aprovada guardada no `state.db`. Um bloco
  `space-lua` numa nota é, para o motor, texto de prosa.
- A segurança do motor não depende do editor: editar não é autorizar (spec 030).
- A credencial de escrita do editor é de gestor/super_admin. Os demais perfis usam o chat.

## Configuração recomendada (para o agente do Docker alinhar o compose)

| Item | Recomendação |
|---|---|
| Shell | Manter desligado. Não habilitar `/.shell`. |
| Imagem | Manter `-slim` (sem Chromium, sem Runtime API). |
| Exposição | `127.0.0.1` por padrão; fora disso, só atrás de proxy com TLS. |
| Contas | Migrar para multi-space com uma conta por pessoa quando possível. Com `SB_USER` (legado) a credencial é única. |
| Pastas do motor | Montar `vault/sistema` e `vault/relatorios` como `:ro` dentro de `/data`. |
| Hostname | Se houver mais de um espaço, um hostname por espaço (cookie e origem isolados). |
| Bibliotecas | Não instalar bibliotecas/plugs de terceiros no espaço sem revisão. |

Exemplo do trecho de volumes (as duas pastas existem no template; o motor cria
`sistema/` na primeira geração):

```yaml
volumes:
  - ./clients/${CLIENT_ID:-example}/vault:/data
  - ./clients/${CLIENT_ID:-example}/vault/sistema:/data/sistema:ro
  - ./clients/${CLIENT_ID:-example}/vault/relatorios:/data/relatorios:ro
```

## Limite que continua valendo

Com uma única credencial, o editor não sabe **quem** escreveu, e o motor também não
(o arquivo não registra autor). Por isso a atribuição de autoria é feita no motor:
quem **propõe** (`config_propor`) e quem **aprova** (`aprovar_operacao`) são pessoas
identificadas e diferentes. Isso é segregação de funções entre propor e aprovar, não
prova de quem digitou a nota.

## Fontes

- Autenticação: https://docs.silverbullet.md/Authentication
- Configuração e variáveis de ambiente: https://docs.silverbullet.md/Install/Configuration
- Modelo de confiança: https://docs.silverbullet.md/Security
- Perfis de segurança: https://docs.silverbullet.md/Security%20Profiles
- Dashboard, espaços e níveis de acesso: https://docs.silverbullet.md/Dashboard
- Docker: https://docs.silverbullet.md/Install/Docker

(Texto lido a partir do repositório oficial, `silverbulletmd/silverbullet`, pasta `docs/`.)
