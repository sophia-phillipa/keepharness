# Instalação do Tail Harness conduzida por IA

Status: especificação operacional. Público: agente de IA com acesso autorizado ao servidor e pessoa responsável pela instalação. Não é uma nova release nem comprovação de instalação executada.

## Objetivo e contrato

Instalar o Tail Harness, reutilizar CLIs, contas, tokens e servidores de modelos já disponibilizados e entregar administração e conversa utilizáveis, com testes e evidências. **O instalador é o agente de IA:** ele inspeciona, explica, executa comandos individuais, verifica resultados e resolve decisões com a pessoa. Não criar nem executar um script de instalação integral; não usar `install.sh`, `setup.sh` ou `tail-harness-install` como substitutos deste procedimento. Scripts de testes continuam permitidos.

Leia `AGENTS.md`, os dois READMEs, `pyproject.toml` e as specs dos adaptadores utilizados. Confirme os comandos na revisão instalada. Os módulos de instalação existentes são referências técnicas legadas; esta spec não os remove nem afirma que deixaram de existir.

### Regras de execução

- Informe antes de cada fase o que fará e, ao terminar, o resultado, a evidência e a próxima etapa. Nunca silencie um bloqueio nem anuncie sucesso só porque um processo iniciou.
- Faça perguntas apenas quando faltar uma decisão necessária. Reutilize respostas e autorizações anteriores. Continue tarefas independentes enquanto aguarda; não escolha pelo usuário uma porta alternativa ou uma ampliação de acesso.
- Não leia valores de tokens para a conversa, logs ou relatório. Reutilize os mecanismos privados do provedor, sob o usuário correto. Presença de arquivo não comprova autenticação.
- Preserve checkout, estado, credenciais, modelos, serviços existentes e trabalhos em execução. Faça backup privado consistente antes de mudanças em estado existente; para SQLite ativo, use backup transacional, não cópia isolada do arquivo.
- Não instalar CLIs, baixar pesos, trocar runtime, habilitar integrações, publicar na rede ou ampliar permissões apenas porque foram descobertos. Execute o escopo já autorizado; pergunte sobre escolhas adicionais necessárias.
- Cada checkpoint recebe `APROVADO`, `BLOQUEADO` ou `NÃO APLICÁVEL`, com evidência, horário e motivo. Falha de um provedor pode permitir continuar os demais, mas impede declarar esse provedor pronto. Ausência de evidência não equivale a aprovação.

## Simulação de instalação limpa

Antes de instalar sobre uma instância existente, prefira um diretório temporário privado, venv novo, estado vazio e portas livres próprias. Teste primeiro um pacote instalado fora do checkout e depois o fluxo editável. Não registre serviço, atalho, linger ou rota VPN durante essa simulação.

`--state` separa configurações/histórico, mas **não é um sandbox do servidor**: descoberta ainda consulta os perfis do usuário e processos/modelos já ativos. Defina também `TAIL_HARNESS_ROOT` para uma pasta temporária própria se houver operações com recursos locais. Consulte somente metadados dos perfis existentes; não execute login, instalação/remoção de plugins ou mudanças em runtimes compartilhados. Se for necessário isolamento de credenciais/processos, use usuário ou container dedicado, reconhecendo que isso muda o que será descoberto.

Em teste local real, habilite somente o modelo escolhido na instância temporária; conceda leitura/escrita/terminal apenas para um projeto sintético, sem rede, hooks ou integrações. Aguarde disponibilidade do runtime, não concorra com inferência alheia, imponha prazo e cancele somente o próprio job ao excedê-lo. Ao terminar, encerre somente os processos da simulação e confira os listeners de produção. Preserve relatório e evidências, informando que diretórios em `/tmp` são temporários.

## CP-01 — Identificar servidor e preservar a instalação

