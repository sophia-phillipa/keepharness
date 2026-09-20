# Menu e composer compactos

Ajuste solicitado: botão de engrenagem sem rótulo visual, estado de conexão ao lado e versão no topo do menu com prefixo `Release:` e separador. Menus devem compartilhar proporções e aparência; somente o modo Acesso total usa a cor de destaque do tema. O texto promocional e as sugestões iniciais saem da área de conversa; a seção sem projeto chama-se Conversas. O composer fica mais estreito, centralizado e com tipografia menor.

O campo Tarefa opcional sai da interface. Cada conversa ganha um menu com renomeação persistente e exclusão. Renomear modifica o título de apresentação, preservando a primeira mensagem. O rodapé deixa de repetir os estados rotineiros; a animação acompanha o fim da resposta durante a geração e desaparece ao terminar. Erros e avisos acionáveis permanecem visíveis.

## Referência consultada

[Material Design — Menus](https://m1.material.io/components/menus.html), consultado em 19/09/2026: recomenda 15 px para menus desktop comuns e permite 13 px em interfaces densas; menus devem respeitar as bordas da janela e permitir rolagem quando necessário. Essa é uma referência de interface desktop, não uma norma específica de harness. A escala de 13–14 px orienta os controles; dimensões da caixa são decisões de layout deste aplicativo.

## Validação

- Backend: `.venv/bin/python -m pytest -q tests/test_conversations.py` — 6 aprovados, incluindo validação de título, autorização e preservação do prompt.
- Servidor local reiniciado após confirmação de ociosidade: stop/start HTTP 200; listagem de conversas e versão HTTP 200.
- `tests/harness-layout.spec.cjs` passou após correções: seis temas, desktop/mobile, zoom 200%, nomes longos, menus e renomeação com API simulada.
- Capturas do menu principal conferidas visualmente; ícones sem moldura e itens alinhados. Os três arquivos HTML/JS/CSS entregues pelo servidor responderam HTTP 200 e coincidiram com os arquivos do checkout.

- `tests/harness-motion.spec.cjs` passou: animação no último parágrafo durante resposta/pensamento, remoção ao concluir, ausência de estado rotineiro no rodapé, erro preservado e menu mobile com margem de 12 px.

Não envolve nova versão ou marco Git.
