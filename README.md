# AI Projects local

Guia do ambiente local de IA executado no Ubuntu do WSL2 com Docker Compose.

> **Pasta do projeto:** `~/ai-projects/ai-projects`
> Abra um terminal Ubuntu/WSL e entre nela antes de executar os comandos deste guia.

## Como usar o pipeline

O CLI do agente roda no Conda do WSL. Whisper e Ollama são chamados por HTTP
local; ComfyUI é chamado por `docker exec`. O backend e os CLIs iniciam um
encaminhador local quando as portas não estão acessíveis no host. Os listeners
ficam vinculados a `127.0.0.1`, restringindo conexões de entrada a este
computador. O processamento é local; baixar imagens Docker, modelos ou
atualizações ainda pode exigir acesso à internet. O pipeline WAV → transcrição
→ resposta → imagem foi validado com o WAV sintético de exemplo.

O backend de conversa e a interface web também rodam no WSL, sem container ou
dependência web adicional. O backend escuta apenas em `127.0.0.1:8765`; a página
é servida pelo próprio backend. Ollama, Whisper, ComfyUI e Piper são usados
por adaptadores locais, sem chamadas a APIs externas.

O Compose desativa o autostart do Syncthing e as atualizações automáticas do
container ComfyUI. O ComfyUI Manager é iniciado em modo `cli-only` para impedir
as consultas de cache remotas feitas ao carregar seu painel; por isso, o painel
gráfico do Manager (instalação/consulta de custom nodes e modelos) fica
indisponível. A geração de imagens pelo ComfyUI e pelo agente continua disponível.
Essas medidas reduzem tráfego externo conhecido. A rede do Compose é interna
por padrão, bloqueando a saída de rede dos containers. Neste ambiente Docker,
as portas dos containers nessa rede interna não ficam publicadas no host; por
isso, os pontos de entrada do agente iniciam um encaminhador TCP no host,
vinculado somente a `127.0.0.1`. Ele encaminha às APIs locais sem conectar os
containers à internet. Para acesso manual às portas enquanto o backend não está
rodando, use `./scripts/forward-local-services.sh`.

Use `AI_OFFLINE_MODE=false` apenas quando um container precisar baixar um
modelo ou atualização. Antes de recriar os containers, encerre qualquer backend
ou CLI que mantenha encaminhamentos ativos (`Ctrl+C` ou
`systemctl --user stop ai-agent.service`) para liberar as portas locais. Depois
recrie a rede do Compose:

```bash
docker compose down
AI_OFFLINE_MODE=false docker compose up -d ollama whisper stable-diffusion
# Após o download ou atualização explícitos:
docker compose down
AI_OFFLINE_MODE=true docker compose up -d ollama whisper stable-diffusion
```

`docker compose down` preserva os dados locais; não acrescente `--volumes`.
Enquanto `AI_OFFLINE_MODE=false`, todos os serviços do Compose podem acessar a
rede externa. Volte ao modo offline assim que concluir a operação e reinicie o
backend para que ele recrie os encaminhamentos.

Inicie os serviços:

```bash
cd ~/ai-projects/ai-projects
docker compose up -d ollama whisper stable-diffusion
docker compose ps
```

Com `AI_OFFLINE_MODE=true`, é esperado que `docker compose ps` não mostre
portas publicadas. `./scripts/web.sh`, `./scripts/chat.sh` e
`./scripts/agent.sh` iniciam encaminhamentos apenas para os serviços que
precisarem deles e os removem ao encerrar. Para acesso manual às APIs enquanto
o agente não estiver rodando, abra outro terminal e execute:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda run --no-capture-output -n ai-agents python -m agents.port_forwarder
```

Encerre esse encaminhador manual com `Ctrl+C`.

O launcher executa o agente dentro do ambiente Conda `ai-agents`; não é
necessário ativar o ambiente manualmente. O primeiro comando testa áudio →
transcrição → resposta; o segundo também gera uma imagem:

```bash
./scripts/agent.sh outputs/audio/amostra_ptbr.wav --no-image
./scripts/agent.sh outputs/audio/amostra_ptbr.wav
```

Para conversar pela interface web local, inicie-a em outro terminal depois dos
containers:

```bash
./scripts/web.sh
```

Abra **http://localhost:8765** no navegador. Encerre o modo interativo com
`Ctrl+C`. Para uso contínuo, configure opcionalmente o serviço de usuário
systemd descrito na seção [Backend e interface de conversa locais](#backend-e-interface-de-conversa-locais).
Para testar o mesmo núcleo no terminal, use `./scripts/chat.sh`; acrescente
`--task code` para o perfil de programação ou `--message "..."` para uma
pergunta única. A interface permite selecionar qualquer modelo que o Ollama já tenha
instalado, sem fazer downloads. Acrescente `--image` ou `--video` ao chat CLI
para testar a geração local pelo ComfyUI.

Se o ambiente ainda não existir, crie-o usando Conda-forge:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda env create -f agents/environment.yml
```

