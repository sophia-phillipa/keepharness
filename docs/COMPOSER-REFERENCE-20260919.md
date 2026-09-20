# Caixa de mensagem baseada na referência visual

Cápsula com cantos arredondados, área de escrita ampla e barra inferior compacta. Anexar usa o símbolo + sem moldura; acesso aparece com escudo; modelo e esforço ficam adjacentes, sem campos emoldurados nem rótulos redundantes; envio usa botão circular de contraste neutro. A paleta existente continua respeitada. Não foi adicionado um microfone sem funcionalidade correspondente.

Os seletores continuam nativos e acessíveis por teclado. Acesso automático preserva os limites administrativos existentes. Nomes de modelos aparecem sem emojis, com GPT-6 Astra/GPT-5.6 nos nomes conhecidos. Tarefa opcional foi movida para fora da cápsula. A ajuda permanece disponível para leitores de tela, incluindo as permissões associadas ao modelo. Contêineres estreitos reorganizam a barra em duas linhas, mesmo com painéis laterais abertos.

Alterados: `agent_service/index.html`, `agent_service/ui.css`, `agent_service/ui.js` e `tests/harness-layout.spec.cjs`. Alterações preexistentes preservadas. Sem commit, criação de branch, nova versão ou reinício do serviço em uso.

## Validação

- `node tests/harness-layout.spec.cjs` com Playwright do runtime local: seis temas, 1515/768/390 px, modos de acesso, nomes longos, painel de atividade, zoom de 200%, seleção por teclado, configurações, anexar e payload de envio; captura dos temas claro, ametista e Arizona. Assets do checkout com APIs simuladas, sem inferência.
- `node tests/harness-ux.spec.cjs` em servidor temporário isolado: 12 cenários aprovados.
- `node tests/harness-model-permissions.spec.cjs`: aprovado.
- `node tests/harness-connection.spec.cjs`: aprovado, incluindo bloqueio e reconexão.
- `node --check agent_service/ui.js` e `git diff --check`: aprovados.

O teste de layout anteriormente incompatível com o menu foi atualizado para abrir o menu real e verificar o comportamento final. A suíte completa não foi executada por não haver marco Git.