1. Confirme usuário efetivo, sistema, arquitetura, checkout/revisão e alterações locais. Registre se é primeira instalação, atualização ou retomada.
2. Verifique Python 3.11+, venv/pip, espaço, RAM, Node/Chromium quando necessários aos testes e acesso às dependências. GPU é opcional; não carregar modelos para medir disponibilidade.
3. Identifique serviço, processo, ambiente Python, diretório de estado e portas realmente usados. Compare o ambiente do terminal ao do serviço, inclusive PATH e acesso aos perfis dos CLIs.
4. Use por padrão o estado `~/.local/share/tail-harness` e um checkout permanente. Inventarie `settings.json`, `local-profiles.json`, banco e anexos existentes, sem expor conteúdo privado.
5. Localize código com Graphify/rg antes de leituras amplas. As referências de implementação estão no final desta spec.

**Aceite:** destino e usuário inequívocos; pré-requisitos atendidos ou lacunas identificadas; estado anterior preservado. Não instalar uma segunda instância por não reconhecer a primeira.

## CP-02 — Resolver portas e escolhas necessárias

Verifique listeners e seus processos antes de iniciar qualquer serviço. Padrões: administração `8094`, conversa `8095`; `8096` só é relevante se um runtime local gerenciado for solicitado. Ollama existente normalmente usa `11434`; descubra o endereço real. Administração permanece em `127.0.0.1`.

Se a porta estiver ocupada, descubra se pertence à própria instalação. Reutilize a instância correta quando cabível. Para conflito com outro serviço, apresente portas livres verificadas e pergunte, por exemplo: **“A porta 8094 está ocupada por outro serviço. Qual porta deseja usar para a administração? A 8097 está livre neste momento.”** Aguarde a escolha, confira novamente antes do bind e atualize serviço, atalhos, URLs e testes. Não encerre o ocupante.

Resolva somente as demais escolhas ainda desconhecidas: provedores/modelos desejados, projetos autorizados, permissões, início manual/no login/antes do login e acesso local/VPN. Havendo runtime externo, confirme reutilização, sem migrá-lo para o diretório interno por iniciativa própria.

**Aceite:** portas distintas válidas, entre 1024 e 65535 para administração/conversa, e decisões registradas. Rotas VPN existentes precisam ser tratadas antes de mudar suas portas, conforme a validação do painel.

## CP-03 — Instalar dependências e iniciar a administração

O agente executa cada comando, inspeciona o retorno e só então avança. Exemplo para um checkout já obtido da origem autorizada, com variáveis ajustadas e caminhos absolutos:

```sh
TH_CHECKOUT=/caminho/absoluto/tail-harness
TH_VENV="$HOME/.local/share/tail-harness/venv"
TH_STATE="$HOME/.local/share/tail-harness"
TH_ADMIN_PORT=8094
python3 -m venv "$TH_VENV"
"$TH_VENV/bin/python" -m pip install -e "$TH_CHECKOUT[test]"
"$TH_VENV/bin/python" -m pip check
"$TH_VENV/bin/python" -m control --port "$TH_ADMIN_PORT" --state "$TH_STATE"
```

Não recrie às cegas um ambiente existente. O último comando permanece em primeiro plano: acompanhe-o em uma sessão gerenciada ou use a unidade aprovada abaixo. Instalação editável depende da permanência do checkout nesse caminho. Não executar uma instalação de wheel e uma editável simultaneamente por rotina.

Para Linux/systemd, se início automático fizer parte do escopo, o agente escreve e revisa a unidade de usuário individualmente, usando `control/install.py:files` como referência, sem executar o instalador. Deve conter Python absoluto, `-m control`, porta e estado escolhidos, PATH que encontre os CLIs reais, `UMask=0077` e política de reinício. Valide com `systemd-analyze --user verify CAMINHO_DA_UNIDADE`, recarregue o gerenciador e habilite/inicie somente a unidade alvo. Não iniciar sobre o processo manual na mesma porta. Atalho desktop é opcional em servidor sem sessão gráfica; quando aplicável, deve abrir a URL efetiva e iniciar o serviço, sem reinstalar nada.