As transcrições são salvas em `outputs/audio/transcriptions/`, respostas e
metadados em `outputs/text/`, imagens em `outputs/images/` e vídeos em
`outputs/videos/`. Use `--no-image` no pipeline de áudio para não executar a
geração de imagem.

Para usar o ComfyUI manualmente no navegador do Windows, autentique-se em
`http://localhost:1111` e depois abra `http://localhost:8188`.
`http://localhost:18188` também encaminha ao ComfyUI através do proxy Caddy
autenticado. Nenhuma dessas portas fica publicada fora do computador.

Confira os arquivos e pare os serviços quando terminar:

```bash
find outputs -type f -printf '%p\n' | sort
docker compose stop ollama whisper stable-diffusion
```


## Estrutura

```text
ai-projects/
├── docker-compose.yml
├── .gitignore             # exclui credenciais, modelos e resultados locais
├── .env                 # credenciais do portal ComfyUI; não compartilhar
├── agents/
│   ├── agent.py         # CLI compatível do pipeline WAV existente
│   ├── core.py          # orquestração de chat, voz, imagens e vídeos
│   ├── jobs.py          # fila serial e estado das gerações de mídia
│   ├── port_forwarder.py # encaminhamentos TCP locais para a rede interna Docker
│   ├── settings.py      # configuração local compartilhada
│   ├── chat_cli.py      # CLI do núcleo de conversa
│   ├── server.py        # API e servidor web de loopback
│   ├── adapters/        # integrações Ollama, Whisper, ComfyUI e eSpeak
│   ├── web/             # cliente web local sem recursos externos
│   └── environment.yml  # ambiente Conda isolado do agente
├── scripts/
│   ├── agent.sh         # executa o pipeline WAV no ambiente Conda
│   ├── chat.sh          # conversa no terminal
│   └── web.sh           # inicia API e interface local
├── tests/               # testes unitários, HTTP e de migração do histórico
├── ollama/              # modelos e dados persistentes do Ollama
├── stable-diffusion/    # workspace e checkpoint persistente do ComfyUI
│   ├── config/          # marcadores de configuração local do ComfyUI Manager
│   │   └── ComfyUI-Manager/
│   │       └── .enable-cli-only-mode
│   └── models/
│       └── checkpoints/
├── stable-video/        # workflow API do piloto Wan2.1 T2V-1.3B
├── whisper/             # API faster-whisper e cache persistente do modelo
└── outputs/
    ├── audio/           # WAVs de teste e transcrições Whisper
    ├── text/            # respostas textuais do agente
    ├── images/          # imagens geradas pelo ComfyUI
    └── videos/          # WebM gerados localmente
```

Os dados dos modelos ficam nos diretórios do projeto e podem ocupar bastante
espaço. Não apague `ollama/` ou `stable-diffusion/` se quiser preservar modelos
e arquivos baixados. ComfyUI persiste imagens em `outputs/images/` e Whisper
persiste transcrições em `outputs/audio/transcriptions/`. O agente grava
respostas e metadados em `outputs/text/`; vídeos vão para `outputs/videos/`. O fluxo completo foi validado com um
WAV sintético; a precisão ainda precisa ser avaliada com gravação de voz natural.

## Pré-requisitos e credenciais

- Docker Engine funcionando no Ubuntu/WSL2.
- Docker Compose v2 (`docker compose version`).
- Driver NVIDIA para WSL instalado no Windows para aceleração por GPU.
- O arquivo `.env` fica **na mesma pasta** que `docker-compose.yml`. O Compose
  usa `COMFYUI_WEB_USER` e `COMFYUI_WEB_PASSWORD` para o login do ComfyUI.
- Não publique nem envie o `.env` para repositórios. Não cole suas credenciais
  em logs ou mensagens.

## Iniciar e administrar os serviços

Confira a configuração e veja o estado dos containers:

```bash
docker compose config --quiet
docker compose ps
```

Inicie somente os serviços já usados:

```bash
docker compose up -d ollama whisper stable-diffusion
```

Para ver os logs:

```bash
docker compose logs --tail=80 ollama
docker compose logs --tail=80 stable-diffusion
docker compose logs --tail=80 whisper
```

Para parar os serviços sem remover os dados persistidos:

```bash
docker compose stop ollama whisper stable-diffusion
```

Para iniciá-los novamente:

```bash
docker compose start ollama whisper stable-diffusion
```

`docker compose down` remove os containers e a rede do Compose, mas mantém as
pastas bind-mounted do projeto. Não use `down -v` sem entender quais volumes
serão removidos.

## Ollama: LLM de texto e código

Confirme que a API local está acessível:

```bash
curl http://localhost:11434/api/tags
```

Confira somente os modelos já instalados:

