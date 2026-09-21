# Modelo canônico de agentes e skills

## Regra obrigatória

Todo agente e toda skill novos, próprios deste projeto, devem usar **objective_context_role** com nomes sempre em inglês, em ASCII minúsculo, sem acentos. Usar exatamente dois `_` para separar os três radicais; dentro de cada radical, usar `-` entre palavras compostas (kebab-case). Descrições e instruções podem permanecer em português. São exatamente três radicais, obtidos dividindo o nome por `_`: objetivo, contexto e função. Cada radical pode ser composto; `tail-harness` é uma única entidade no radical de contexto. O contexto fixo `tail-harness` ocupa sempre o segundo radical. Não abreviar para `th` ou `tailharness`.

- **Objetivo:** verbo em inglês na forma base e, quando necessário, objeto que diferencia a especialidade: `test`, `integrate-codex`, `develop-interface`.
- **Contexto:** sempre `tail-harness` neste projeto.
- **Função/cargo:** papel do agente (`engineer`) ou função da skill (`procedure`, `guide`, `audit`). O tipo do artefato também é identificado pelo diretório e formato.

Exemplo do agente: `test_tail-harness_engineer` = `test` + `tail-harness` + `engineer`.
Exemplo para futura skill: `validate-interface_tail-harness_procedure`. Este exemplo não cria uma skill.

O nome não contém modelo, esforço, versão, senioridade ou nome pessoal; esses dados pertencem à configuração/descrição. IDs devem ser únicos no seu catálogo e compatíveis com o consumidor. Usar o mesmo ID em inglês em todos os idiomas da interface e documentação.

## Arquivos e contratos

Agentes: `.codex/agents/<id>.toml`, com `name` igual ao nome-base do arquivo. Preservar `description`, `model`, `model_reasoning_effort` e `developer_instructions` conforme o formato já usado no projeto. Descrição e instruções devem explicitar objetivo, área, limites, coordenação e evidências esperadas.

Skills: no diretório de skills reconhecido pelo runtime adotado, usar `<id>/SKILL.md` e o mesmo ID no campo `name` do frontmatter. A descrição informa quando usar; o corpo define entradas, procedimento, limites e validação. Confirmar o formato e a descoberta no consumidor real; este documento não presume portabilidade entre motores.

Skills externas, plugins instalados e agentes globais pertencem aos seus distribuidores: não renomeá-los automaticamente. O padrão vale para os artefatos próprios criados neste projeto. Não é necessário renomear IDs técnicos de API, provedores ou modelos.

## Catálogo e migração

| Nome anterior | Nome canônico |
|---|---|
| `nucleo` | `maintain-core_tail-harness_engineer` |
| `modelos_mcp` | `integrate-contracts_tail-harness_engineer` |
| `interface` | `develop-interface_tail-harness_engineer` |
| `operacao` | `operate-runtime_tail-harness_engineer` |
| `provedor_codex` | `integrate-codex_tail-harness_engineer` |
| `provedor_claude` | `integrate-claude_tail-harness_engineer` |
| `provedor_gemini` | `integrate-gemini_tail-harness_engineer` |
| `provedor_deepseek` | `integrate-deepseek_tail-harness_engineer` |
| `provedor_local` | `integrate-local_tail-harness_engineer` |
| Novo | `test_tail-harness_engineer` |

Os nove agentes existentes mantêm seus modelos, esforços e áreas. A nova engenheira usa `gpt-6-astra` com esforço `low` (Astra Light). Sua persona adota a perspectiva de 30 anos de desenvolvimento e refatoração, com especialização em Clean Code, SOLID, UI/UX, harnesses e tecnologias agênticas; não representa uma biografia humana real.

Atualizar nomes de arquivos, campos `name`, referências cruzadas e documentação operacional juntos. Não manter aliases antigos silenciosos. Documentos históricos de releases preservam os nomes da época; esta tabela explica a migração. Sessões que já carregaram o catálogo podem manter os nomes antigos: a alteração em disco não prova recarga; verificar a descoberta em uma nova sessão antes de alegar disponibilidade no runtime.

## Checklist de criação e revisão

