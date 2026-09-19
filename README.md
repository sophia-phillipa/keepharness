# Tail Harness

Painel local para descobrir, configurar e executar Codex CLI, Claude Code e modelos locais, com uma interface de conversa acessível pela Tailscale ou outra VPN. Python 3.11+, licença MIT, versão 0.4.0.

## Estado persistente

A instalação pelo checkout usa um vínculo editável: o serviço executa o código desta pasta, sem uma segunda cópia em site-packages. Mantenha o checkout neste caminho e reinicie o serviço após alterações de código Python. Wheels continuam sendo distribuições independentes e precisam de atualização explícita.

O estado de produção fica em `~/.local/share/tail-harness`: `settings.json`, `local-profiles.json`, `runs/jobs.sqlite3` e anexos. Prévias com `--state` em `/tmp` são descartáveis e não substituem esse estado. Antes de encerrar uma prévia, exporte e importe suas configurações no painel permanente; migrar apenas o código ou o banco não transfere as permissões dos modelos.

## Instalar e iniciar automaticamente (Linux/systemd)

Repositório: `<repository-url>`.

```sh
git clone <repository-url>
cd tail-harness
./install.sh --boot
```

O instalador `.sh` cria um ambiente Python privado em `~/.local/share/tail-harness/venv`, vincula a instalação ao código deste checkout e instala as dependências, executa a suíte Python e registra serviço e atalho. Requer Python 3.11+ com venv/pip, systemd do usuário e acesso à internet para dependências. Não usa sudo, não instala CLIs de terceiros e não autoriza contas automaticamente.

Abra **Tail Harness** no menu de aplicativos. O atalho inicia o serviço se necessário e abre o navegador. `--boot` habilita linger para o serviço subir mesmo antes do login; se o sistema exigir autenticação administrativa, o instalador mostra a falha, sem declarar essa etapa concluída. Sem `--boot`, o serviço sobe ao iniciar a sessão do usuário.

```sh
systemctl --user status tail-harness
systemctl --user restart tail-harness
journalctl --user -u tail-harness -n 50
# Desativar somente este serviço:
systemctl --user disable --now tail-harness
```

Ao reiniciar, a administração volta automaticamente. Um harness iniciado pelo painel é retomado com as escolhas salvas; clicar em **Parar harness** desativa essa retomada. Se um CLI, login ou modelo estiver indisponível, o painel continua acessível e mostra a falha. Pesos locais não são carregados silenciosamente e permissões não são ampliadas no reinício.

Para desenvolvimento ou execução manual:

```sh
./setup.sh
./start.sh
```

Para instalar um wheel, use um ambiente virtual e `pip install tail_harness-0.4.0-py3-none-any.whl`, seguido de `tail-harness`. O comando `tail-harness-install --boot` registra serviço e atalho para esse ambiente. A distribuição contém os arquivos da interface; não depende de manter o checkout original. Não há publicação no PyPI nesta versão.

Abra **http://127.0.0.1:8094/** no computador servidor. O inventário é somente leitura: nenhum serviço, projeto, upload, instalação ou compartilhamento é habilitado automaticamente. Para apenas verificar a máquina: `tail-harness --scan` (ou `.venv/bin/python -m control --scan` no checkout).

1. Verifique os serviços encontrados; use **Entrar** para iniciar o login oficial.
2. Em **Operações**, abra o link de autorização emitido pelo CLI e conclua no navegador deste computador. Depois clique em **Verificar conta**.
3. Selecione modelos, modo de execução, permissões e integrações por serviço.
4. Adicione somente os projetos que deseja disponibilizar, ou use conversas sem projeto.
5. Salve e inicie o harness. O endereço padrão de conversa é **http://127.0.0.1:8095/**.
6. Para Tailscale, cadastre as identidades autorizadas e habilite o compartilhamento. Para outra VPN, informe o IP privado dessa interface; o painel gera uma chave de acesso, mostrada mediante clique.