Linger só quando início antes do login tiver sido solicitado; falha de autorização do sistema deixa essa etapa bloqueada. Sem systemd, registre o modo manual ou o supervisor escolhido e suas limitações, sem declarar autostart validado.

Abra `/` e confira assets e `/api/state` com a mesma sessão HTTP: a página inicial estabelece o cookie administrativo. Não contornar essa proteção diante de um 401.

**Aceite:** pacote importável pelo Python instalado, dependências consistentes, administração responde e executa o checkout/estado esperados. Serviço e atalho, quando solicitados, apontam à instalação correta.

## CP-04 — Descobrir modelos e recursos de todos os provedores

Informe explicitamente: **“Vou ler os modelos disponíveis nos servidores e os recursos já configurados nos CLIs para preencher os painéis dos provedores. Essa leitura não habilita permissões novas.”**

Faça a varredura completa das categorias abaixo em todos os provedores suportados, sob o usuário/perfil efetivo do serviço. Complete a descoberta automática com consultas oficiais somente leitura disponíveis na versão instalada. “Total” significa cobrir cada categoria e registrar limites, erros e paginação; não percorrer indiscriminadamente arquivos pessoais, extrair segredos ou prometer descobrir recursos que o CLI não expõe.

| Categoria | Levantar e conferir |
| --- | --- |
| Provedor e motor | Binário, versão, perfil, autenticação verificável, adaptador e `adapter_spec_revision`; distinguir inferência de execução de ferramentas/sessões. |
| Modelos | IDs, fonte da listagem, endpoint local quando houver, esforços/capacidades declarados e disponibilidade observada. Alias ou arquivo de peso não comprova modelo utilizável. |
| Características | Streaming, continuidade, cancelamento, ferramentas, aprovações, modalidades/anexos, cotas e isolamento; marcar desconhecido quando não confirmado. |
| Conectores | MCP stdio/HTTP, apps de conta quando expostos, IDs, escopo global/projeto, configurado/habilitado, autenticação conhecida e permissões. |
| Plugins e extensões | Instalados, habilitados e apenas disponíveis para instalar, separadamente; não instalar entradas do catálogo durante a leitura. |
| Skills, hooks e artefatos | Recursos já habilitados/expostos pelo provedor, instruções e templates relevantes, nomes/IDs e capacidades de produzir/obter artefatos. Não importar conteúdo de conversas, arquivos ou credenciais como inventário. |
| Projetos e acesso | Raízes e permissões já autorizadas, uploads, rede e seleção por modelo; projeto descoberto não é projeto autorizado. |

Fontes atuais: `python -m control --scan`, inventário do painel (`GET /api/state`), verificação de conta/modelos (`POST /api/check`, com `provider`) e catálogo (`POST /api/integration-catalog`, com `provider`). Requisições administrativas de escrita exigem cookie obtido em `/` e `X-Harness-Admin: 1`, além de origem válida. Codex usa listagem de modelos do CLI; Claude apresenta aliases cuja disponibilidade depende da conta. Para Gemini e DeepSeek, siga seus adaptadores. Descubra processos/servidores llama.cpp e consulte modelos locais; Ollama cloud não deve ser rotulado como inferência local. Endpoint que lista modelos ainda precisa satisfazer o contrato de execução do adaptador.

Para catálogos Codex/Claude, o código usa `codex mcp list --json`, `claude mcp list` e `plugin list --available --json` em cada CLI. Confirme suporte pela versão/ajuda instalada. Lista MCP vazia é resultado válido; erro ou saída desconhecida não é lista vazia. O catálogo ampliado atual cobre Codex/Claude; Gemini tem descoberta de MCP configurado. Local e DeepSeek compartilham o ecossistema do motor Codex, sem que isso prove compatibilidade de todo recurso com esses modelos.