```bash
docker compose exec ollama ollama list
```

Neste ambiente, os modelos locais verificados são `qwen3:4b`, `mistral:latest`
e `llama2:latest`. A aplicação não baixa modelos. O perfil de texto e o de
código usam `qwen3:4b` por padrão, mas você pode selecionar outro modelo
instalado na interface ou configurar cada perfil:

```bash
OLLAMA_TEXT_MODEL=qwen3:4b OLLAMA_CODE_MODEL=mistral:latest ./scripts/web.sh
```

O perfil seleciona instruções de atendimento, não garante que um modelo seja
especialista em código. A lista suspensa pode substituir o modelo para uma
conversa. Se o modelo configurado não estiver instalado, o backend informa o
erro e não tenta baixá-lo.

Também é possível testar a API local do Ollama diretamente:

```bash
curl http://localhost:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","prompt":"Explique o que é WSL2 em português do Brasil.","stream":false}'
```

Os modelos baixados são persistidos em `ollama/`. O modelo e o tamanho escolhido
afetam bastante a RAM/VRAM necessária; comece com um modelo pequeno antes de
baixar opções maiores.

## ComfyUI: geração de imagens

1. No navegador do Windows, abra **http://localhost:1111** e autentique-se
   usando os dados definidos no `.env`.
2. Abra **http://localhost:8188** para acessar a interface do ComfyUI. Também
   é possível usar **http://localhost:18188**; ambas as portas passam pelo proxy
   autenticado do container.
3. Carregue um workflow de texto para imagem e selecione um checkpoint compatível.
   A imagem Docker não fornece automaticamente todos os modelos de geração.
4. Selecione `v1-5-pruned-emaonly.safetensors`, carregue um workflow de texto
   para imagem e gere uma imagem pequena antes de aumentar resolução.

O serviço está configurado com `COMFYUI_ARGS=--output-directory
/workspace/outputs/images`, e essa pasta do container é montada em
`./outputs/images` no projeto. Assim, imagens novas devem permanecer nesse
diretório no WSL mesmo se o container for recriado.

Para descobrir o diretório de checkpoints utilizado pela imagem:

```bash
docker compose exec stable-diffusion sh -lc \
  'ls -lh /opt/ComfyUI/models/checkpoints'
```

Os checkpoints ficam persistidos em
`stable-diffusion/models/checkpoints/` no projeto e são montados em
`/opt/ComfyUI/models/checkpoints` no container. O checkpoint inicial é
`v1-5-pruned-emaonly.safetensors` (aprox. 4,27 GB), licença
CreativeML OpenRAIL-M. Seu SHA-256 foi conferido:
`6ce0161689b3853acaa03779ec93eafe75a02f4ced659bee03f50797806fa2fa`.
Resultados de imagem são salvos em `outputs/images/`.

## GPU

O estado `Up` confirma que o container está rodando, mas não prova que a GPU
está disponível para ele. Confira:

```bash
docker compose exec stable-diffusion nvidia-smi
```

Se esse comando falhar, registre a mensagem completa e valide primeiro o driver
NVIDIA no Windows/WSL e o suporte a GPU do Docker. Não instale versões aleatórias
do CUDA Toolkit dentro do WSL: para inferência, o driver NVIDIA do Windows e as
bibliotecas incluídas na imagem geralmente são o caminho necessário.

## Decisão e preparação para vídeo

O inventário local não contém workflow ou modelo de vídeo em `stable-video/`;
em `stable-diffusion/models/checkpoints/` só foi encontrado o checkpoint SD 1.5
usado para imagens. A GPU foi consultada durante esta avaliação: RTX 4060
Laptop, 8188 MiB totais e 5095 MiB livres naquele momento. A memória livre é
dinâmica; o backend pode descarregar Ollama, mas ComfyUI, Whisper, buffers e
resolução/frames ainda competem por VRAM.

