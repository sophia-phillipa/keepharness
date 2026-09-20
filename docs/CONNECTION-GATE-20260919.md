# Bloqueio da interface enquanto aguarda o servidor

A interface começa esmaecida, com mensagem central e conteúdo `inert`, impedindo cliques e foco por teclado. Projetos, catálogo de modelos, permissões do projeto selecionado e histórico precisam responder corretamente antes da liberação. Um catálogo de modelos vazio é válido: permite acessar a configuração do servidor.

A conexão é verificada a cada cinco segundos, com limite de cinco segundos por requisição de prontidão e sem sondagens simultâneas. Falha na verificação bloqueia novamente o painel; a recuperação refaz a inicialização automaticamente, preservando o rascunho. Diálogos de configuração abertos são fechados na queda; o diálogo de autenticação continua acessível. A detecção de queda depende da próxima sondagem e de seu timeout, não é instantânea.

Arquivos: `agent_service/index.html`, `agent_service/ui.css`, `agent_service/ui.js`; regressão em `tests/harness-connection.spec.cjs`, cenário atualizado em `tests/harness-ux.spec.cjs`, inclusão no executor `scripts/test-ui.sh`. As demais alterações preexistentes foram preservadas. Nenhuma versão, branch ou commit foi criada para esta mudança.

## Validação realizada

- `node --check agent_service/ui.js`: passou.
- `node tests/harness-connection.spec.cjs` com Playwright instalado no runtime local: passou. Assets reais do checkout, APIs simuladas; cobre indisponibilidade inicial, inicialização parcial, reconexão automática, queda com diálogo aberto, bloqueio de teclado, rascunho, autenticação e catálogo vazio.
- `HARNESS_URL=http://127.0.0.1:18197 node tests/harness-ux.spec.cjs` sobre serviço isolado e APIs simuladas: 12 cenários passaram.
- `HARNESS_URL=http://127.0.0.1:18197 node tests/harness-model-permissions.spec.cjs`: passou.
- Capturas verificadas em 1280 e 390 pixels, temas claro e escuro.
- `git diff --check`: passou; mapa Graphify AST atualizado sem clustering.
- Teste adicional `harness-layout.spec.cjs` parou na linha 15: tenta clicar em Configurações sem abrir o menu que já existe no checkout. Não foi alterado nem declarado aprovado.

Não foi executada a suíte completa, nem inferência de modelo, benchmark ou reinício do serviço de uso da usuária.