**Aceite:** matriz por provedor cobrindo todas as categorias, com fonte, estado e lacunas. Resuma ao usuário os modelos encontrados antes de configurar a seleção. Diferencie `descoberto`, `configurado`, `habilitado`, `autenticado`, `testado` e `não suportado`.

## CP-05 — Preencher e conferir os painéis

1. Preencha os cartões pelos mecanismos suportados de configuração, reutilizando IDs e ícones reais. Identifique **Modelo Local via Codex** e **DeepSeek via Codex** quando essas forem as combinações efetivas; não anunciar motores alternativos ainda não implementados.
2. Apresente modelos e recursos já encontrados e habilitados no perfil de origem. Reconcile cada item do inventário com o painel do provedor: ID, estado, seleção e origem. Detectado não significa autorizado para todas as tarefas do Tail Harness.
3. A instalação adota como padrão descobrir e ativar os recursos que já estejam instalados/configurados, autenticados e suportados pelo Tail Harness, reutilizando as permissões e os projetos já autorizados. Em atualizações comuns, preserve uma desativação explícita feita pela pessoa. Credencial presente não prova autenticação; não faça login interativo, instale CLIs, baixe modelos, amplie acesso ou dispare inferência paga para completar a matriz. Separe a leitura do inventário da gravação da seleção. Salvar modelo habilitado pode iniciar o harness: confira portas e alcance antes de salvar.
4. Registre explicitamente recursos que o painel não representa. Não invente campos em `settings.json`, nem declare que um artefato/skill está no painel só porque existe no servidor. Para um recurso obrigatório sem suporte, marque o checkpoint bloqueado e descreva a implementação necessária; para um opcional, registre a limitação aceita.
5. Após salvar, aguarde a configuração efetiva: confira `config_revision` e ausência de `config_reload_error` em `/v1/version`, além do modelo, projeto e permissões esperados em `/v1/models?project_id=ID`. O salvamento administrativo pode anteceder a recarga do processo de conversa; não submeta uma tarefa antes dessa convergência. Reabra/recarregue o painel e compare a configuração persistida com a matriz. Verifique nomes, ícones, modelo, motor, integrações selecionadas e projetos efetivamente disponíveis na conversa.

**Aceite:** nenhum recurso conhecido omitido silenciosamente; os itens representáveis estão nos cartões corretos, e lacunas têm tratamento explícito. Ao menos uma combinação autorizada precisa funcionar para entrega operacional.

No padrão da instalação, classifique separadamente **catalogado**, **instalado/configurado**, **credencial presente**, **autenticado**, **habilitado** e **testado**. Ative tudo que esteja instalado/configurado, autenticado e tenha contrato implementado, sem mudar a lista de projetos nem permissões. Catálogo sem instalação fica disponível para consulta, não instalado; recurso sem autenticação fica pendente de login; recurso sem suporte fica marcado como não suportado. Conectores configurados podem ser selecionados somente quando forem compatíveis com o provedor/motor atual e permanecerem dentro do escopo já concedido. Uma entrada de catálogo ou configuração não prova operação real; marque-a como testada apenas após uma chamada mínima, autorizada e somente leitura.

## CP-06 — Testes pós-instalação obrigatórios

Execute testes dirigidos à instalação e aos contratos selecionados, respeitando `AGENTS.md`. Suíte completa somente nos marcos Git previstos; não criar um marco para dispará-la. Fixtures não comprovam acesso real a contas. Testes reais devem usar dados sintéticos, projeto temporário autorizado e orçamento curto, sem benchmarks de GPU ou inferências pagas não autorizadas.

