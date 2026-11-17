# repro-agent

Agente de IA que transforma um relato de bug em um teste pytest que reproduz o bug, com uma suíte de avaliação que mede o quanto ele acerta.

Projeto em construção. A especificação está em [SPEC.md](SPEC.md).

## Como verificar

Requer [uv](https://docs.astral.sh/uv/) e `make`.

```bash
make verify
```

Esse comando roda o ruff (lint e formatação), o mypy e a suíte pytest.