A administração fica somente no loopback. A VPN transporta a interface; Codex/Claude continuam usando os respectivos provedores na nuvem. O backend **local** usa Codex como agente e llama.cpp/Ollama para a inferência, sem enviar a inferência à OpenAI. Internet, hooks e conectores habilitados podem produzir suas próprias comunicações externas.

## Modelos locais

O inventário encontra processos `llama-server` do usuário em Linux e consulta os modelos do servidor, incluindo autenticação por arquivo indicada pelo processo. Também consulta Ollama em `127.0.0.1:11434`; modelos identificados como cloud são excluídos.

Em **Modelos no seu computador**, encontre arquivos GGUF na pasta do servidor atual, na biblioteca do painel ou numa pasta indicada. Servidores existentes são reutilizados. Para iniciar um GGUF, informe o executável `llama-server`, selecione o arquivo e as camadas GPU. O padrão é CPU; não se alteram clocks, potência ou ventoinhas. Um servidor llama.cpp já ativo impede iniciar outro pelo painel. Esta proteção não controla trabalhos iniciados fora do painel.

O servidor gerenciado usa porta 8096, chave privada, contexto de 64k e um slot; seu ciclo de vida acompanha a administração. Cancele sua operação para encerrá-lo. Após carregar, atualize o inventário. Não se substitui nem encerra um servidor existente.

O catálogo permite download explícito de Gemma 4 E4B Q4_K_M e Qwen3.6 35B A3B UD-Q3_K_M. GGUFs são baixados de revisões fixas e verificados por SHA-256; a alternativa Ollama usa `ollama pull`. Os pesos têm suas próprias licenças. Arquivo instalado não significa modelo carregado, compatível ou rápido na máquina. A execução pelo agente exige servidor compatível com **Responses API** e modelo apto a ferramentas.

## Conectores e plugins

Cadastre um servidor MCP HTTPS ou um comando stdio em JSON na interface. Escolha Codex ou Claude, execute a operação e acompanhe o resultado. A ação **Autorizar** inicia o login MCP oficial; links OAuth aparecem em Operações. Selecione depois a integração no cartão de cada serviço. O backend local compartilha o ecossistema MCP/plugins do Codex.

Gmail, Drive e GitLab podem ser conectados por servidores MCP/plugins compatíveis com o CLI e com as permissões da conta. O painel não inventa endpoints, credenciais ou permissões OAuth. Integrações exclusivas dos aplicativos web não são automaticamente portáveis para os CLIs. Esta versão cadastra os transportes nativos; a configuração de cabeçalhos HTTP secretos específicos de um fornecedor ainda deve ser feita no CLI. Os apps de conta OpenAI não selecionados são desabilitados no executor; use MCP explícito.

A instalação/remoção de integrações modifica o perfil do CLI deste usuário. Operações de autenticação podem abrir o navegador automaticamente. O painel também mostra o link; fluxos que exigem um terminal interativo não são emulados. Nenhuma senha de conta é pedida pelo painel.

## Conversas e ferramentas

O harness oferece streaming SSE persistido, cancelamento, histórico, continuidade da sessão, modelos/esforços disponíveis, atividade das ferramentas e aprovações interativas. Eventos de raciocínio e compactação são apresentados quando o provedor os fornece; não se fabrica raciocínio interno. Cota Codex é consultada antes/depois das execuções; Claude não fornece essa cota neste adaptador. Tokens/contexto são apresentados quando enviados pelo CLI.

Há dois modos:

- **Isolado:** Linux + bubblewrap; ferramentas limitadas ao projeto, sem terminal geral ou acesso livre à internet. Alterações passam por proposta/aplicação com backup. Não usa conectores externos.
- **Nativo:** executa o CLI com ferramentas nativas, plugins escolhidos, política de sandbox e aprovações. A pasta selecionada define o projeto, mas não constitui uma prisão de leitura. Terminal, hooks e conectores têm o alcance de suas próprias permissões. A opção Internet não é um firewall para processos externos. Edições nativas acontecem diretamente no projeto.

