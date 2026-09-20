# Navegação de arquivos no painel direito

Pedido: compartilhar o painel direito entre Arquivos (visualização inicial) e Atividade, com controles na barra superior. A usuária revisou explicitamente o escopo para permitir navegação no sistema, independente do projeto. O projeto da conversa permanece como destino dos anexos; as permissões de execução não são ampliadas.

A árvore carrega filhos por expansão, mostra arquivos e pastas visíveis, permite seleção múltipla e arraste para o prompt. O navegador inicia em Pastas Locais e mantém Pastas Externas, com destaque na opção selecionada; os atalhos Sistema e Mídias externas do sistema foram retirados. Pastas de sistema e itens ocultos são filtrados conforme o sistema operacional; the local Linux host foi confirmado localmente nesta revisão. A revisão final remove checkboxes e o botão Anexar seleção: a anexação ocorre pelo arraste, com seleção visual por clique e modificadores. A navegação é independente do projeto. A seleção deve ser associada ao destino no momento da anexação e protegida contra troca de contexto durante pedidos assíncronos. Diretórios são expandidos em anexos suportados, preservando os originais e indicando itens ignorados/limites. O limite existente é de dez arquivos por pedido.

A visualização de Atividade preserva os eventos e controles existentes. Arquivos é a visualização inicial; no celular, o painel é aberto sob demanda. Os dois botões abrem ou alternam a visualização, e recolhem o painel ao clicar novamente na visualização aberta.

## Contrato

- GET `/v1/project-files?view=tree&project_id=...&root_id=...&path=...&start=1&limit=100`: diretório imediato, raízes do navegador e indicação de paginação.
- POST `/v1/project-files/attach?project_id=...&backend=...&model=...&max_files=...`: seleção relativa à raiz do navegador; retorna anexos com `file_id` e itens ignorados com motivo. Reaproveita o envio da pergunta por `file_ids`.

## Validação

- Backend: `tests/test_project_browser.py` — 7 aprovados, incluindo autenticação, anexação fora do projeto, caminhos canônicos, permissões, limites e encaminhamento do modelo.
- Interface: `tests/harness-files-panel.spec.cjs` passou com APIs simuladas para seleção múltipla, expansão, alternância, arraste e envio.
- Runtime: reinício apenas após ociosidade confirmada, stop/start HTTP 200. Árvore global HTTP 200 com Sistema, Pasta pessoal e atalhos de mídias presentes.
- Anexação real de arquivo sintético em `/tmp`: HTTP 200, um anexo, nenhum item ignorado, original preservado. Não foi executado modelo.
- Revisão mobile concluída: controles Arquivos/Atividade agrupados à direita; drawer inicia exatamente abaixo da barra e termina na borda direita. Teste focado passou após ajuste. Árvore real carregou sem erro.

Sem marco Git.

Revisão de interface: checkboxes e Anexar seleção removidos. A seleção visual usa clique/Ctrl/Shift e arraste para o prompt. Abaixo de Arquivos: "arraste os arquivos ou pastas para o chat para utilizá-los". O seletor de acesso sincroniza ícone, nome e tooltip com o modo ativo; descrições menores explicam cada modo.