| Candidato | O que oferece | Ajuste preliminar para esta máquina |
|---|---|---|
| **Wan2.1 T2V-1.3B** | Texto para vídeo, recomendação oficial em 480p; há workflow ComfyUI oficial. O repositório oficial estima 8,19 GB de VRAM e cita cerca de 4 min para 5 s em uma RTX 4090. Na versão repacotada para ComfyUI, os pesos 1.3B disponíveis são FP16/BF16 (2,84 GB); o encoder de texto FP8 ocupa 6,74 GB e o VAE 0,25 GB. | **Melhor candidato para o primeiro piloto T2V pela integração**, mas sem garantia de caber ou ter bom desempenho com 8 GiB. O bundle listado soma cerca de 9,83 GB em disco; a variante 1.3B FP8 não apareceu no pacote oficial consultado. O resultado de uma RTX 4090 não prevê o tempo da 4060 Laptop. Modelo informa licença Apache-2.0. |
| **LTX-Video 2B distilled FP8** | O projeto descreve 2B como mais leve em VRAM; há peso 0.9.8 distilled FP8 de 4,46 GB em disco. A tabela oficial não lista workflow ComfyUI para essa versão; um workflow 0.9.6 distilled está publicado, mas usa peso de 6,34 GB. | Alternativa de menor peso de difusão a investigar, não uma solução comprovada para 8 GiB: falta um mínimo de VRAM documentado e é necessário validar workflow/encoder e versão do ComfyUI. Conferir a licença da variante exata. |
| **Stable Video Diffusion XT** | Somente imagem para vídeo; 25 frames em 576×1024. A ficha cita cerca de 180 s em A100 80 GB e diz que há otimizações para menor memória, sem quantificar o mínimo. | Não é T2V e seu checkpoint foi estimado anteriormente em cerca de 9,6 GB em disco. O tamanho em disco não mede VRAM; permanece uma alternativa apenas se o fluxo começar por uma imagem e o teste de memória justificar. Licença Community License da Stability AI. |
| **CogVideoX-2B** | Texto/vídeo e imagem/vídeo; o projeto declara execução em placas como GTX 1080 Ti e aponta uso via código próprio. | Não é a primeira opção para a arquitetura atual: a declaração não confirma 8 GiB e o projeto não oferece aqui um fluxo ComfyUI pronto integrado ao nosso setup. |

**Resultado do piloto:** Wan2.1 T2V-1.3B gerou um WebM válido com 33 quadros
distintos, 832×480, 24 fps e 1,375 s. A execução levou 230 s; o pico observado
foi 6034 MiB usados e o mínimo livre, 1923 MiB. O encoder de texto foi
explicitamente carregado na CPU para reservar VRAM. A geração de imagem SD 1.5
também foi testada depois da atualização e continuou funcionando.

O ComfyUI v0.42.1 foi atualizado para PyTorch 2.7.0+cu128, com CUDA disponível
na RTX 4060 Laptop (8188 MiB totais; driver 617.42). Os nodes
`EmptyHunyuanLatentVideo` e `SaveWEBM` foram confirmados pela API. O workflow
reexecutável está em
[`stable-video/wan2.1_t2v_1.3B_pilot.json`](stable-video/wan2.1_t2v_1.3B_pilot.json);
a amostra está em
[`outputs/videos/wan_t2v_pilot_00001_.webm`](outputs/videos/wan_t2v_pilot_00001_.webm).
O resultado valida um clipe curto, não vídeos de vários segundos nem uso
permanente; o tempo e a memória dependem da carga concorrente na GPU.

