# repro-agent: especificação

Agente de IA que transforma um relato de bug em um teste automatizado que reproduz o bug, e uma suíte de avaliação que mede, com números, o quanto ele acerta.

## 1. Problema

Relatos de bug chegam em linguagem natural, muitas vezes vagos. O primeiro passo para corrigir um bug é reproduzi-lo com um teste que falha. Esse passo é manual, lento e depende de quem conhece o código. O repro-agent automatiza esse passo e mede a própria confiabilidade, em vez de só "parecer funcionar".

## 2. Escopo do MVP

Entrada: o texto de um relato de bug (Markdown) e o caminho de um projeto Python que usa pytest.

Saída:
- um arquivo de teste pytest que falha no código com bug, pela razão certa;
- um relatório JSON com status, tentativas, tokens consumidos, custo estimado e tempo.

Fora do escopo do MVP: corrigir o bug, linguagens além de Python, leitura direta de issues do GitHub ou Jira, sandbox em Docker.

## 3. Stack

- Python 3.12, dependências com uv
- LangGraph para o fluxo do agente
- Provedor de LLM configurável por variável de ambiente: Gemini (Google AI Studio) e DeepSeek (API compatível com OpenAI). Nome do modelo também por variável de ambiente, nada fixo no código
- Pydantic para saídas estruturadas
- pytest para os testes do próprio projeto
- Typer para a CLI
- FastAPI para a API (fase 2)
- GitHub Actions para CI

Código, nomes de variáveis e mensagens de log em inglês. README em português.

## 4. Fluxo do agente (grafo LangGraph)

1. **understand**: lê o relato e produz uma saída estruturada: comportamento esperado, comportamento observado, passos ou entrada que disparam o bug, módulos suspeitos.
2. **locate**: loop ReAct com ferramentas somente leitura (listar arquivos, ler arquivo, buscar texto) restritas à pasta do projeto alvo. Limite de passos configurável (padrão 8).
3. **write_test**: escreve um único arquivo `tests/test_repro_<id>.py` no projeto alvo.
4. **run_test**: executa só esse teste com pytest em subprocesso, numa cópia temporária do projeto, com timeout (padrão 60 s). Captura a saída.
5. **judge**: decisão determinística, sem LLM:
   - sucesso: o teste falhou com `AssertionError` (ou exceção que o relato descreve como o próprio bug);
   - falha pela razão errada: erro de import, erro de sintaxe, erro de coleta, fixture inexistente, timeout, ou o teste passou;
6. **reflect**: em caso de falha pela razão errada, recebe a saída do pytest, explica o que deu errado e volta para **write_test**. Máximo de 3 tentativas no total.

Estado do grafo guarda: relato, análise estruturada, arquivos lidos, código do teste de cada tentativa, saídas do pytest, contagem de tokens por nó.

## 5. Segurança e robustez

- Ferramentas de leitura recusam caminhos fora da pasta do projeto alvo (inclusive `..` e links simbólicos). Ter teste para isso.
- O agente nunca altera arquivos do projeto alvo além do arquivo de teste que ele cria, e sempre trabalha numa cópia temporária.
- Timeout em toda execução de pytest e em toda chamada de LLM.
- Chaves de API só por `.env`, que fica no `.gitignore`. Ter um `.env.example`.
- Documentar no README que o teste gerado é código executado na máquina, e que um sandbox em Docker é o próximo passo.

## 6. Suíte de avaliação (o diferencial do projeto)

Projeto alvo de exemplo em `evals/fixtures/pedido/`: um pacote Python pequeno de cálculo de pedido de delivery (subtotal, cupom percentual e de valor fixo, taxa de entrega, frete grátis acima de um valor mínimo, arredondamento em centavos, validações de quantidade). Esta versão é a correta e tem testes próprios passando.

Cada caso em `evals/cases/<id>/` contém:
- `issue.md`: relato escrito como um usuário real escreveria. Pelo menos dois casos devem ser vagos de propósito.
- `bug.patch`: patch que introduz o bug na versão correta.

Exemplos de bugs: frete grátis com `>` em vez de `>=` no valor mínimo, cupom aplicado depois da taxa de entrega, arredondamento com float gerando centavo a mais, cupom percentual acima de 100% aceito, carrinho vazio cobrando taxa.

Runner `evals/run.py`, para cada caso:
1. copia o projeto correto para uma pasta temporária e aplica `bug.patch`;
2. roda o agente;
3. confere **fail-to-pass**: o teste gerado precisa falhar na versão com bug **e passar** na versão correta. Só isso conta como reprodução válida;
4. registra status, tentativas, tokens, custo estimado e tempo.

Saídas: `evals/results/<data>_<modelo>.json` e uma tabela Markdown com taxa de reprodução válida, média de tentativas, tokens e custo por caso. O runner aceita `--model` e `--cases`, para comparar modelos.

A suíte de avaliação usa LLM real e **não** roda no CI.

## 7. Testes do próprio projeto

- Testes unitários das ferramentas (incluindo a recusa de caminhos fora da pasta) e do **judge** (cada tipo de falha classificado corretamente a partir de saídas reais de pytest).
- Teste do grafo inteiro com um modelo falso que devolve respostas roteirizadas: um cenário que acerta de primeira e um que erra o import, reflete e acerta na segunda tentativa.
- Esses testes rodam no CI sem chave de API.

## 8. Interfaces

- CLI: `repro run --repo <pasta> --issue <arquivo.md> [--model ...]` e `repro eval [--model ...] [--cases ...]`
- API (fase 2): `POST /reproduce` recebendo o texto do relato e o nome de um projeto alvo previamente registrado; `GET /health`.

## 9. Fases

**Fase 1 (MVP)**: estrutura do repositório, AGENTS.md, ferramentas, grafo completo, CLI, projeto `pedido` com 5 casos, runner de avaliação, testes com modelo falso, CI, README com a primeira tabela de resultados.

**Fase 2**: mais 3 a 5 casos (total de 8 a 10), API FastAPI, comparação entre dois modelos documentada no README.

**Fase 3 (depois)**: leitura de issues do GitHub, sandbox em Docker, tracing com Langfuse.

## 10. Comando de verificação

Um único comando, `make verify` (ou `uv run task verify` se não houver make no ambiente), que roda lint (ruff), checagem de tipos (mypy ou pyright, modo básico) e a suíte pytest do projeto. Nenhuma tarefa é dada como pronta sem esse comando passando.

## 11. Definição de pronto da fase 1

- `make verify` passa localmente e no CI.
- `repro eval` roda os 5 casos com um modelo real e grava o resultado.
- README explica o problema, a arquitetura (com diagrama do grafo em Mermaid), como rodar, a tabela de resultados e as limitações conhecidas.
