# Como Rodar o Pipeline UDA (Unstructured Data Analysis)

Este documento descreve o passo a passo de como configurar o ambiente, preencher as variáveis necessárias e executar o Pipeline UDA localmente.

## 1. Pré-requisitos
- Python 3.10 ou superior instalado.
- Acesso ao terminal / linha de comando (preferencialmente Bash ou PowerShell).

## 2. Configurando o Ambiente Virtual
Recomenda-se utilizar um ambiente virtual (`venv`) para isolar as dependências do projeto. 

1. No terminal, na raiz do projeto (onde está o `requirements.txt`), crie o ambiente virtual:
   ```bash
   python -m venv venv
   ```

2. Ative o ambiente virtual:
   - **Linux/macOS:**
     ```bash
     source venv/bin/activate
     ```
   - **Windows:**
     ```bash
     venv\Scripts\activate
     ```

## 3. Instalando Dependências
Com o ambiente ativado, instale os pacotes requeridos:
```bash
pip install -r requirements.txt
```

## 4. Configurando Variáveis de Ambiente
O projeto precisa de algumas configurações locais, especialmente a chave da API do LLM (neste caso, a API do Google Gemini).

1. Renomeie (ou faça uma cópia de) `.env.example` para `.env`:
   ```bash
   cp .env.example .env
   ```
2. Abra o arquivo `.env` e preencha com a sua chave de API do Gemini:
   ```env
   GEMINI_API_KEY="COLOQUE_SUA_CHAVE_AQUI"
   ```

## 5. Rodando o Servidor (API)
O pipeline pode ser executado como um serviço web utilizando a biblioteca **FastAPI** e **Uvicorn**. Ele possui endpoints para consultar os dados consolidados e processar relatórios em formato PDF manualmente. O *scheduler* (ingestão automática) será ativado junto com o servidor.

1. Inicie o servidor:
   ```bash
   uvicorn app.api:app --reload
   ```
   *(Pode também utilizar `uvicorn app.api:app --host 0.0.0.0 --port 8000`)*

2. Acesse a documentação da API em:
   [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## 6. Executando os Testes do Pipeline
A implementação possui um arquivo contendo os testes do fluxo de trabalho do processamento de relatórios: testes de leitura local (parsing), regras semânticas, conversão de layout e idempotência.

Para testar o pipeline completo:
```bash
python test_pipeline.py
```

Para rodar **somente** os testes de parser e chunking sem acessar a API do Gemini (não gasta tokens):
```bash
python test_pipeline.py --parsing-only
```