Arquivos baixados manualmente para este piloto: `wan2.1_t2v_1.3B_fp16.safetensors`
(2.84 GB), `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (6.74 GB) e
`wan_2.1_vae.safetensors` (0.25 GB), total de aproximadamente 9.83 GB. Os pesos
reempacotados estão sob Apache-2.0; havia 859 GB livres no volume na última
verificação. Nenhuma variante 14B ou alternativa será baixada neste piloto.

Fontes primárias consultadas:
[Wan2.1](https://github.com/Wan-Video/Wan2.1),
[workflow Wan para ComfyUI](https://comfyanonymous.github.io/ComfyUI_examples/wan/),
[modelo Wan T2V-1.3B](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B),
[pesos Wan repacotados para ComfyUI](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/tree/main/split_files),
[LTX-Video](https://github.com/Lightricks/LTX-Video),
[ficha LTX-Video](https://huggingface.co/Lightricks/LTX-Video),
[Stable Video Diffusion XT](https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt)
e [CogVideoX](https://github.com/THUDM/CogVideo).

O piloto não implica aprovação para uso permanente. O próximo teste de vídeo
deve aumentar a duração gradualmente, medir novamente memória/tempo e manter
batch 1; não foram baixadas variantes 14B ou modelos alternativos.

## Backend e interface de conversa locais

O backend (`agents/server.py`) escuta apenas em `127.0.0.1:8765`; rejeita
endereços de escuta que não sejam loopback. A interface estática é servida pelo
mesmo processo, sem CDN, fontes externas, telemetria ou dependências JavaScript.
Os fluxos implementados são:

1. **Texto/código:** o núcleo valida as mensagens e o perfil, escolhe o modelo
   local configurado ou o selecionado na interface e chama o adaptador Ollama.
2. **Imagem:** pedidos explícitos como “gere uma imagem de...” entram em um fluxo
   próprio: o modelo cria um prompt visual a partir do pedido e o núcleo confirma
   a geração apenas depois que o ComfyUI retorna o arquivo. Isso evita respostas
   textuais que negam a capacidade antes de uma imagem ser produzida. A opção
   “Adicionar imagem relacionada à mensagem” continua disponível para perguntas
   normais. O núcleo descarrega o modelo Ollama da VRAM antes de chamar o
   adaptador ComfyUI com o workflow SD 1.5 de 512×512 já existente.
3. **Vídeo:** pedidos explícitos como “gere um vídeo de...” ou a opção
   “Gerar vídeo curto local” roteiam para o workflow Wan2.1. O prompt é
   preparado pelo Ollama local, que é descarregado antes da inferência. O
   ComfyUI gera 832×480, 33 frames (~1,4 s), batch 1; o WebM é salvo em
   `outputs/videos/` e persistido no histórico. A duração padrão máxima da
   chamada é 30 minutos. O custo observado foi aproximadamente 3 min 50 s e
   6 GiB de VRAM usados; uma única execução não garante estabilidade sob carga
   simultânea.
4. **Microfone/voz:** o navegador grava áudio localmente; o backend envia o
   arquivo ao Whisper local, acrescenta a transcrição à conversa e pede a
   resposta ao Ollama. A leitura usa Piper com a voz neural `pt_BR-faber-medium`,
   carregada uma vez no serviço e executada em CPU. Não usa síntese de voz do
   navegador.

A captura pelo microfone depende de permissão do navegador e de dispositivo
disponível. O teste automatizado cobre o endpoint de áudio, mas a captura física
no navegador e a qualidade da voz ainda precisam ser verificadas neste
computador. A gravação do microfone não é armazenada pelo backend; o serviço Whisper
persiste transcrição e metadados em `outputs/audio/transcriptions/`. O histórico
é separado por conversas e persistido localmente em `outputs/agent-history.sqlite3`.
Na interface, crie, selecione, renomeie e exclua conversas; a seleção atual é
lembrada no navegador. A exclusão remove as mensagens e referências da conversa,
mas não apaga os arquivos de mídia já gerados em `outputs/images/` e
`outputs/videos/`. A **Galeria de mídia** exibe imagens e vídeos vinculados às
mensagens salvas, junto ao prompt, à data e à conversa; arquivos sem referência
no histórico não são incluídos.
O painel **Diagnóstico local** consulta Ollama, Whisper, síntese de voz e
ComfyUI, lista os modelos de texto/código configurados e mostra CPU, memória
disponível, VRAM consultada por `nvidia-smi` (ou reportada pelo ComfyUI) e
espaço livre no disco do projeto.
O diagnóstico é sob demanda; serviços desligados aparecem como indisponíveis e
respostas inválidas são identificadas como erro. A consulta não aciona downloads
nem tentativas de inicialização.
Preferências da interface (perfil, modelo, tema claro/escuro e opções de
voz/imagem/vídeo) ficam no armazenamento local do navegador. O tema segue a
configuração do sistema até que seja escolhido manualmente.

### Vozes neurais Piper

O serviço inclui um catálogo para português brasileiro, inglês dos EUA e
espanhol (Espanha/Argentina). Para baixar explicitamente os arquivos:

```bash
./scripts/download-piper-voice.sh
docker compose build whisper
docker compose up -d whisper
curl http://localhost:8001/health
```

O download é salvo em `whisper/models/tts/`, não é incluído no Git e não ocorre
automaticamente ao iniciar o serviço. O script fixa uma revisão do catálogo e
valida SHA-256 dos oito modelos e arquivos de configuração. O runtime
`piper-tts` é instalado no container. A síntese é local e usa CPU, sem ocupar a
GPU de imagem/vídeo. A voz Faber é carregada na inicialização; ao selecionar
outra, apenas a última voz escolhida fica carregada em memória.

| Identificador | Idioma/voz | Perfil | Licença indicada no cartão |
|---|---|---|---|
| `pt_BR-faber-medium` | Português BR · Faber | Média | CC0 |
| `pt_BR-cadu-medium` | Português BR · Cadu | Média | CC0 |
| `pt_BR-jeff-medium` | Português BR · Jeff | Média | CC0 |
| `pt_BR-edresson-low` | Português BR · Edresson | Baixa | CC BY 4.0 |
| `pt_BR-dii` | Português BR · Dii (feminina) | Piper | CC BY-NC-ND 4.0 |
| `en_US-hfc_female-medium` | Inglês EUA · feminina | Média | CC BY-NC-SA 4.0 |
| `en_US-hfc_male-medium` | Inglês EUA · masculina | Média | CC BY-NC-SA 4.0 |
| `es_ES-davefx-medium` | Espanhol Espanha · Davefx | Média | CC0 |
| `es_AR-daniela-high` | Espanhol Argentina · Daniela | Alta | CC BY-SA 4.0 |

O seletor **Voz de leitura** mostra somente vozes instaladas e mantém a escolha
no armazenamento local do navegador. As vozes HFC têm licença não comercial;
observe as atribuições e condições CC BY/CC BY-SA indicadas acima antes de
redistribuir áudio, modelos ou o projeto. Confira os cartões/licenças de cada
modelo no catálogo Piper antes de usar em outro contexto.

A voz Dii é uma voz feminina brasileira adicional, publicada pela OpenVoiceOS
e atribuída a TigreGotico Lda. Seu cartão informa que foi ajustada a partir da
voz `pt-PT_dii` com dados sintéticos brasileiros. A licença CC BY-NC-ND 4.0
permite uso não comercial com atribuição; não permite modificar/adaptar o
modelo nem criar vozes derivadas. A interface exibe esse aviso quando Dii está
selecionada. Para baixar somente essa voz:

Fonte e licença: [cartão do modelo Dii](https://huggingface.co/OpenVoiceOS/pipertts_pt-BR_dii)
e [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/).

```bash
./scripts/download-piper-dii-voice.sh
```

O script fixa a revisão do repositório, valida os SHA-256 do modelo e da
configuração e grava os arquivos em `whisper/models/tts/`. Ele não baixa nem
ativa a voz automaticamente; depois do download, reinicie o serviço Whisper
para que a opção apareça no seletor.

A saída atual é WAV, mantida compatível com o endpoint `/api/speech` e com a
interface existente. `GET /api/voices` lista as vozes disponíveis no serviço local e
`SPEECH_TIMEOUT_SECONDS` configura o limite da chamada (120 s por padrão).
Na interface, a leitura pode ser parada ou repetida, e a velocidade de
reprodução pode ser ajustada de 0,80× a 1,25×. Respostas longas são divididas
em trechos de até 900 caracteres para respeitar o limite do serviço e iniciar
a reprodução progressivamente; a velocidade é aplicada pelo navegador e pode
afetar a percepção da voz conforme o suporte do dispositivo.

### Voz feminina em português brasileiro (Kokoro)

O catálogo Piper deste projeto não informa o gênero das vozes `pt_BR`, então
elas não são rotuladas como femininas sem confirmação. Para disponibilizar a
voz feminina brasileira `pf_dora`, o serviço também oferece Kokoro como motor
opcional, sem substituir Piper:

```bash
./scripts/download-kokoro-voice.sh
docker compose build whisper
docker compose up -d whisper
```

O script baixa explicitamente o modelo ONNX completo (~326 MB) e o pacote de
vozes (~28 MB) para `whisper/models/tts/`, conferindo os hashes SHA-256
publicados no release. Não há download durante a inicialização nem durante o
uso; sem esses arquivos, a voz Dora aparece indisponível e as vozes Piper
continuam funcionando. Kokoro roda localmente em CPU via ONNX Runtime, sem
ocupar a GPU de Whisper, imagem ou vídeo. A implementação usa
`kokoro-onnx` (MIT); os pesos do modelo Kokoro são Apache-2.0.

O backend oferece `GET /api/health`, `GET /api/models`, `GET /api/conversations`,
`POST /api/conversations`, `PATCH /api/conversations/{id}` e
`DELETE /api/conversations/{id}` para gerenciar conversas; `GET /api/history`
retorna as mensagens da conversa mais recente ou, com `?conversation_id={id}`,
da conversa selecionada. `POST /api/chat` (respostas com mídia) e
`POST /api/chat/stream` (respostas de texto/código em NDJSON) aceitam
`conversation_id` e salvam o turno nela. Gerações de imagem e vídeo respondem
com `202 Accepted` e um identificador de tarefa; `GET /api/jobs` lista as
tarefas ativas, `GET /api/jobs/{id}` retorna progresso/resultado e
`DELETE /api/jobs/{id}` solicita cancelamento. A interface acompanha o progresso
e oferece cancelamento; a fila executa uma geração de mídia por vez para evitar
disputa simultânea pela GPU. `DELETE /api/history` permanece
disponível para limpar todo o histórico local. `GET /api/gallery` lista somente
as mídias referenciadas no histórico local; `GET /api/diagnostics` consulta os
serviços e recursos locais sem iniciá-los. Outras rotas: `GET /api/voices`,
`GET /api/videos/{filename}`, `POST /api/transcribe` e `POST /api/speech`.
A interface e a API compartilham o mesmo host local. Logs de
acesso e falha ficam em `outputs/logs/agent-service.log`, com rotação limitada
a 2 MiB por arquivo e três arquivos antigos.

Para manter a API/interface iniciada como serviço do usuário no WSL (após
iniciar os containers Docker), com systemd já habilitado na distribuição:

```bash
mkdir -p ~/.config/systemd/user
cp scripts/ai-agent.service ~/.config/systemd/user/ai-agent.service
systemctl --user daemon-reload
systemctl --user enable --now ai-agent.service
systemctl --user status ai-agent.service
```

A unidade usa `127.0.0.1:8765`, reinicia o processo se ele falhar e executa
`conda run` no ambiente `ai-agents`. Para acompanhar o journal, use
`journalctl --user -u ai-agent.service -f`; os logs rotativos do aplicativo
também são gravados em `outputs/logs/`. Para parar/desabilitar:
`systemctl --user disable --now ai-agent.service`. Se o systemd de usuário não
estiver ativo no WSL, continue usando `./scripts/web.sh` em um terminal; não
altere a configuração de inicialização do WSL sem decidir isso explicitamente.

## Agente CLI no Conda

`agents/agent.py` mantém o fluxo WAV → Whisper → Ollama e, por padrão,
ComfyUI. Ele roda no WSL, não em um container próprio. Whisper e Ollama são
chamados por HTTP local. Para gerar uma imagem, o CLI usa `docker exec` e chama
a API local dentro do container; essa chamada não usa as portas do host. Os
encaminhamentos para Ollama, Whisper e a interface ComfyUI escutam somente em
`127.0.0.1`.

O ambiente isolado está definido em `agents/environment.yml` e já foi criado
como `/home/drique/miniconda3/envs/ai-agents` (Python 3.11). O launcher
`scripts/agent.sh` localiza a raiz do projeto e executa o pipeline com
`conda run -n ai-agents`; ele não ativa o Conda no terminal atual, não cria o
ambiente e não inicia os serviços Docker.

O novo fluxo de conversa fica em `agents/core.py`; configurações e integrações
estão separadas em `agents/settings.py` e `agents/adapters/`. O pipeline WAV do
CLI antigo continua disponível e já reutiliza os adaptadores de Ollama, Whisper
e ComfyUI para que a migração permaneça incremental. `agents/chat_cli.py` e o
backend web chamam o mesmo núcleo de conversa.

As integrações e tempos limite são configuráveis por variáveis de ambiente;
sem overrides, os padrões locais atuais são:

| Variável | Padrão | Uso |
|---|---|---|
| `OLLAMA_URL` | `http://127.0.0.1:11434` | API local do Ollama |
| `WHISPER_URL` | `http://127.0.0.1:8001` | API local do Whisper |
| `COMFYUI_CONTAINER` | `stable-diffusion` | Nome do container usado pelo `docker exec` |
| `COMFYUI_API_URL` | `http://127.0.0.1:18188` | Endereço chamado dentro do container |
| `OLLAMA_MODEL` | `qwen3:4b` | Modelo para resposta e prompt visual/de vídeo |
| `OLLAMA_TEXT_MODEL` | valor de `OLLAMA_MODEL` | Modelo padrão do perfil de texto |
| `OLLAMA_CODE_MODEL` | valor de `OLLAMA_TEXT_MODEL` | Modelo padrão do perfil de código |
| `COMFYUI_CHECKPOINT` | `v1-5-pruned-emaonly.safetensors` | Checkpoint para gerar imagem |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | `15` | Tempo limite para conectar às APIs HTTP |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | Tempo limite de resposta do Ollama |
| `WHISPER_TIMEOUT_SECONDS` | `600` | Tempo limite da transcrição |
| `SPEECH_TIMEOUT_SECONDS` | `120` | Tempo limite da síntese Piper |
| `COMFYUI_REQUEST_TIMEOUT_SECONDS` | `60` | Tempo limite de cada chamada à API ComfyUI |
| `IMAGE_TIMEOUT_SECONDS` | `300` | Tempo total permitido para gerar uma imagem |
| `VIDEO_TIMEOUT_SECONDS` | `1800` | Tempo total permitido para gerar um vídeo |

