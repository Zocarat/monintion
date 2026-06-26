# MONINTION — System Monitor

> Dashboard de monitoramento de sistema em tempo real para Windows, Linux e Raspberry Pi.

![Python](https://img.shields.io/badge/Python-3.8+-blue?style=flat-square&logo=python)
![Flask](https://img.shields.io/badge/Flask-2.0+-black?style=flat-square&logo=flask)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20RPi-lightgrey?style=flat-square)

---

## Visão Geral

MONINTION é um painel web de monitoramento leve e sem dependências de frontend — sem Node.js, sem build step. Um único `python server.py` sobe tudo.

**Stack:**
- Backend: Python 3 + Flask + psutil
- Banco: SQLite (histórico local, não versionado)
- Frontend: HTML/CSS/JS puro + Chart.js 4 via CDN

---

## Funcionalidades

### ⬡ Visão Geral
- Métricas ao vivo de CPU, RAM e discos (atualização a cada 5s)
- Gráfico de uso de disco por partição e gráfico donut de RAM
- Mini cards por core de CPU com barra de uso
- Uptime, contagem de processos e tráfego de rede acumulado

### 📈 Histórico
- Gráficos de linha com histórico de disco (GB e % — eixos separados)
- Histórico de RAM e CPU com seletor de registros (50–500)
- Gravação automática a cada 30 segundos em background thread

### 📂 Explorador de Arquivos
- Navegação por diretórios com breadcrumb e seletor de drives
- Filtros por nome, extensão, data e tamanho mínimo
- Ordenação por nome, tamanho e data
- Painel lateral: top 8 maiores arquivos + gráfico donut por extensão

### 🧹 Limpeza do Sistema
- Scan de arquivos temporários e cache com tamanho estimado por categoria
- Windows: temp do usuário/sistema, Prefetch, cache de browsers, miniaturas
- Linux: /tmp, ~/.cache, pip, apt, logs antigos
- Modal com lista dos arquivos antes de deletar — confirmação obrigatória

### 📦 Aplicativos Instalados
- Lista completa via registro do Windows / dpkg no Linux
- Tamanho em disco, data de instalação, último uso e status de execução
- Badge "Em execução" com RAM consumida via psutil

### 🌐 Redes & Conexões
- Cards por interface com IP, MAC, speed, MTU e tráfego acumulado
- Gráfico ao vivo de download/upload em KB/s (atualiza a cada 2s)
- Tabela de conexões TCP/UDP com filtro por status

#### 🔍 Varredura de Portas
- Scanner TCP paralelo (200 threads) com range configurável
- Presets: Comuns (1–1024), Web (80–8888), 1–10k
- Classificação de risco por porta: CRÍTICO / ALTO / MÉDIO / BAIXO

### 🖥️ Informações do Sistema
- OS, hostname, arquitetura, Python, boot time, uptime
- CPU: modelo, cores, frequência, context switches, barras de uso por modo
- RAM, swap, discos, interfaces de rede, usuários ativos
- Sensores de temperatura (Linux/RPi) e bateria (se disponível)

### 🔗 Acesso Remoto SSH
- Hosts salvos no browser (localStorage) — senha ou chave SSH
- Terminal integrado com output colorido
- **Deploy automatizado**: envia arquivos via SFTP, instala dependências e inicia o MONINTION no host remoto
- Verificação de status e acesso direto ao painel remoto (`http://<host>:5050`)

---

## Instalação

```bash
git clone https://github.com/seu-usuario/monintion.git
cd monintion
pip install -r requirements.txt
python server.py
```

Acesse: `http://localhost:5050`

---

## Plataformas testadas

| Plataforma | Status | Notas |
|------------|--------|-------|
| Windows 10/11 | ✅ | Funcionalidade completa |
| Ubuntu / Debian | ✅ | dpkg para apps, apt cache no cleaner |
| Kali Linux | ✅ | |
| Raspberry Pi OS | ✅ | Sensores de temperatura disponíveis |

---

## Estrutura

```
monintion/
├── server.py           # Flask app + recorder daemon + todos os endpoints
├── db.py               # SQLite — init e conexão
├── requirements.txt    # flask, psutil, paramiko
└── templates/
    └── index.html      # SPA completo — sem build step
```

---

## Aviso de Segurança

Os endpoints `/api/files*` e `/api/remote/*` não possuem autenticação. O projeto é concebido para uso **local ou em rede interna**. Antes de qualquer exposição pública, implemente proteção por token.

---

## Licença

MIT
