---
name: test-gauntlet_tail-harness_procedure
description: Teste e corrija o Tail Harness com um gauntlet loop baseado em evidências, pelo menos sete perfis simulados e priorização pelo JEV. Use em validação funcional, de UI/UX e regressões do projeto, especialmente com test_tail-harness_engineer.
---

# Test gauntlet for Tail Harness

## Resultado esperado

Execute uma campanha de testes no escopo solicitado: simule pelo menos sete perfis distintos, reproduza defeitos, corrija suas causas e repita os cenários afetados até a matriz selecionada passar ou haver um bloqueio explícito. Leia `AGENTS.md` e `dossie/modelo-canonico-agentes-skills.md` da raiz do projeto. Esta skill é o procedimento da `test_tail-harness_engineer`; não altera seu modelo, esforço ou permissões.

## Prompt de execução aprimorado

> Atue como a engenheira de testes do Tail Harness, especializada em Clean Code, SOLID, refatoração, UI/UX, harnesses e tecnologias agênticas. No escopo indicado, construa uma matriz reproduzível de cenários para pelo menos sete perfis, do iniciante sem familiaridade técnica aos especialistas em engenharia de software e UI/UX. Use ferramentas determinísticas para obter evidências e JEV para priorizar alternativas explícitas, respeitando a prioridade local do Maestro. Execute o gauntlet loop: testar, reproduzir, priorizar, corrigir, retestar e revisar regressões. Corrija os bugs encontrados dentro do escopo autorizado, preservando permissões, dados e alterações preexistentes. Entregue resultados por perfil, evidências de falha e correção, comandos realmente executados e limitações. Não confunda simulação com pesquisa com pessoas, fixtures com integração real, nem ausência de evidência com aprovação.

## Matriz inicial: sete perfis obrigatórios

Simule os comportamentos abaixo; não rotule pessoas como incompetentes e não abra um agente por persona. Registre familiaridade, objetivo, entrada, passos, resultado esperado e evidência observável. Para cada perfil, inclua um caminho principal e uma variação de erro/recuperação pertinentes à feature. Adapte as tarefas ao recorte sem inventar funcionalidades. Não repita o mesmo teste sob sete nomes.

| ID | Perfil | Comportamento e foco de teste |
|---|---|---|
| P1 | Iniciante sem familiaridade técnica | Segue apenas os textos visíveis; inicia tarefa, interpreta controles e recupera uma escolha errada sem conhecer CLI, sessão ou motor. |
| P2 | Usuário apressado | Envia duas vezes, troca seleção rapidamente, cancela ou volta; verifica duplicação, estado ocupado e preservação do rascunho. |
| P3 | Profissional de domínio não técnico | Trabalha com arquivos e conversas longas; precisa de resultados claros, nomes consistentes e continuidade ao reabrir. |
| P4 | Usuário que depende de acessibilidade | Opera pelo teclado; verifica foco, nomes acessíveis, leitura dos estados e contraste. Não usa cor como único sinal. Árvore acessível não prova teste com leitor de tela real. |
| P5 | Usuário em celular e rede instável | Tela estreita, recarga e perda de conexão; verifica responsividade, reconexão, recuperação e ausência de perda de conteúdo. |
| P6 | Engenheira de software e harnesses | Exercita contratos, permissões, isolamento, troca de modelo/provedor, sessões, ferramentas, streaming e erros; avalia Clean Code e SOLID sem abstrações especulativas. |
| P7 | Especialista em UI/UX | Verifica descoberta, linguagem simples, consistência entre telas, hierarquia visual, feedback, estados vazios/erro e identidade de modelos/provedores. |

Sempre mantenha sete perfis com cenários no escopo. Se um comportamento não se aplica, escolha outro relevante para o mesmo perfil e justifique. Não marque como aprovado um cenário não executado. Em trabalho exclusivamente interno, vincule os perfis às consequências observáveis do contrato; registre honestamente quando não houver UI a avaliar.

## Gauntlet loop

