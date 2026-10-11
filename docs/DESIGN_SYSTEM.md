# Design System

> Linguagem visual do **painel `super_admin`** (`dashboard/`). O motor não tem UI própria; o Claude é a interface de uso diário. Sem este documento, telas diferentes inventam fontes, cores e espaçamentos.
> Fonte dos tokens: `dashboard/src/index.css`. Valores em HEX foram convertidos de OKLCH (a definição real) e conferidos em 2026-10-11 — em caso de divergência, **vale o CSS**.

## 1. Design Direction

- **Style:** Editorial-técnico. Folha de papel azulada, tinta azul-marinho, metadados em monoespaçada, títulos em serifa itálica.
- **Mood:** Sóbrio, confiável, de "documento de auditoria" — transmite governança, não "dashboard de marketing".
- **Design Principles:**
  - **A decisão é o protagonista.** `allowed`/`denied`/`pending_approval` e o motivo aparecem primeiro, com cor **e** texto.
  - **Densidade informativa.** Operador técnico, tela de desktop; texto pequeno (11–13 px) e bordas finas em vez de sombras.
  - **Mostrar o que o motor sabe.** Regra aplicada, fatos derivados, políticas e contagem de ocultas ficam visíveis, nunca escondidos.
  - **Perigo explícito.** Agir "como outro perfil" nunca é confundido com a sessão real (faixa de simulação).

## 2. Brand

- **Brand Name:** BRM Engine (painel) / BRM Solutions.
- **Logo:** Hoje um quadrado `navy` de 24 px com "B" em mono (`Shell.tsx`) + rótulo `BRM ENGINE` em mono, caixa alta, `tracking-[0.18em]`. Não há logotipo oficial no repositório (`favicon.svg` é genérico). *Pendente:* regras de uso do logo da BRM.
- **Brand Voice:** Português do Brasil, direto, sem jargão de IA; mensagens dizem **o que aconteceu, por quê e o que fazer** (`reason` + `proximo_passo`).

## 3. Color Palette

Tokens da marca (definidos em `:root`, usados como `bg-paper`, `text-ink`, `border-line`, etc.):

| Token | Uso | OKLCH | HEX aprox. |
|---|---|---|---|
| `paper` (Background) | Fundo da página | `94.9% 0.008 253` | `#EAEFF3` |
| `surface` (Surface) | Cartões, inputs | `100% 0 0` | `#FFFFFF` |
| `ink` (Text) | Texto principal | `25.2% 0.021 258` | `#1C232C` |
| `muted-ink` (Muted Text) | Texto secundário | `49% 0.02 258` | `#5A616C` |
| `line` (Border) | Bordas e divisores | `84.5% 0.019 259` | `#C5CDD9` |
| `navy` (Primary) | Ação primária, item ativo, foco | `40.6% 0.115 265` | `#2A4587` |
| `green` (Success) | `allowed`, `allow`, online | `47.4% 0.099 158` | `#1B6C46` |
| `amber` (Warning) | `pending_approval`, `require_approval` | `50.3% 0.1 76` | `#845B0F` |
| `red` (Error) | `denied`, `deny`, erro | `45.1% 0.152 27` | `#982522` |

- **Secondary:** não há; use `ink` com opacidade e `line`.
- **Semântica de decisão (fixa em todo o painel):** `allowed`/`allow` = **verde**; `denied`/`deny` = **vermelho**; `pending_approval`/`require_approval` = **âmbar**. Nunca reutilize essas cores para outra coisa. Padrão do selo: `border-{cor}/30 bg-{cor}/10 text-{cor}` (`DecisionBadge`, `EffectBadge`).
- Tokens `shadcn` (`--primary`, `--muted`, `--destructive`…, neutros) existem para os primitivos de `components/ui/*`; **a identidade da marca vem dos tokens acima**.
- **Contraste medido** (WCAG, sobre `paper`): `ink` 13,7:1 ✓; `muted-ink` 5,4:1 ✓; `navy` 7,9:1 ✓; texto branco sobre `navy` 9,2:1 ✓; `green`/`red`/`amber` sobre o próprio fundo tingido 5,2–6,8:1 ✓. **Atenção:** `text-ink/55` = 3,5:1 e `text-ink/40` = 2,4:1 **não** atingem 4,5:1 — não use essas opacidades em texto que carregue informação (ver §15).

