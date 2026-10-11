---
tipo: modelo
zona: modelos
---
# Modelo de skill

Copie para `configuracao/skills/`. Skills com efeito precisam de `autonomia` A2 ou A3.

````markdown
---
tipo: skill
id: consultar_exemplo
---
Descreva o que a skill faz.

```brm-config
name: consultar_exemplo
description: Consulta um exemplo
method: GET
url_template: https://api.exemplo.com.br/itens/{id}
allowed_domains: [api.exemplo.com.br]
```
````

Os domínios precisam estar na allowlist do cliente; qualquer outro host é rejeitado.