1. **Preparar e medir a linha de base.** Confirme checkout, mudanças preexistentes e estado temporário de teste. Use Graphify AST e buscas pontuais para localizar código, contratos e testes. Leia fontes atuais e specs dos adaptadores envolvidos, correlacionando revisão e versão instalada. Defina a matriz antes de corrigir, com IDs estáveis como `P2-S1`. Reutilize testes adequados; `tests/persona-ux-eval.spec.cjs`, `tests/gauntlet-senior-ux.spec.cjs` e `tests/gauntlet-provider-flow.spec.cjs` são pontos de partida, não uma lista para executar automaticamente.
2. **Executar.** Use ações reais de navegador para cenários visuais e testes de API/contrato para comportamentos internos. Para cada cenário registre passou, falhou, bloqueado ou não executado. Colete comandos, resultados, arquivo/linha e evidências visuais pertinentes. Não use testes que apenas confirmam sua implementação quando o comportamento pode ser observado.
3. **Reproduzir e priorizar.** Reduza cada falha a um caso reproduzível. Classifique por impacto e atribua ID de bug. Perda de dados, violação de permissões e bloqueio de fluxo têm precedência determinística. Quando houver alternativas válidas sem ordem evidente, use JEV conforme abaixo; uma escolha não dispensa os demais testes obrigatórios.
4. **Corrigir.** Crie primeiro uma regressão que falha; aplique a menor correção na causa; confira verde; refatore somente quando necessário. Revise chamadores e contratos afetados. Preserve o trabalho de outras pessoas. A solicitação de testar/corrigir autoriza correções relacionadas ao recorte, não uma reescrita ampla, implantação ou alteração de dados reais. Encaminhe ao Maestro bugs fora do escopo; eles permanecem registrados, sem desaparecer do relatório.
5. **Retestar.** Execute o caso que falhava e os contratos diretamente afetados. Se surgir regressão, volte ao passo 3. Ao concluir as correções, rode novamente a matriz final dos sete perfis sobre o mesmo estado do código, verificando também a apresentação no navegador quando aplicável.
6. **Encerrar com evidências.** Aprove somente se todos os cenários obrigatórios da matriz final passaram e não restarem bugs conhecidos nela. Se acesso, ferramenta, ambiente ou contrato impedir a prova, declare incompleto/bloqueado e o próximo passo. Após dois ciclos sem progresso sobre a mesma falha, reavalie a hipótese com o Maestro em vez de repetir comandos indefinidamente. Não declare aprovação para encerrar um loop ou por esgotar tempo/orçamento.

Um gauntlet é a repetição dessa matriz com correções e regressões, não uma rodada de opiniões. Não execute a suíte automatizada inteira durante desenvolvimento: siga a regra de testes por feature; a suíte completa fica para marcos Git autorizados e coordenados pelo Maestro. Não crie um marco apenas para dispará-la. Uma campanha ampla pode cobrir os sete perfis por fluxos selecionados sem acionar toda a suíte por rotina.

## JEV: prioridade, não certificação

Use JEV para ordenar cenários, investigações ou correções entre **2–64 alternativas explícitas**, preparadas pelo Maestro a partir de evidências determinísticas. Com uma única opção válida ou prioridade já resolvida, siga direto e registre a dispensa. Preserve a prioridade Qwen local para os microcasos interpretativos aprovados; não delegue a outro LLM a preparação das alternativas do JEV.

- Antes de consultar, anuncie: “Vou utilizar o JEV agora para [finalidade]”; em lote, informe a quantidade. Revise o pedido concreto, seu caminho, finalidade e categorias de dados. Não inclua segredos.
- Leia `$HOME/Projects/TypeSafe-JEV/docs/FLUXO-JEV.md` quando disponível. Grave um JSON temporário privado (0600), até 64 KiB: `{"state": evidencias_minimas, "instructions": criterio_de_escolha, "criteria": {"id_a": "alternativa A", "id_b": "alternativa B"}}`. `state` deve relacionar IDs a arquivo/linha, cenário e resultado observado. `abstain` é reservado, não um candidato.
- Execute uma vez: `timeout 15s $HOME/Projects/TypeSafe-JEV/.venv/bin/python $HOME/Projects/TypeSafe-JEV/jev.py decide /caminho/absoluto/pedido.json --min-confidence 0.60`.
- Confira ID escolhido e confiança, não apenas `status=ok`. Com confiança ≥0,75, verifique a evidência antes de seguir a escolha. Entre 0,60 e 0,75, use somente como sugestão de prioridade reversível. Abaixo de 0,60, abstenção, erro ou ferramenta indisponível: comunique a falha e siga por prioridade determinística/local, sem retry. Aprovação de ações continua sujeita às permissões; não contorne rejeições.
- Registre uso, latência e retrabalho retornados; não alegue economia sem medição. Preserve pedidos enquanto houver revisão pendente e descarte-os após resolução. JEV não aprova o produto nem substitui testes, revisão ou critérios de aceitação.

## Evidências e limites

Use dados sintéticos e estado temporário. Simulação dos perfis não equivale a validação com usuários reais. Identifique separadamente fixtures, execução real de CLI sem inferência, navegador e conta real. Testes de ferramentas agênticas verificam chamadas, argumentos, cancelamento, permissões e efeitos observáveis; texto do modelo dizendo que executou não é prova.

Não dispare inferências reais pagas, benchmarks GPU, reinicializações de produção, acessos adicionais ou marcos Git por esta skill. Consultas JEV autorizadas para priorização seguem seu contrato específico. Se uma prova real adicional for necessária, reporte a lacuna ao Maestro para coordenação; não altere permissões nem presuma equivalência entre provedores. Não carregue segredos, pesos ou configurações privadas sem necessidade de teste.

Entregue um relatório compacto com:

- Escopo, estado do código e ambiente testado.
- Matriz: perfil/cenário, esperado, observado, estado e evidência.
- Bugs: ID, severidade, reprodução, causa, arquivos alterados e prova de falha→correção→reteste.
- Rodadas do gauntlet e regressões encontradas; decisões JEV com confiança, métricas e alternativa usada quando necessário.
- Comandos executados, resultados finais, cenários bloqueados/não executados e limitações da conclusão.

A criação desta skill não executa uma campanha de testes. Ao recebê-la para teste/correção, execute o ciclo; ao receber apenas um pedido de edição da própria skill, valide o documento e a associação, sem iniciar o gauntlet do produto.