## 4. Typography

- **Font Family:** carregadas do Google Fonts em `dashboard/index.html`.
- **Heading Font (display):** *Newsreader* — **itálico**, só em título de página.
- **Body Font:** *Public Sans* (`font-sans`).
- **Mono:** *IBM Plex Mono* (`font-mono`) — metadados, rótulos, badges, campos técnicos, números de log.

| Papel | Tamanho / peso / entrelinha | Classe |
|---|---|---|
| H1 (título de página) | 28 px / 400 itálico / 1 | `font-display text-[28px] leading-none italic` |
| H1 do login | 24 px / 400 itálico / 1 | `font-display text-[24px] italic leading-none` |
| Body | 13 px / 400 / `leading-snug` | `font-sans text-[13px]` (definido no `Shell`) |
| Texto de apoio | 12 px | `text-[12px]` |
| Rótulo / metadado | 10–11 px mono, **caixa alta**, `tracking-wide` (`0.18–0.2em` em *eyebrows*) | `font-mono text-[10px] uppercase tracking-wide` |
| Badge | 9 px mono caixa alta | `text-[9px]` |

Tamanhos fixos em px (não use `rem`) e o conjunto acima: 8 px não deve ser usado em texto novo.

## 5. Spacing

- **Base Unit:** 4 px (escala padrão do Tailwind 4).
- **Escala:** 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96 px.
- Prática atual: padding de cartão `px-3 py-2`/`p-3`; `main` com `p-4`; espaçamento entre blocos `mb-3`/`gap-3`/`gap-4`; cabeçalho de página com `mb-3`.

## 6. Border Radius

- **Small:** `calc(var(--radius) * 0.6)` ≈ 4 px — selos, atalhos de teclado.
- **Medium:** `rounded-md` ≈ 6 px — **padrão** de cartões, inputs, botões.
- **Large:** `--radius` = `0.45rem` ≈ 7 px (`rounded-lg`) — primitivos shadcn.
- **Pill:** `rounded-full` — indicador "live", avatar de iniciais.
- O logotipo "B" é **quadrado** (sem raio) de propósito.

## 7. Shadows

- **Small / Medium / Large:** **não use sombras.** Profundidade vem de borda `line` 1 px + superfície `bg-surface/60` sobre `paper`. Única exceção: o anel de foco (§9). Movimento: entrada `rise` (0,4 s, sobe 6 px com fade) em cartões.

## 8. Buttons

Há dois estilos no código; **prefira o primitivo** para telas novas e mantenha o inline nas existentes:

- **Primary:** `bg-navy text-surface rounded-md px-3 py-1.5 font-mono text-[11px] hover:bg-navy/90`. Largura total no login.
- **Secondary:** primitivo `ui/button` `variant="outline"` ou `secondary` (`react-aria-components` + `cva`, alturas `xs` 24 / `sm` 28 / `default` 32 / `lg` 36 px).
- **Danger:** `variant="destructive"` (`bg-destructive/10 text-destructive`). Ações irreversíveis exigem confirmação.
- **Disabled:** `disabled:opacity-60` (inline) ou `opacity-50` + `pointer-events-none` (primitivo). Sempre acompanhado de motivo quando não óbvio.
- **Loading:** troque o rótulo pelo gerúndio ("Entrando…") e desabilite; não esconda o botão.

## 9. Inputs

- **Default:** `rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-[11px]`; rótulo acima em mono 10 px caixa alta (`text-ink/45`, ver §15).
- **Focus:** `focus:border-navy focus:ring-2 focus:ring-navy/20 focus:outline-none` (anel visível obrigatório).
- **Error:** mensagem em `text-red` mono 10 px abaixo do formulário; no primitivo, `aria-invalid` aplica borda/anel `destructive`.
- **Disabled:** `opacity` reduzida e `cursor` padrão.
- **Placeholder:** `placeholder:text-ink/30` — é só exemplo (ex.: `super-admin-token-demo`), nunca o único rótulo do campo.
- Campos de segredo usam `type="password"`.

## 10. Cards