O projeto não é uma solução multiusuário para pessoas mutuamente não confiáveis. Todos os clientes autorizados recebem a mesma política administrativa de projetos; cada um tem seu histórico e aprovações. Para separação forte de identidades e credenciais, execute instâncias sob usuários do sistema distintos.

## MCP no notebook

No Linux ou macOS, abra **Conexão / MCP** no harness e clique em **Baixar instalador MCP (.sh)**. Com Tailscale conectado, Python 3.10+, curl e Claude Code instalados, salve o arquivo em Downloads. No Mac, abra o Terminal pelo Spotlight (⌘ + Espaço → Terminal). Execute:

```sh
cd ~/Downloads
chmod +x setup-mcp.sh
./setup-mcp.sh 'https://SEU-SERVIDOR-TAILSCALE'
```

Use a URL real mostrada na janela Conexão / MCP. O `chmod +x` torna o arquivo executável; alternativamente, use `sh setup-mcp.sh 'https://SEU-SERVIDOR-TAILSCALE'`. O instalador cria um ambiente Python no diretório do usuário, baixa o bridge e cadastra o MCP no Claude Code. Confira a conexão com `/mcp`. O arquivo também está em `agent_service/setup-mcp.sh` no projeto. Para Claude Desktop, faça a configuração manual abaixo.

Instale Python e as dependências do projeto no notebook, copie o projeto limpo e configure seu cliente MCP para executar `agent_service/mcp_bridge.py`. Use caminhos absolutos adequados ao notebook:

```json
{
  "mcpServers": {
    "tail-harness": {
      "command": "/caminho/tail-harness/.venv/bin/python",
      "args": ["/caminho/tail-harness/agent_service/mcp_bridge.py"],
      "env": {"LOCAL_AGENT_URL": "http://SEU-SERVIDOR:8095"}
    }
  }
}
```

Na Tailscale com identidade autorizada, a rota encaminha a identidade. Para VPN com chave, crie `~/.config/tail-harness/client.json` contendo `{"url":"http://IP-VPN:8095","key_file":"/caminho/privado/chave"}`. Coloque a chave num arquivo privado no notebook. Nunca coloque chave em Git ou em prompts. O bridge oferece descoberta de modelos/projetos, tarefas, eventos, resultados, anexos, cancelamento e resposta a aprovações. Para continuar contexto use o último `job_id` como `parent_job_id`.

## Delegação pelo MCP: documentos, pastas e projetos

O conector apresenta um guia ao cliente durante a conexão; `workflow_guide` também permite consultá-lo. Ele diferencia o computador cliente (Mac/Linux) do servidor e informa os recursos realmente configurados. Não presume acesso aos conectores nem aos arquivos do outro computador.

### Relatórios com documentos da empresa

Peça, por exemplo: “Use os documentos desta pasta para cruzar as decisões das reuniões com os e-mails e preparar um relatório com fontes, delegando o processamento ao harness”.

1. O cliente obtém transcrições, arquivos, e-mails e mensagens pelos conectores que já possui. Salve os documentos e suas referências (link, data, identificador) em uma pasta autorizada.
2. `upload_path` transfere o arquivo ou pasta diretamente do disco do cliente para um projeto autorizado do servidor, sem colocar os bytes na conversa. Retorna um `workspace_id` reutilizável.
3. `inspect_files` lista arquivos, procura nomes/textos ou lê trechos numerados. PDFs e DOCX recebem cópias de texto para pesquisa em `_harness_sources`; falhas de extração são informadas (até 200 documentos por envio, 2 MiB de texto por documento). Imagens, áudio e PDFs digitalizados sem texto não recebem OCR/transcrição automática.
4. `submit_job` recebe a tarefa e o `workspace_id`. O processamento usa as fontes no servidor; o cliente recebe resultados compactos. Para conteúdo disponível apenas como texto de um conector, `upload_text` continua disponível.
5. `download_workspace` salva um ZIP novo no computador cliente. Não sobrescreve arquivos existentes nem aplica mudanças automaticamente ao projeto original.

