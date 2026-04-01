# CAM AI — Monitoramento Inteligente com Visão e Assistente Conversacional

Sistema de monitoramento residencial que combina câmera IP, análise de IA por visão computacional e um **assistente conversacional** que usa a câmera como seus olhos para conversar sobre o ambiente, os eventos do dia e a rotina do morador.

---

## O que ele faz

### Monitoramento em tempo real
- Captura contínua via RTSP de câmeras TP-Link (e compatíveis)
- Análise de cenas a cada N segundos usando **Claude (AWS Bedrock)**
- Reconhecimento do morador comparando com foto de referência
- Detecção de eventos: presença no PC, saída de casa, ausência prolongada, etc.

### Assistente com Visão (`🎤 Assistente`)
- Janela de chat integrada à GUI
- O assistente **vê o que a câmera está vendo** em tempo real
- Lembra dos eventos e descrições do dia inteiro
- Suporte a **voz**: clique em Gravar, fale, clique em Parar → transcrição automática via Whisper
- Responde por **texto e voz** (TTS nativo do Windows)
- Exemplos de uso:
  - *"O que você está vendo agora?"*
  - *"Alguém entrou em casa hoje?"*
  - *"Como foi meu dia hoje?"*
  - *"Está tudo bem na sala?"*

### Agenda e saudação
- Ao reconhecer o morador chegando, anuncia os compromissos do dia via TTS
- Regra inteligente: só anuncia novamente se o morador ficou ausente por 30+ minutos
- Integração com API de agenda (configurável)

### Gravação e transcrição de áudio
- Detecta silêncio automaticamente — só grava quando há som
- Transcrição automática em background via **Whisper** (modelo small, CPU)
- Arquivos salvos em `registros/audio/` com ID e timestamp
- Filtro VAD evita alucinações do Whisper em silêncio

### Persistência
- Frames analisados salvos em `registros/{id}.jpg` e `registros/{id}.txt`
- Banco SQLite em `data/cam.db` com frames, eventos e log de ações
- Histórico navegável na GUI com miniaturas e eventos coloridos

### Interface
- GUI Tkinter responsiva e redimensionável
- Modo picture-in-picture (histórico colapsável)
- Feed ao vivo a 10fps independente da análise IA
- Modal de descrição detalhada da cena
- Histórico de detecções com clique para ver snapshot

---

## Arquitetura

```
main.py
  └── CameraService (service.py)
        ├── _capture_loop     → feed ao vivo (100ms) + fila de análise
        ├── _analysis_loop    → Bedrock Claude → eventos → ações
        ├── AudioRecorder     → gravação por silêncio (P2 mic)
        └── AudioTranscriber  → worker Whisper em subprocess

CameraGUI (gui.py)
  ├── Feed ao vivo (escala com janela)
  ├── Histórico de detecções (scrollable)
  └── AssistantWindow
        ├── ConversationAssistant (assistant.py) → Bedrock com visão
        └── MicRecorder → transcribe_once.py (subprocess isolado)

Bedrock (analyzer.py)
  └── 2 imagens: foto de referência + frame atual → JSON estruturado

action_engine.py
  ├── announce_agenda  → TTS + API de agenda
  ├── cooldown 30min   → evita repetição excessiva
  └── tts              → PowerShell SpeechSynthesizer
```

---

## Setup

### Pré-requisitos
- Python 3.11+
- Windows (TTS usa PowerShell nativo)
- Câmera IP com suporte RTSP (testado com TP-Link C200)
- AWS com acesso ao Bedrock (`us.anthropic.claude-sonnet-4-6`)

### Instalação

```bash
git clone https://github.com/hudsonrj/cam_ai.git
cd cam_ai
pip install -r requirements.txt
```

### Configuração

```bash
cp config.yaml.example config.yaml
```

Edite `config.yaml`:

