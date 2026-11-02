# 036 — Design system do Conecta no painel

**O quê:** implementa `docs/specs/036-design-system-conecta-e-relacao-com-o-conecta.md` no `dashboard/`.

- `src/index.css`: tokens do Conecta (claro e escuro) em Tailwind 4, classes do contrato do design
  system (`content-wrapper`, `card`, `icon-box-*`, `preview-*`, `badge-outline-*`, `btn-*`, `input`,
  `label`, `hint`, `field-error`, `skeleton`, `sidebar-*`) e `data-table`, `code`.
- `index.html`: `lang="pt-BR"`, sem Google Fonts, tema aplicado antes do React (sem piscar).
- `src/lib/theme.ts`: tema claro/escuro com preferência salva (falha de armazenamento tratada).
- `src/components/`: `Shell` (menu lateral escuro em grupos, ícones lucide, navegação móvel, alternador
  de tema, sair), `PageHeader` (caixa de ícone, título, descrição, `document.title`), `Card`, `Empty`,
  `ErrorState`, `DecisionBadge`/`EffectBadge` (ícone e rótulo em português, valor técnico no `title`).
- `src/App.tsx` e as 9 páginas reescritas no novo visual, lógica preservada; "Vault jurídico" virou
  "Segundo cérebro" (núcleo agnóstico).
- Dependência: `@fontsource/rubik`.

**Verificação:** `npm run build` ok; `npm run lint` com 4 avisos, todos anteriores (eram 5; o do
`Shell` saiu); sem token antigo restante; conferido no navegador (login, visão geral, permissões em
tema escuro, simulador), sem erro no console.

**Pendente:** front da Central de Ações sobre a API da spec 035; campo de PIN; cor de marca por
cliente (seção 18 do design system do Conecta).
