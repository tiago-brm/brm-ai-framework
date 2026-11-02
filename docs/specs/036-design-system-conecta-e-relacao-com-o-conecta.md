# 036 — Design system do Conecta no framework e relação com o Conecta (spec)

**Status:** implementada no painel (2026-10-31). Ver `docs/changelog/036-design-system-conecta.md`.

**Origem:** estudo de unificação brm-jarvis (BRM Conecta) × brm-ai-framework (2026-10-31) e decisão do
dono.

## Decisões

1. **D-030 mantida:** Conecta e framework seguem separados, ligados por contrato. Fusão descartada
   (opção D); monorepo (E) só se a integração avançar.
2. **Design system do Conecta é o padrão visual do brm-ai-framework** (só dele, não da BRM inteira):
   tokens, Rubik, cards MD2, ícones em caixa de gradiente, menu lateral escuro, badges com ícone e
   texto, tema claro e escuro. Fonte: `brm-jarvis/docs/DESIGN_SYSTEM.md`, seções 1 a 16. A camada
   Assistente (HUD e orbe, seção 17) e a cor de marca por cliente (seção 18) ficam fora por ora.
3. **A Central de Ações (spec 035) nasce nesse design system**, reaproveitando a lógica do campo de
   PIN do Conecta.
4. **`/mcp` e login do framework atendem dois clientes:** o Claude e o Conecta.
5. **Depois do piloto do Weis:** opção B1 (o Conecta opera os clientes pelo `/mcp` do framework). A B2
   (Conecta como Assistente BRM da equipe) é decidida com os números de uso do piloto.
6. **Papel do Conecta:** laboratório do dono para validar teses e, depois, produto: um assistente
   pessoal multicanal com governança.

## Ajustes ao portar

- Tailwind 3 (Conecta) para Tailwind 4 (framework): tokens como cores completas e transparência por
  `color-mix`, em vez de tripletos HSL.
- **Contraste AA:** as cores semânticas de texto (sucesso, aviso, info, perigo) e o texto secundário
  usam tons mais escuros que os do Conecta no tema claro (e mais claros no escuro), porque os tons MD2
  originais não passam 4,5:1 em texto pequeno. Os gradientes das caixas de ícone continuam os do MD2.
- Fonte local (`@fontsource/rubik`, só latin e latin-ext), porque o painel roda sem internet no Docker.
- Fonte mono só para código, nomes de tool e identificadores.