`Card` (`components/Card.tsx`): `rise overflow-hidden rounded-md border border-line bg-surface/60`; cabeçalho opcional `border-b border-line px-3 py-2` com título em mono 11 px caixa alta `text-ink/55` e área `extra` à direita. Sem sombra. `Empty` para conteúdo vazio.

## 11. Navigation

- **Header:** *sticky*, ~41 px, `border-b border-line bg-paper/95 backdrop-blur`; marca à esquerda; busca/comando "⌘K" (visual, **sem função hoje**); à direita: indicador `live`, selo do perfil e avatar de iniciais (clique = sair).
- **Sidebar:** trilho de 68 px, ícones de glifo Unicode + rótulo mono 9 px; item ativo em `navy` e `aria-current="page"`. Itens: Visão, Usuários, Matriz, Tools, Regras, Vault, Auditoria, Simular, Governança.
- **Mobile Navigation:** **inexistente** — o trilho é `hidden md:flex` e não há alternativa abaixo de `md`. Lacuna conhecida; o painel é desktop-first.
- **Roteamento:** hash (`#/simulador`).
- Cabeçalho de página (`PageHeader`): *eyebrow* mono `super_admin · <seção>` + título display; ações à direita.

## 12. States

- **Loading:** texto mono "Carregando…" centralizado; não bloqueie a página inteira se só um cartão carrega.
- **Empty:** `Empty` — frase curta em mono 11 px, `text-ink/40`, dizendo o que falta e como preencher.
- **Error:** mensagem em `text-red` mono, em português, com o que fazer ("Confira a URL e o token"); `403` explica que o painel exige `super_admin`.
- **Success:** confirmação discreta (`text-green`); a lista é atualizada.
- **Disabled:** ver §8–9.
- **Decisão do motor:** sempre selo + motivo + `proximo_passo` (nunca só cor).
- **Perfil simulado:** faixa fixa de cor própria "Simulando como *Nome · papel*" enquanto ativo; só avisa, não bloqueia as outras abas.

## 13. Icons

- **Icon Library:** os glifos Unicode da navegação (`◲ ◔ ▦ ▤ § ❏ ≣ ◉ ▥`) são o padrão atual; `lucide-react` está instalado e é usado pelos primitivos `ui/*` (tabs, select, input-group).
- **Default Size:** 16 px (`size-4`); 12–14 px em botões pequenos.
- **Icon Style:** contorno (*outline*), traço fino, herdando `currentColor`. Ícone nunca é o único portador de significado — junte rótulo ou `title`/`aria-label`.

## 14. Responsive Design

- **Mobile (< 768 px):** sem navegação lateral (lacuna); evite novas telas que dependam de largura fixa.
- **Tablet / Desktop (≥ 768 px, `md`):** trilho lateral visível; conteúdo fluido em `main` (`min-w-0 flex-1 p-4`).
- **Breakpoints (Tailwind padrão):** `sm` 640 · `md` 768 · `lg` 1024 · `xl` 1280.
- Tabelas largas (Matriz, Auditoria) devem rolar dentro do cartão, não estourar a página.
- **Tema escuro:** os tokens `shadcn` têm variante `.dark`, mas os tokens da marca (`paper`, `ink`, `navy`…) **não**. Considere o painel **somente claro** até haver um tema escuro da marca.

## 15. Accessibility

- Use HTML semântico (`button` para ação, `nav`, `main`, `h1` por página; `label` associado ao campo). Já há `aria-current` no item ativo.
- Mantenha contraste ≥ 4,5:1 para texto (3:1 para texto grande e ícones); **corrija** os usos de `text-ink/40` e `/45` que carregam informação (rótulos de campo, estados vazios) — hoje ficam em ~2,4–2,7:1.
- Navegação por teclado completa e **foco visível** (anel `navy`/`ring`); respeite `prefers-reduced-motion` ao adicionar animação (a `rise` ainda não respeita).
- Rótulos acessíveis em campos e botões só-ícone (`aria-label`/`title`).
- Não dependa apenas de cor: decisão = cor **+** texto do selo; erro = cor **+** mensagem.
- Alvos de toque/clique: o tamanho 24–32 px atual é suficiente para mouse; mínimo de 44 px se o painel ganhar uso móvel.