A redução de contexto ocorre no trabalho delegado e no retorno compacto. Não recupera tokens que o Claude já consumiu ao ler respostas dos conectores. Economia líquida ainda precisa ser medida. Gmail, Drive e Slack precisam estar autenticados no computador que fará a consulta. Integrações do cliente não são transferidas; não copie credenciais. Para busca direta pelo servidor, habilite o conector no executor e delegue a consulta. O inventário informa configuração, não comprova autenticação ativa.

### Execução automática e Maestro opcional

O padrão MCP é `backend="auto"`. Quando Codex está habilitado para o projeto e **Usar Maestro por padrão** está ativo, o Codex planeja de uma a seis etapas e escolhe os executores, modelos e esforços dentre os habilitados naquela instalação. O modelo local participa quando configurado e autorizado para o projeto. A fila executa uma etapa de cada vez.

Sem Maestro elegível, o servidor usa **Executor padrão sem Maestro** ou o primeiro executor elegível habilitado. Nenhum modelo específico é obrigatório para o aplicativo: um serviço ausente nunca é anunciado como disponível. Os requisitos do executor continuam valendo (por exemplo, o adaptador local atual utiliza o CLI Codex como agente). É possível selecionar explicitamente `backend`, `model` e `effort` para execução direta.

O painel do Codex permite acrescentar **Instruções do Maestro nesta instalação**, sem embutir regras pessoais na distribuição. O plano e as saídas de cada etapa ficam registrados em `runs/maestro/<job_id>/`; o resultado final informa os modelos/esforços e métricas disponíveis. Para pastas enviadas, a resposta final também é salva em `_harness_results/<job_id>/answer.md`. Planos inválidos e etapas incompletas são reportados como falhas. Disponibilidade declarada do modelo não garante cota ou funcionamento do provedor no momento da execução.

### Pastas, código e serviços

`upload_path` aceita qualquer linguagem como arquivo, preserva a hierarquia e não executa código no envio. Análise textual independe da linguagem; executar/testar exige runtime e permissões no servidor. A cópia original do envio é preservada como `original.zip`. A pasta de trabalho pode ser modificada conforme as permissões existentes. Limites: 10 mil entradas, 200 MiB por pasta descompactada e armazenamento total limitado. Metadados Git, dependências, links simbólicos, `.env`, chaves e pesos de modelos são excluídos ou recusados; confira a lista de exclusões retornada.

Na configuração de cada projeto, **Serviços deste projeto** cadastra unidades `systemd --user`, como `meu-app.service`. `project_services` consulta o estado ou inicia/para/reinicia somente unidades cadastradas, com permissão de terminal de um executor nativo e pedido explícito para a alteração. As ações são registradas. Esse controle requer servidor com systemd; não administra serviços do Mac cliente. Tarefas executadas pelos agentes continuam sujeitas às permissões e aprovações de cada executor.

`job_events` retorna progresso compacto por padrão, omitindo saídas intermediárias e raciocínio. Use `compact=false` para diagnóstico detalhado. O resultado final é obtido com `job_status`/`get_artifact`. `job_status` também é compacto por padrão: não repete o pedido original e limita a prévia da resposta a 12 mil caracteres, sinalizando cortes; o arquivo completo continua disponível.

## Desenvolvimento e distribuição

```sh
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/python -m build
# Teste de navegador com servidor temporário e estado isolado:
PYTHON="$PWD/.venv/bin/python" PLAYWRIGHT_MODULE=/caminho/playwright ./scripts/test-ui.sh
```

Estado privado: `~/.local/share/tail-harness` (ou `--state`). Credenciais permanecem nos perfis locais; logs, conversas, anexos, pesos e configuração pessoal não fazem parte da distribuição. A exclusão de conversas na interface é lógica, não uma garantia de apagamento físico. Backups e retenção do diretório de estado são responsabilidade de quem administra.

