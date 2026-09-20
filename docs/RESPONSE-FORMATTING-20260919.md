# Formatação das respostas

Implementada no Tail Harness em 19/09/2026. Respostas antes exibidas como texto literal agora renderizam Markdown no streaming, resultado final e histórico. Títulos, tabelas, negrito, itálico, listas, citações, links e código recebem apresentação própria. JSON objeto/array puro e blocos json em Markdown são indentados; conteúdo inválido permanece visível. Copiar resposta conserva a fonte original.

Preservados os balões e detalhes laterais do Tail Harness, caminhos de erro, aprovações e eventos Maestro. Mensagens do usuário e raciocínio permanecem literais. O parser markdown-it 14.1.0 é servido localmente, com HTML desativado, links perigosos rejeitados e imagens suprimidas. Bundle, proveniência e licença MIT estão em agent_service/vendor/ e incluídos no pacote. Referência: https://github.com/markdown-it/markdown-it/blob/14.1.0/README.md

## Validação executada

- tests/response-format.spec.cjs: aprovado contra os arquivos do serviço ativo em 127.0.0.1:8095, com API de respostas simulada. Exercita streaming fragmentado, histórico/resultado, tabelas/listas/citações, JSON puro/cercado/misto/objeto/inválido, cópia bruta, usuário literal, HTML/links perigosos/imagens e largura 390/1280 nos temas reais Violeta & Bordô e Ametista.
- `.venv/bin/python -m unittest discover -s tests -p test_response_assets.py -v`: 1 teste aprovado. Verifica rota Starlette real, ordem dos scripts, tipo JavaScript e política de conteúdo.
- `node --check agent_service/ui.js`: aprovado. Verificado também que bundle e licença constam nos padrões de package-data.
- Fila consultada vazia antes do reinício. Apenas o processo do painel foi parado/iniciado por /api/stop e /api/start da administração local; ambos HTTP 200. Página, biblioteca e ui.js do serviço retornaram HTTP 200; ui.js servido contém renderAnswer.
- Sem inferência real, benchmark, suíte completa, commit, push, merge, tag ou criação de branch nesta feature.

Os testes do navegador podem ser executados isoladamente com PLAYWRIGHT_MODULE apontando para a instalação local de Playwright e HARNESS_URL=http://127.0.0.1:8095. Sem HARNESS_URL, o teste serve os arquivos do checkout por interceptação local. Nenhuma fonte do local-llm-service é necessária para a execução dos testes ou do renderizador.
