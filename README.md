# Tail Harness

Painel local para descobrir, configurar e executar Codex CLI, Claude Code e modelos locais, com uma interface de conversa acessível pela Tailscale ou outra VPN. Python 3.11+, licença MIT, versão 0.2.0.

## Instalar e iniciar automaticamente (Linux/systemd)

Repositório: `<repository-url>`.

```sh
git clone <repository-url>
cd tail-harness
./install.sh --boot
```

O instalador `.sh` cria um ambiente Python privado em `~/.local/share/tail-harness/venv`, instala a distribuição e dependências, executa a suíte Python e registra serviço e atalho. Requer Python 3.11+ com venv/pip, systemd do usuário e acesso à internet para dependências. Não usa sudo, não instala CLIs de terceiros e não autoriza contas automaticamente.

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

Para instalar um wheel, use um ambiente virtual e `pip install tail_harness-0.2.0-py3-none-any.whl`, seguido de `tail-harness`. O comando `tail-harness-install --boot` registra serviço e atalho para esse ambiente. A distribuição contém os arquivos da interface; não depende de manter o checkout original. Não há publicação no PyPI nesta versão.

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