```yaml
camera:
  host: 192.168.X.X          # IP da câmera
  user: seu_usuario
  password: "sua_senha"
  rtsp_path: /stream1
  interval_seconds: 5        # frequência de análise

audio:
  device: 2                  # índice do microfone (sounddevice)

bedrock:
  region: us-east-1
  model_id: us.anthropic.claude-sonnet-4-6

owner_photo: data/owner.jpg  # foto do morador para reconhecimento
```

Defina a variável de ambiente:
```bash
set AWS_BEARER_TOKEN_BEDROCK=seu_token
```

Adicione sua foto em `data/owner.jpg`.

### Execução

```bash
python main.py
```

Ou use o executável gerado pelo PyInstaller em `dist/CAM Monitor/CAM Monitor.exe`.

### Build do executável

```bash
python -m PyInstaller cam_monitor.spec -y
```

---

## Rede / Tailscale

Se usar Tailscale, ele pode interceptar o tráfego para a câmera via subnet routing. Solução:

```bash
# Desabilita accept-routes para não rotear subnet local via Tailscale
tailscale set --accept-routes=false

# Adiciona rota persistente (como Admin) apontando para interface WiFi
route -p add 192.168.X.0 mask 255.255.255.0 192.168.X.1 metric 1 if <ifindex_wifi>
```

O script `fix_rota_camera.bat` automatiza isso para a rede padrão.

---

## Estrutura de arquivos

```
cam/
  analyzer.py         — análise de frames via Bedrock (visão)
  assistant.py        — assistente conversacional com visão
  action_engine.py    — motor de ações (TTS, agenda, Telegram)
  audio_recorder.py   — gravação por silêncio
  capture.py          — conexão RTSP com reconexão automática
  db.py               — SQLite (frames, eventos, actions_log)
  detector.py         — mapeamento eventos → regras do config
  gui.py              — interface Tkinter + AssistantWindow
  service.py          — orquestração de threads
  transcribe_worker.py — worker Whisper (batch, background)
  transcribe_once.py  — Whisper single-file para o assistente
  transcriber.py      — agendador do worker de transcrição

data/
  cam.db              — banco SQLite
  owner.jpg           — foto do morador (não versionada)

registros/
  {id}.jpg            — frames analisados
  {id}.txt            — descrições
  audio/              — gravações de áudio + transcrições
```

---

## Potencial e próximas funcionalidades

O projeto já tem a infraestrutura para evoluir para um **assistente doméstico completo**:

### Curto prazo
- [ ] **Wake word** — ativar o assistente por voz ("Hey CAM") sem clicar
- [ ] **Múltiplas câmeras** — monitorar diferentes cômodos simultaneamente
- [ ] **Alertas Telegram** — notificar sobre eventos relevantes no celular
- [ ] **Modo noturno** — ajustar sensibilidade e análise por horário

### Médio prazo
- [ ] **Memória persistente** — lembrar de eventos de dias anteriores
- [ ] **Dashboard web** — visualização de histórico via browser
- [ ] **Análise de padrões** — detectar anomalias na rotina ("você chegou mais tarde que o normal")
- [ ] **Integração com Home Assistant** — automação residencial baseada em presença

### Longo prazo
- [ ] **Streaming de vídeo** — assistente respondendo com contexto de vídeo ao vivo
- [ ] **Reconhecimento de visitas** — identificar pessoas frequentes e aprender nomes
- [ ] **Modo proativo** — assistente inicia conversa baseado em eventos ("Você está há 4h no PC, já fez uma pausa?")
- [ ] **Transcrição contínua** — assistente que ouve o ambiente e reage a comandos naturais sem interface

---

## Contribuindo

Pull requests são bem-vindos. Áreas com mais oportunidade:

- `cam/analyzer.py` — melhorar prompts, adicionar novos tipos de evento
- `cam/assistant.py` — memória de longo prazo, personalidade configurável
- `cam/gui.py` — temas, atalhos de teclado, suporte multi-câmera
- `cam/action_engine.py` — novos tipos de ação (webhooks, MQTT, etc.)

Para rodar os testes:
```bash
pytest tests/
```

---

## Licença

MIT