Os valores podem ser definidos apenas para uma execução, por exemplo:

```bash
OLLAMA_TIMEOUT_SECONDS=420 ./scripts/agent.sh outputs/audio/amostra_ptbr.wav --no-image
```

O launcher não carrega automaticamente o `.env` do Docker Compose; assim, as
credenciais da interface web não são misturadas à configuração do agente. Para
manter o processamento local, mantenha as URLs nos endereços `127.0.0.1`
padrão. O agente rejeita URLs HTTP para hosts externos para evitar enviar áudio
ou prompts por engano a outro serviço.

Antes de enviar o áudio, o agente verifica a saúde do Whisper, confirma que o
modelo Ollama configurado está instalado e, quando a geração de imagem está
ativada, verifica a API do ComfyUI e confirma que o checkpoint configurado
aparece na lista de checkpoints do `CheckpointLoaderSimple`. Se algum serviço,
modelo ou checkpoint não estiver disponível, a execução para com uma mensagem
indicando o componente a verificar. A disponibilidade é consultada antes de
enviar o áudio ao Whisper.

Se precisar recriá-lo por não existir, use Conda-forge para evitar os canais
`defaults`:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda env create -f agents/environment.yml
```

Depois de conferir transcrição e resposta, rode sem `--no-image` para gerar
também uma imagem 512×512:

```bash
./scripts/agent.sh outputs/audio/amostra_ptbr.wav
```

Para rodar os testes unitários sem chamar serviços nem gerar arquivos no projeto:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda run -n ai-agents python -m unittest discover -s tests
```

