---
tipo: modelo
zona: modelos
---
# Modelo de regra

Copie para `configuracao/regras/`. A regra só vale depois que uma pessoa a aprova (peça `config_propor`).

````markdown
---
tipo: regra
id: bloqueia-exemplo
ordem: 100
---
Explique em português o que a regra faz e por quê.

```brm-config
id: bloqueia-exemplo
description: Bloqueia o exemplo
condition: {"==": [{"var": "skill_name"}, "exemplo"]}
effect: deny
message: Bloqueado pela política da casa
```
````

Efeitos: `allow`, `deny`, `require_approval`. Regras `deny` são avaliadas antes das `allow` na mesma `ordem`.
