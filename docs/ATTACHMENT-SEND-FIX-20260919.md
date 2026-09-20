# Envio, seleção e prévia de anexos

O botão de envio parou porque a declaração de `send()` foi removida durante a inserção da árvore de arquivos. O identificador global do elemento `#send` mascarava a ausência de uma função. A função foi restaurada com modo de acesso e título inicial, sem o campo de tarefa removido anteriormente.

Botões de remover anexos permaneciam desabilitados porque os chips eram criados enquanto `uploads` ainda era positivo e não eram reconstruídos ao concluir a anexação da árvore. O bloco final agora atualiza os anexos após liberar esse estado.

Revisão solicitada: Shift deve abranger itens visíveis de diferentes níveis da árvore, incluindo arquivos e pastas; o arraste não deve substituir seu próprio elemento de origem. O limite existente de dez arquivos deve ser apresentado e aplicado tanto ao upload quanto ao navegador de arquivos.

Imagens PNG/JPEG/WebP anexadas recebem `preview_url` autenticada por dono e projeto. A prévia entrega somente o arquivo de imagem validado, com no-store e nosniff; não permite navegar por um caminho arbitrário.

Validação backend: `tests/test_file_previews.py tests/test_project_browser.py` — 9 aprovados. Servidor ativado após confirmação de ociosidade, stop/start HTTP 200. `tests/harness-files-panel.spec.cjs` passou: clique/Enter com prompt e file_ids, seleção mista Shift/Ctrl/Space, arraste, prévia, remoção, limite 10 e feedback hover. Requisições de execução simuladas; nenhum modelo executado nesta validação. Sintaxe e git diff --check aprovados.

Também foi encontrada interceptação de cliques por notificações sobre o compositor. As notificações agora ficam abaixo da barra superior e seu texto não captura eventos do mouse. Botões usam a aparência neutra dos seletores e feedback comum de hover/foco. Miniaturas de 128 × 96 px usam preview autenticado, e o compositor apresenta o contador de anexos.