| Teste | Procedimento e resultado exigido |
| --- | --- |
| Pacote instalado | Execute `"$TH_VENV/bin/python" -m control.install_check`. Deve passar fora do checkout, com estado temporário, porta livre, assets/API válidos e nenhum provedor habilitado. Não substitui o teste da instância definitiva. |
| Administração definitiva | Abra a URL escolhida; confira `/api/state`, arquivos estáticos, ausência de erro de carregamento e estado/versão corretos. Preserve cookie/origem nas consultas. |
| Descoberta e painel | Compare matriz, catálogo e cartões após recarga. Teste lista vazia e falha de consulta sem confundi-las; examine mensagens e persistência. |
| Prontidão da conversa | Aguarde conexão completa: modelos, projetos, permissões e histórico carregados. Verifique `/v1/models` e `/v1/projects` na sessão autorizada; um HTTP 200 na página inicial não basta. |
| Execução real por combinação habilitada | Envie uma tarefa curta para cada combinação modelo/motor habilitada: responder um marcador, ler um arquivo sintético por ferramenta e produzir um pequeno resultado no projeto de teste. Confira estado terminal, resposta não vazia, modelo efetivo e ausência de fallback oculto. Sem autorização/cota, registre bloqueio, não aprovação por fixture. |
| Continuidade e artefato | Continue a mesma tarefa usando uma informação sintética anterior; recarregue e confira histórico. Abra/baixe o resultado e confira conteúdo. Anexos/modalidades só são aprovados mediante teste do formato suportado. |
| Permissões e cancelamento | Em cenário sintético, confira aprovação/negação conforme a política e cancelamento de um job próprio. Não conceda acesso adicional para fazer o teste passar. Testes negativos perigosos ficam em fixtures isoladas. |
| Conector/plugin habilitado | Quando permitido, faça uma operação mínima somente leitura, com escopo e dados de teste; confira execução real. Configuração listada sem chamada bem-sucedida fica marcada como não testada. |
| Reinício e recuperação | Sem jobs ativos e dentro da autorização, reinicie apenas o Tail Harness; confira retorno da administração, retomada configurada, configurações/histórico e reconexão. Não encerre runtime compartilhado. Boot real só pode ser declarado testado após observação efetiva. |
| VPN, se solicitada | Teste de cliente autorizado, bloqueio de cliente sem autorização e administração inacessível externamente. Não abrir firewall público nem habilitar compartilhamento por conveniência. |

Regressões dirigidas disponíveis nesta revisão (confirme os arquivos antes de executar):

```sh
"$TH_VENV/bin/python" -m pytest -q tests/test_distribution.py tests/test_integration_catalog.py
node tests/admin-integrations.spec.cjs
```

O primeiro comando valida distribuição, inicialização e catálogo, não a instalação inteira. Execute pytest no modo padrão: alguns testes importam helpers de outros arquivos em `tests/`; `--import-mode=importlib` sem ajustar esses imports impede a coleta. Em verificação de wheel, rode fora do checkout para não importar acidentalmente o código-fonte no lugar do pacote. O teste de UI exige Node, Playwright e Chromium; informe `PLAYWRIGHT_MODULE` se necessário. Selecione testes adicionais do adaptador e das permissões realmente configurados. Para casos de navegador que dependem de servidor, leia a fixture e inicie servidores de teste separados. `scripts/test-ui.sh` executa toda a regressão de UI e reserva 18094/18095: use somente no marco apropriado e verifique conflitos antes. Falta de dependência ou impossibilidade de abrir navegador é teste bloqueado, não aprovado.

**Aceite:** resultados registrados por teste e combinação; sem falhas obrigatórias abertas. Sucesso do pacote, sucesso das fixtures e sucesso ponta a ponta são evidências diferentes.

## CP-07 — Tratar falhas e retomar

Antes de alterar algo, recolha erro sanitizado, horário, comando, versão, processo/porta, usuário e estado efetivos. Consulte logs da unidade (`journalctl --user -u tail-harness -n 50`) ou da sessão manual. Evite dumps de ambiente e configs com segredos.

