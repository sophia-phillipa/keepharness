# Menu de acesso e modo total

Caixa de escrita ampliada e com borda mais evidente. O seletor de acesso passa a abrir uma caixa de até 620 px, com borda, descrições, indicação da seleção e quatro opções: Pedir aprovação, Automático, Acesso total e Somente leitura. Em telas estreitas, o menu respeita os limites da janela. Teclado: setas/Home/End, Enter, Escape e Tab. O bloqueio de conexão fecha o menu, preservando o modo selecionado.

`access_mode=full` é aceito pela API. Não amplia permissões administrativas, roots ou rede. Codex/Claude usam never/dontAsk, como já acontece em auto; novas solicitações inesperadas nesses executores não recebem autorização automática adicional. No executor local isolado, ações reconhecidas são aprovadas conforme as permissões efetivas. Solicitações explícitas de ampliação de permissões são negadas no modo total; pedidos de informações ao usuário continuam interativos.

Validação: 21 testes de backend aprovados em `test_approval_policy.py`, `test_model_permissions.py` e `test_native.py`; layout Playwright aprovado nos seis temas, desktop/mobile, teclado, seleção de Acesso total e payload; 12 cenários de UX, permissões por modelo e bloqueio/reconexão aprovados. Sem execução de inferência ou suíte completa. `git diff --check` e sintaxe JavaScript verificados.

Ativação local: serviço estava sem tarefas queued/running. Reinício pelo painel administrativo com verificação de ociosidade; stop/start HTTP 200. `/v1/version` respondeu HTTP 200, versão 0.4.4, build bb0a3bc26aa7. Não houve nova versão, commit ou mudança de grants/padrões globais.
