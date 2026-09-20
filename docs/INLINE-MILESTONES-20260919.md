# Etapas recolhidas na conversa

Cada resposta tem um grupo recolhido de etapas. Ao expandir, aparecem somente títulos de pensamento, ferramentas e marcos, com indicação de conclusão ou falha. Histórico recupera os títulos sob demanda. Eventos repetidos são deduplicados e término de ferramenta atualiza sua linha pelo identificador. Resposta e conclusão continuam no corpo da conversa.

O painel direito concentra estado e marcos gerais. Entradas, saídas, métricas brutas e texto bruto de pensamento não aparecem nesses grupos. Os registros originais permanecem preservados. Metadados aditivos nos adaptadores permitem mostrar apenas o nome do executável; quando indisponível, o título permanece genérico, sem tentar reconstruir comandos ou argumentos.

## Validação

- `tests/harness-side-details.spec.cjs`: histórico, execução ao vivo, expansão, deduplicação, ausência de conteúdo bruto, painel lateral e celular.
- `tests/response-format.spec.cjs`: Markdown, streaming, JSON, cópia e segurança de renderização.
- `tests/harness-layout.spec.cjs`: seis temas, desktop, notebook, celular, escala, teclado e seletores.
- `tests/harness-ux.spec.cjs`: 12 cenários; `tests/harness-model-permissions.spec.cjs`: aprovado.
- Testes direcionados de metadados e integração nativa: 11 aprovados.

Testes de navegador usam assets reais e APIs simuladas. Sem inferência, commit ou execução da suíte inteira. Alterações anteriores do checkout preservadas.
- `tests/harness-connection.spec.cjs`: aprovado; indisponibilidade, recuperação automática, bloqueio de teclado, autenticação e preservação do rascunho.
- Sintaxe JavaScript e `git diff --check` aprovados; Graphify AST atualizado.
- Aplicação reiniciada pelo administrador após confirmar estado ocioso. Stop/start e versão retornaram HTTP 200; HTML, JS e CSS servidos conferidos byte a byte com o checkout.
