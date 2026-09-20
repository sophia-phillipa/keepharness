# Instalação local portátil

Este fluxo suporta servidores Linux e mantém runtime, modelos e chave dentro do projeto, em caminhos ignorados pelo Git:

```text
local-ai/runtime/llama-b11003/
local-ai/models/
local-ai/config/api-key
```

Ele não instala dependências do sistema, não baixa pesos junto do runtime e não inicia o servidor durante a instalação.

## 0. Instalar o aplicativo

Na raiz do checkout execute `./setup.sh` para criar `.venv` e instalar as dependências Python. Em Linux/systemd, `./install.sh` instala também o serviço administrativo, sem executar a suíte de testes. Os comandos de módulos abaixo usam `.venv/bin/python`; se usou `install.sh`, use `~/.local/share/tail-harness/venv/bin/python`.

## 1. Compilar llama.cpp

Tenha `python3`, `git`, compilador C/C++ e `cmake` já instalados. Compile em cada servidor Linux: um binário Linux construído em um computador não é um pacote universal para os demais servidores. Outros sistemas são recusados explicitamente pelo instalador atual.

Na raiz do projeto, a opção CPU mais portátil é:

```sh
python3 control/install_runtime.py --root . --backend cpu
```

Em uma máquina Linux que já tenha SDK Vulkan:

```sh
python3 control/install_runtime.py --root . --backend vulkan
```

Em uma máquina que já tenha toolkit CUDA:

```sh
python3 control/install_runtime.py --root . --backend cuda
```

O instalador clona a fonte oficial `ggml-org/llama.cpp`, usa a tag `b11003`, verifica o commit `7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19`, compila com CMake e executa somente `llama-server --help`. Se o runtime já existir, ele recusa sobrescrevê-lo.

A configuração segue a documentação oficial do llama.cpp: [build CPU, Vulkan e CUDA](https://github.com/ggml-org/llama.cpp/blob/b11003/docs/build.md).

## 2. Baixar o modelo verificado

O downloader existente usa revisões e SHA-256 fixados. Para o Qwen3.6 UD-Q3_K_M do catálogo, com destino interno:

```sh
.venv/bin/python -m control.download_model qwen36 local-ai/models
```

Para o outro modelo cadastrado:

```sh
.venv/bin/python -m control.download_model gemma4 local-ai/models
```

O arquivo é escrito como temporário, só recebe o nome final após conferir SHA-256 e nunca substitui um modelo já existente.

## 3. Iniciar o servidor

O perfil CPU genérico evita assumir GPU e serve como ponto de partida para outro servidor:

```sh
.venv/bin/python -m control.start_local \
  --root . \
  --profile profiles/local-cpu-profile.json \
  --port 8091 \
  --alias qwen-local \
  --key-file local-ai/config/api-key
```

O perfil `profiles/qwen-author-profile.json` é uma sugestão pessoal da autora para Vulkan, com projetor multimodal e afinidade de CPU específicos. Ele é distribuído como exemplo autorizado, sem credenciais. Revise seus parâmetros e informe seu caminho com `--profile`; não é uma configuração universal.

Para apenas conferir o comando sem iniciar o runtime nem criar a chave, acrescente `--check`.

## Última validação

Em 19/09/2026, a instalação CPU foi concluída em `local-ai/portable-check` com raiz vazia e `--jobs 2`. Foi usado CMake 4.4.3 privado em `local-ai/build-tools`, sem instalação de dependência do sistema. A fonte oficial `b11003` foi conferida no commit `7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19`; o destino final recebeu `llama-server`, bibliotecas, `LICENSE` e `runtime.json`. O `RUNPATH` final é `$ORIGIN` e `llama-server --help` passou depois da publicação. Não foram baixados pesos nem inicializada GPU.


## Instalação a partir de wheel

O wheel inclui os perfis em `share/tail-harness/profiles` no prefixo do ambiente Python. Copie o perfil escolhido para uma pasta `profiles/` em sua raiz de dados e passe essa raiz por `--root` ao instalador/launcher. Para o painel instalado por wheel, configure `TAIL_HARNESS_ROOT` apontando à mesma raiz: runtime, downloads e chave usarão `<raiz>/local-ai`. O pacote não carrega a configuração privada nem os pesos deste servidor.

O downloader padrão de texto `qwen36` baixa somente os pesos UD-Q3_K_M. Para imagens, configure explicitamente um projetor multimodal compatível em um perfil local; o perfil CPU distribuído não inclui projetor.
