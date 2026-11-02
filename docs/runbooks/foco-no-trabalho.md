# Runbook: foco no trabalho (conector e Projeto)

O pedido da clínica é que o chat seja usado só para o trabalho. Isto é um pedido ao modelo, não
um bloqueio: ele funciona na maioria das vezes, e não vale quando a pessoa usa o Claude fora do
conector ou do Projeto. Controle técnico de verdade exige um chat próprio (Assistente BRM).

## Conector (feito)

`client_config.yaml`:

```yaml
foco_trabalho: true            # texto padrão
# foco_trabalho: "Só assuntos da clínica. Recuse o resto em uma frase."   # texto próprio
```

O texto entra nas instruções que o servidor MCP entrega ao Claude quando o conector é usado.
Reinicie o servidor (no Claude Desktop, feche e abra o app) para valer.

## Projeto no Claude Team (a clínica configura)

Crie um Projeto "Clínica" e cole nas instruções do projeto:

> Este projeto é de uso exclusivo para o trabalho da clínica: agenda, pacientes, cobrança,
> contratos e relatórios, sempre pelas ferramentas do conector da clínica. Para qualquer outro
> assunto, recuse com educação em uma frase e diga que este espaço é só para o trabalho.

Peça às pessoas que abram as conversas de trabalho dentro desse Projeto.

## Combinar com a clínica

Política de uso assinada (contas da empresa só para trabalho). Sem ela, a instrução é só um aviso.