| Falha | Ação do agente |
| --- | --- |
| Porta ocupada | Identificar ocupante, perguntar alternativa, conferir e atualizar referências. Nunca matar o ocupante por padrão. |
| CLI não encontrado pelo serviço | Comparar PATH/binário/usuário com o terminal e corrigir a unidade ou configuração suportada; não reinstalar CLI automaticamente. |
| Conta/token inválido | Conferir mecanismo de credencial e acesso do usuário; orientar fluxo oficial ou disponibilização privada. Não solicitar token na conversa nem substituir identidade silenciosamente. |
| Modelo listado, execução falha | Conferir revisão do adaptador, protocolo, endpoint, capacidade de ferramentas, cota e erro concreto. Não trocar para outro provedor/modelo sem decisão aplicável. |
| Recurso ausente no painel | Distinguir descoberta incompleta, não suportado e falha de UI; registrar ID e fonte. Não habilitar por edição improvisada nem declarar integração concluída. |
| HTTP 401/403 ou interface bloqueada | Conferir sessão/cookie, origem, identidade e permissões; não desativar controles. |
| Dependência, serviço ou teste falha | Corrigir causa demonstrada, repetir o teste afetado e registrar antes/depois. Se a mesma falha reaparecer, revisar hipótese; não repetir indefinidamente. |

Correções reversíveis dentro do escopo são responsabilidade do agente. Mudança destrutiva, nova identidade, novo gasto/acesso não autorizado ou escolha de usuário pendente exige esclarecimento. Antes de restaurar backup, confira trabalhos/dados posteriores para não perdê-los. Reversão afeta só arquivos/processos criados ou alterados nesta instalação, nunca credenciais ou serviços alheios.

**Aceite:** falhas corrigidas com reteste ou bloqueios documentados com próxima ação concreta. Ao retomar, confira o estado real e reutilize checkpoints ainda válidos; mudanças invalidam apenas as evidências afetadas.

## CP-08 — Entrega e critério de conclusão

Entregue um relatório sanitizado fora do versionamento, em local combinado, com:

- revisão instalada, usuário, checkout, Python, estado, portas, URLs e modo de inicialização;
- matriz de modelos/motores, características, conectores, plugins e artefatos, incluindo lacunas do painel;
- decisões do usuário e permissões aplicadas, sem valores de credenciais;
- checkpoints e testes: comando/ação, esperado, observado, horário, resultado e evidência local;
- correções realizadas, recursos não testados, limitações e instruções de retomada/rollback.

Na mensagem final da instalação, explique **como abrir, como iniciar/parar, como repetir os testes curtos e onde diagnosticar erros**. Use caminhos e portas efetivos, não placeholders. Diga quais testes você, agente instalador, executou; não repasse ao usuário os testes que pode executar. Quando precisar de login interativo, decisão ou autorização ainda ausente, informe exatamente a pendência.

Só declare **“instalação concluída e testada”** quando os checkpoints obrigatórios passarem, houver ao menos uma combinação operacional e todos os recursos obrigatórios do escopo estiverem validados. Caso contrário, entregue **“instalação parcial — bloqueios: …”**. Recursos opcionais não aplicáveis precisam de justificativa; não transforme falhas em “não aplicável”.

## Referências de implementação

- [Entrypoint e portas](../control/cli.py), [dependências](../pyproject.toml), [modelo legado de unidade e atalho](../control/install.py).
- [Descoberta](../control/discovery.py), [inventário de integrações](../control/integrations.py), [catálogo de CLIs](../control/integration_catalog.py).
- [Administração e validação de configuração](../control/server.py), [contratos de adaptadores](../Adapters/README.md).
- [Smoke de pacote instalado](../control/install_check.py), [regressão de UI](../scripts/test-ui.sh).

Esta especificação descreve o trabalho que o agente deverá executar em cada instalação. Sua revisão documental não certifica uma instalação, conta, modelo ou conector deste servidor.

Validação de referência: [simulação limpa de 20/09/2026](installation-validation-2026-09-20.md), com escopo, resultados e impedimentos. Ela não substitui os checkpoints de uma nova instalação.