Transcrições ficam em `outputs/audio/transcriptions/`, respostas e metadados em
`outputs/text/` e imagens em `outputs/images/`. O pipeline foi testado com
`outputs/audio/amostra_ptbr.wav`; por ser fala sintética, a transcrição teve
erros e não substitui um teste de precisão com voz natural.

## Estado do checklist do pipeline

- [x] Diagnóstico inicial: ComfyUI ativo; RTX 4060 visível no container.
- [x] Criar pastas persistentes `outputs/audio`, `outputs/text`,
  `outputs/images` e `outputs/videos`.
- [x] Configurar o ComfyUI para salvar imagens em `outputs/images`; Compose
  validado, volume conferido e container recriado.
- [x] Iniciar Ollama, validar a API e testar `qwen3:4b` em português.
- [x] Implementar e iniciar o serviço separado faster-whisper; `/health`,
  acesso à RTX 4060 e cache persistente do modelo `small` confirmados.
- [x] Gerar WAV sintético em português e validar uma transcrição, salvando TXT
  e JSON em `outputs/audio/transcriptions/`.
- [x] Persistir checkpoint SD 1.5 em `stable-diffusion/models/checkpoints/` e
  confirmar que o ComfyUI o lista.
- [x] Gerar imagem 512×512 com SD 1.5 e confirmar o PNG em `outputs/images/`.
- [x] Implementar CLI com Conda no WSL para transcrição, resposta Ollama e
  geração opcional pelo ComfyUI.
