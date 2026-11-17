# Regras do projeto repro-agent

Este arquivo vale para qualquer agente de código (Claude Code, Codex, etc.) e para pessoas.
A especificação completa está em [SPEC.md](SPEC.md).

## Verificação

- O único comando de verificação é `make verify`: ruff (lint e formatação), mypy no modo básico e pytest.
- Nenhuma tarefa está pronta sem `make verify` passando. Rode antes de cada commit.
- Os testes do projeto não podem precisar de chave de API nem de rede. Eles rodam no CI.

## Código

- Python 3.12, dependências gerenciadas com uv (`uv add`, `uv sync`). Não use pip direto.
- Código, nomes de variáveis, mensagens de log e docstrings em inglês.
- README, AGENTS.md e relatos de bug em português.
- Prefira código simples e fácil de revisar. Funções pequenas, sem abstrações antecipadas.
- Todo código novo em `src/repro_agent/` tem teste em `tests/`.
- Anote tipos nas funções públicas.

## Estrutura

- `src/repro_agent/`: o pacote do agente.
- `tests/`: testes do próprio projeto.
- `evals/fixtures/pedido/`: projeto alvo de exemplo, na versão correta. Não coloque bugs aqui.
- `evals/cases/<id>/`: um caso por pasta, com `issue.md` (relato) e `bug.patch` (introduz o bug).

## Segurança

- As ferramentas de leitura só acessam arquivos dentro da pasta do projeto alvo. Caminho absoluto,
  `..` e links simbólicos que saem da pasta são recusados, e isso tem teste.
- O agente nunca altera o projeto alvo além do arquivo de teste que ele cria, e sempre trabalha numa
  cópia temporária.
- Toda execução de pytest e toda chamada de LLM têm timeout.
- Chaves de API só no `.env`, que está no `.gitignore`. Nunca faça commit de chave.

## Agente

- O nó judge é determinístico: nunca use LLM para decidir se o teste reproduziu o bug.
- Provedor e nome do modelo vêm de variáveis de ambiente. Nada de modelo fixo no código.

## Git

- Mensagens de commit no padrão Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `build:`,
  `ci:`, `refactor:`, `chore:`), com a descrição em português.
- Um commit por mudança lógica.

## Escrita

- Sem travessões nos textos em português. Use vírgula, dois pontos ou ponto.
