# Menus de modelo/esforço e proporções de tela

Modelo e esforço passam a usar o mesmo popover nativo do acesso: borda, descrições, seleção marcada, navegação por setas/Home/End, confirmação com Enter e fechamento com Escape/Tab. Opções são construídas a partir do catálogo e dos esforços efetivamente disponíveis para o modelo, sem inventar capacidades. Os selects existentes continuam como fonte de valor para submissão, permissões e preferência salva. Os gatilhos refletem alterações, recuperação de sessão e bloqueios de execução.

A conversa e o composer usam uma coluna central de até 1040 px CSS. A caixa vazia passa a cerca de 132–134 px no desktop; tipografia fixa/rem evita aumento excessivo em TVs 4K. Botões de envio de 44 px; reorganização por largura real do contêiner preserva uso com painéis laterais e em celulares. Os menus têm largura e altura limitadas à janela. Não foram alteradas configurações do monitor, escala do sistema, grants ou backend.

## Verificação

- `tests/harness-layout.spec.cjs`: passou em seis temas e larguras 3840, 1920, 1366, 768 e 390; limite de largura/altura, ausência de sobreposição, zoom de 200%, painel de atividade, nomes longos, menus modelo/esforço, atualização de esforços por modelo, teclado, payload e preferência após reload. Também criou contexto 1920x1080 com deviceScaleFactor=2, representando 4K com escala 200%. Validação de navegador, sem afirmar teste físico de leitura à distância na TV.
- `tests/harness-ux.spec.cjs`: 12 cenários passaram.
- `tests/harness-model-permissions.spec.cjs`: passou.
- Teste de conexão ampliado para abrir o menu de modelo e conferir seu fechamento na perda de acesso.
- Sintaxe JavaScript e `git diff --check`; mapa AST Graphify atualizado.

Assets reais do checkout com APIs simuladas nos testes browser. Sem inferência, nova versão, commit, branch ou suíte completa. Mudanças preexistentes preservadas.