1. Definir os três radicais em inglês e verificar se já existe agente ou skill equivalente.
2. Ler AGENTS.md, limitar responsabilidade e preservar políticas de Maestro, permissões e testes por feature.
3. Criar o arquivo no formato do consumidor, alinhando caminho e `name`.
4. Atualizar este catálogo para agentes, referências e índice de documentação; registrar novas skills próprias com seu caminho.
5. Validar exatamente três partes via `name.split("_")`, com `tail-harness` na segunda e cada parte no padrão `[a-z]+(?:-[a-z]+)*`; validar parsing, unicidade, identidade entre nome/caminho e links; buscar referências operacionais antigas. Testar descoberta no runtime quando disponível e registrar limitações.

Definições `.codex/agents/` e AGENTS.md estão ignoradas pelo Git neste checkout; a instalação local não implica distribuição desses arquivos por clone. Este documento é a referência versionável. A adoção em outro checkout exige instalar as definições e verificar sua descoberta.

## Validação desta alteração — 2026-09-20

Validação local com Python `tomllib` e assertions: os dez agentes e `.codex/config.toml` têm TOML válido; IDs únicos, três componentes semânticos, contexto único e correspondência entre arquivo e `name`. Busca nos Markdown operacionais rastreados, AGENTS.md e definições não encontrou IDs antigos de provedores/contratos. Links para este documento conferidos. `git diff --check` passou. Nenhuma suíte funcional foi executada: a alteração é de configuração e documentação, sem marco Git.

A descoberta no runtime não foi executada: `codex` não está no PATH desta execução e o catálogo desta sessão foi carregado antes da renomeação. Não se declara recarga nem execução do novo agente.

Na decisão inicial, anterior à correção explícita dos separadores pela usuária, JEV priorizou a convenção semântica: `semantic`, confiança 0,87, modelo `jev-1.13.0`, 619 tokens de entrada, 40 de saída e 912 ms retornados. A escolha foi conferida contra os arquivos locais; sem retry ou retrabalho da escolha. Não houve medição de economia.

Correção de nomenclatura: todos os dez IDs e arquivos foram migrados para inglês (`objective_context_role`), incluindo `test_tail-harness_engineer`. A forma portuguesa intermediária foi substituída nas referências operacionais. Validação TOML, unicidade, correspondência nome/arquivo e preservação de modelos/esforços repetida; descoberta no runtime permanece não verificada.

Correção dos separadores: os dez agentes agora usam `objective_context_role`, com `_` entre radicais e `-` dentro de termos compostos. Arquivos, campos `name` e referências operacionais foram atualizados juntos. Validação local repetida: exatamente três radicais, contexto `tail-harness`, TOML válido, nomes únicos e modelos/esforços preservados. A descoberta no runtime permanece não verificada.

## Skills do projeto

| Nome canônico | Arquivo | Agente associado |
|---|---|---|
| `test-gauntlet_tail-harness_procedure` | [SKILL.md](../.agents/skills/test-gauntlet_tail-harness_procedure/SKILL.md) | `test_tail-harness_engineer` |

O mesmo padrão de três radicais se aplica a agentes e skills. Na skill de gauntlet: objetivo `test-gauntlet`, contexto `tail-harness`, função `procedure`. A associação é feita por instrução explícita de leitura no TOML do agente e no AGENTS.md; não pressupõe um campo de configuração de skills inexistente.

### Validação da skill de gauntlet — 2026-09-21

Frontmatter YAML, convenção local de três radicais, sete perfis distintos e associação no TOML conferidos. A descoberta `agent_service.resources.discover` encontrou a skill no projeto; `skills/list` do Codex CLI 0.155.0-alpha.9.2 retornou o nome canônico e `enabled=true`, sem iniciar inferência. O `quick_validate.py` genérico rejeita underscores por exigir hyphen-case; a regra explícita da Sophia foi preservada, sem alterar o validador. O Python do venv não tinha PyYAML; a validação YAML usou o Python do sistema já disponível, sem instalação.

JEV priorizou a linha de base dos sete perfis antes das correções: `baseline`, confiança 0,97, modelo `jev-1.13.0`, 585 tokens de entrada, 41 de saída e 755 ms retornados; uma consulta, sem retry, sem retrabalho da escolha e sem alegação de economia. A criação/validação da skill não executou um gauntlet do produto.