- [x] Separar configuração e adaptadores locais; criar núcleo compartilhado de
  conversa com roteamento explícito por perfil e modelo já instalado.
- [x] Criar CLI de chat e backend/API de loopback sem dependências web externas.
- [x] Criar interface web local para texto, código, seleção de modelo, imagens
  opcionais e gravação de microfone.
- [x] Integrar fala de saída com Piper local e seleção de vozes.
- [ ] Testar gravação com microfone físico no navegador e validar a qualidade da
  síntese de voz no computador.
- [ ] Escolher e testar um modelo/workflow de vídeo compatível com a RTX 4060
  Laptop (8 GiB), depois de comparar requisitos e aprovar o modelo; nenhum foi
  baixado nesta etapa.
- [x] Criar o ambiente Conda `ai-agents` usando somente `conda-forge`.
- [x] Validar o CLI sem imagem: WAV → Whisper → Ollama; salvar resposta e
  metadados em `outputs/text/`.
- [x] Rodar o WAV de exemplo de ponta a ponta e conferir transcrição, resposta,
  metadados JSON e imagem 512×512 nos diretórios persistentes.
- [ ] Avaliar modelo/workflow de vídeo.
- [ ] Testar a precisão da transcrição com voz natural e avaliar captura de áudio
  por microfone.

O ComfyUI mantém sua interface independente para workflows manuais. A nova
interface local cobre chat e os fluxos de imagem/voz integrados ao núcleo.

## Whisper local

O serviço usa faster-whisper com CUDA/cuDNN, modelo `small` e idioma `pt`.
O cache fica em `whisper/models/`; os resultados são gravados em
`outputs/audio/transcriptions/`. A porta `8001` está publicada apenas em
`127.0.0.1`, e os arquivos de áudio são enviados ao serviço local, não a uma API
externa.

```bash
# Verificar o serviço (deve informar status ok, modelo small e device cuda)
curl http://localhost:8001/health

# Criar áudio sintético de teste em português do Brasil
docker compose exec -T whisper espeak-ng -v pt-br -s 145 \
  -w /outputs/audio/amostra_ptbr.wav \
  'Olá, este é um teste local do sistema de inteligência artificial. Estou falando em português do Brasil.'

# Transcrever um arquivo de áudio local suportado
curl -fS http://localhost:8001/transcribe \
  -F 'file=@outputs/audio/amostra_ptbr.wav'

# Verificar os artefatos persistidos
find outputs/audio/transcriptions -maxdepth 1 -type f -printf '%f\\n' | sort
```

A API, GPU, cache persistente e uma transcrição de WAV foram validados. O WAV de
eSpeak é sintético, então a qualidade do texto reconhecido pode variar; use uma
gravação natural para avaliar a precisão antes de ajustar o modelo.

## Diagnóstico rápido

```bash
# Estado e portas publicadas
docker compose ps

# Logs recentes
docker compose logs --tail=100 ollama whisper stable-diffusion

# API do Ollama
curl http://localhost:11434/api/tags

# Resposta inicial do portal/interface ComfyUI
curl -I http://localhost:8188

# GPU visível no container do ComfyUI
docker compose exec stable-diffusion nvidia-smi
```

Ao pedir ajuda, compartilhe mensagens de erro e logs relevantes, mas nunca o
conteúdo do `.env`, tokens, senhas ou chaves privadas.