Consulte [auditoria da extração](docs/AUDIT.md) e [validação e limites](docs/VALIDATION.md). Referência de produto: [T3 Code](https://github.com/pingdotgg/t3code), apresentado no [artigo indicado](https://www.crazystack.com.br/blog/it39s-finally-here/). Implementação própria; não incorpora código ou recursos gráficos do T3 e não alega equivalência de recursos.

A suíte cobre políticas de acesso, autenticação, protocolos e aprovações, descoberta local, integridade de download, instalação, arquivos empacotados e retomada após falha. O teste de navegador usa fixtures para não consumir contas nem baixar modelos. A configuração GitLab CI executa testes Python, UI Chromium e construção de wheel/sdist; os artefatos ficam no job de pacote quando o pipeline passa. Autenticação de terceiros e reinício físico da máquina não são simulados como prova de funcionamento real.

O projeto GitLab foi criado privado. Para compartilhar por GitLab, conceda acesso ao destinatário nas configurações do projeto. A primeira execução remota de CI ficou bloqueada por cota; a suíte foi validada localmente.

## Assistente e configuração portátil

O dashboard mostra somente provedores cadastrados, com ações de edição e exclusão. **Adicionar provedor** abre um assistente de três etapas: **Serviço e modelos → Permissões e projetos → Revisão**. Permissões detalhadas, conectores, instalação de modelos, portas e outras VPNs ficam em opções expansíveis. O botão **Usar configuração atual** captura os parâmetros de desempenho do llama.cpp ativo sem reiniciá-lo e grava `local-profile.json` no diretório privado de estado (modo 0600). Esse perfil preserva GPU, MoE na CPU, threads e afinidade para futuras inicializações do mesmo modelo pelo painel; não copia chaves ou argumentos arbitrários.

No dashboard, em **Acesso remoto e configuração → Exportar ou importar configuração**, exporte as escolhas salvas ou selecione um JSON para conferir uma prévia e aplicar. Credenciais, tokens e chaves VPN são excluídos. O arquivo ainda contém caminhos locais e identidades permitidas: trate-o como privado. A importação valida caminhos e integrações existentes, não inicia serviços e não pode substituir escolhas durante uma execução. Sem perfil no arquivo, o perfil local atual é preservado.

Para testar uma instalação vazia sem modificar o serviço principal:

```sh
TAIL_HARNESS_VENV="$(mktemp -d)/venv" ./install.sh --check-only
```

Esse fluxo instala todas as dependências em outro ambiente, roda a suíte e inicia o pacote fora do checkout com estado temporário e porta livre. Não reinicia modelos nem configura Tailscale. A pasta do ambiente de teste pode ser removida depois.

## DeepSeek com sua própria chave (BYOK)

Em **Adicionar provedor → DeepSeek**, informe o token da plataforma DeepSeek e clique em **Salvar chave e verificar**. O painel consulta modelos e saldo disponíveis; depois selecione os modelos e permissões e conclua o assistente. O token fica no arquivo privado `deepseek.key` do estado local (0600), não é retornado pela API administrativa e não entra na exportação. Excluir esse provedor do dashboard também apaga a chave gerenciada pelo painel; a conta externa não é alterada.

O agente é o Codex instalado, com provider DeepSeek temporário por processo; não altera seu perfil global do Codex. A inferência usa a conta/créditos DeepSeek. Ferramentas, sessões, aprovações e esforço seguem o executor nativo. Pesquisa web interna do Codex fica desativada nesse provedor; rede para terminal e MCP segue as permissões selecionadas. A implementação segue a [integração oficial DeepSeek/Codex](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/). Modelos são descobertos pela API, não por uma lista fixa. A execução autenticada depende de cadastrar uma chave válida e ter saldo.

### Usabilidade e regressões

Veja [o relatório das oito rodadas](docs/UX-GAUNTLET.md) para cenários, testes reais com modelo local, desempenho medido e limites de cobertura. `scripts/test-ui.sh` executa as regressões das duas interfaces em servidores temporários.

## Specifications and use cases

The [project dossier](dossie/README.md) contains specifications, use cases, and research. All dossier documents are maintained in English.
