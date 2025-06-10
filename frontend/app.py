# app.py
import streamlit as st
import requests
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional
import uuid
from threading import Thread
import queue

st.set_page_config(
    page_title="Deep Research Service",
    page_icon="🔍",
    layout="wide"
)

st.markdown("""
<style>
    .main-header {
        text-align: center;
        padding: 5px 0;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-radius: 10px;
        margin-bottom: 30px;
    }
    .research-container {
        border: 2px solid #e0e0e0;
        border-radius: 12px;
        padding: 24px;
        margin: 16px 0;
        background-color: rgba(227, 242, 253, 0.07);
    }
    /* Customização do st.status */
    div[data-testid="stStatusContainer"] {
        background-color: rgba(255, 255, 255, 0.05);
    }
    div[data-testid="stStatusContainer"] > div {
        padding: 10px;
    }
    /* Garante que o texto do relatório seja exibido corretamente */
    .report-content {
        white-space: pre-wrap !important;
        line-height: 1.6 !important;
        overflow-wrap: break-word !important;
        word-wrap: break-word !important;
    }
    /* Status de andamento da pesquisa */
    .status-badge {
        display: inline-block;
        padding: 5px 10px;
        border-radius: 15px;
        font-weight: bold;
        margin-bottom: 10px;
    }
    .status-running {
        background-color: #4CAF50;
        color: white;
    }
    .status-error {
        background-color: #F44336;
        color: white;
    }
    .status-warning {
        background-color: #FF9800;
        color: white;
    }
    .status-complete {
        background-color: #2196F3;
        color: white;
    }
    /* Estilos para mensagens de timeout */
    .timeout-message {
        background-color: #FFF3E0;
        border-left: 5px solid #FF9800;
        padding: 10px;
        margin: 10px 0;
        border-radius: 4px;
    }
    /* Animação de carregamento para status em andamento */
    @keyframes pulse {
      0% { opacity: 0.6; }
      50% { opacity: 1; }
      100% { opacity: 0.6; }
    }
    .pulse {
      animation: pulse 2s infinite ease-in-out;
    }
</style>
""", unsafe_allow_html=True)

def initialize_session_state():
    """Inicializa variáveis de sessão"""
    if 'final_report' not in st.session_state:
        st.session_state.final_report = ""
    if 'is_researching' not in st.session_state:
        st.session_state.is_researching = False
    if 'research_progress' not in st.session_state:
        st.session_state.research_progress = 0
    if 'current_task' not in st.session_state:
        st.session_state.current_task = ""
    if 'research_id' not in st.session_state:
        st.session_state.research_id = None
    if 'event_queue' not in st.session_state:
        st.session_state.event_queue = queue.Queue()
    if 'sse_thread' not in st.session_state:
        st.session_state.sse_thread = None
    if 'real_time_events' not in st.session_state:
        st.session_state.real_time_events = []
    if 'backend_check_time' not in st.session_state:
        st.session_state.backend_check_time = 0
    if 'backend_status' not in st.session_state:
        st.session_state.backend_status = None
    if 'last_status_update' not in st.session_state:
        st.session_state.last_status_update = ""
    if 'research_start_time' not in st.session_state:
        st.session_state.research_start_time = None
    if 'max_research_time' not in st.session_state:
        st.session_state.max_research_time = 1800  # 30 minutos em segundos
    if 'research_status' not in st.session_state:
        st.session_state.research_status = "not_started"
    if 'last_check_time' not in st.session_state:
        st.session_state.last_check_time = 0

def get_status_box_class(event_type: str) -> str:
    """Retorna a classe CSS apropriada para o tipo de evento"""
    mapping = {
        'error': 'error-box',
        'complete': 'status-box',
        'plan': 'plan-box',
        'search': 'search-box',
        'search_complete': 'search-box',
        'analyze': 'analyze-box',
        'analyze_complete': 'analyze-box',
        'report': 'report-box',
        'report_complete': 'report-box',
        'start': 'info-box',
        'pipeline_start': 'info-box',
        'decision': 'plan-box',
        'timeout': 'error-box',
        'cancelled': 'error-box'
    }
    return mapping.get(event_type, 'info-box')

def format_status_message(event: dict) -> str:
    """Formata mensagem do evento para o status"""
    event_type = event.get('type', '')
    data = event.get('data', {})
    message = data.get('message', '')
    details = data.get('details', {})
    
    if event_type == 'start':
        return f"🚀 Iniciando pesquisa"
    elif event_type == 'plan' and 'query' in details:
        return f"📋 Planejando: {details['query'][:50]}..." if len(details['query']) > 50 else f"📋 Planejando: {details['query']}"
    elif event_type == 'search' and 'query' in details:
        return f"🔍 Buscando: {details['query'][:50]}..." if len(details['query']) > 50 else f"🔍 Buscando: {details['query']}"
    elif event_type == 'search_complete':
        count = details.get('count', 0)
        return f"✅ Busca concluída"
    elif event_type == 'analyze' and 'titles' in details and details['titles']:
        titles = details['titles'][:2]
        if titles:
            return f"🔬 Analisando: {', '.join(titles)[:50]}..." if len(', '.join(titles)) > 50 else f"🔬 Analisando: {', '.join(titles)}"
        return f"🔬 Analisando resultados"
    elif event_type == 'analyze_complete':
        iteration = details.get('iteration', 0)
        return f"📊 Análise concluída - Iteração {iteration}"
    elif event_type == 'report':
        total = details.get('total_results', 0)
        if total > 0:
            return f"📝 Gerando relatório ({total} resultados)"
        return f"📝 Gerando relatório"
    elif event_type == 'report_complete':
        return f"📄 Relatório completo"
    elif event_type == 'complete':
        return f"🎉 Pesquisa concluída!"
    elif event_type == 'error':
        return f"❌ Erro: {message}"
    elif event_type == 'timeout':
        return f"⏱️ Timeout: {message}"
    elif event_type == 'cancelled':
        return f"🛑 Pesquisa cancelada: {message}"
    elif event_type == 'decision':
        return f"🤔 {message}"
    elif message:
        emojis = {
            'pipeline_start': '⚙️',
            'connected': '🔗',
            'disconnected': '🔌',
            'check_result': '🔄'
        }
        emoji = emojis.get(event_type, '⚙️')
        return f"{emoji} {message}"
    else:
        return "🔄 Processando..."

def format_event_message(event: dict) -> str:
    """Formata mensagem do evento para exibição"""
    event_type = event.get('type', '')
    data = event.get('data', {})
    message = data.get('message', '')
    timestamp = data.get('timestamp', '')
    details = data.get('details', {})
    
    if event_type == 'search' and 'query' in details:
        message = f"🔍 {message}"
    elif event_type == 'plan' and 'query' in details:
        message = f"📋 {message}"
    elif event_type == 'analyze':
        message = f"🔬 {message}"
        if 'titles' in details:
            titles = details['titles']
            if titles:
                message += f"\n   📄 Analisando: {', '.join(titles[:2])}..."
    elif event_type == 'report':
        message = f"📝 {message}"
        if 'total_results' in details:
            message += f" ({details['total_results']} resultados)"
    elif event_type == 'complete':
        message = f"✅ {message}"
    elif event_type == 'error':
        message = f"❌ {message}"
    elif event_type == 'timeout':
        message = f"⏱️ {message}"
    elif event_type == 'cancelled':
        message = f"🛑 {message}"
    elif event_type == 'start':
        message = f"🚀 {message}"
    elif event_type == 'decision':
        message = f"🤔 {message}"
    elif event_type == 'check_result':
        message = f"🔄 {message}"
    
    return message

def sse_consumer(research_id: str, event_queue: queue.Queue):
    """Consome eventos SSE em thread separada"""
    try:
        url = f"http://localhost:8000/research/stream/{research_id}"
        headers = {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
        }
        
        response = requests.get(url, stream=True, headers=headers, timeout=1800)
        
        if response.status_code != 200:
            event_queue.put({
                'type': 'error',
                'data': {
                    'message': f'Erro ao conectar SSE: Status {response.status_code}',
                    'timestamp': datetime.now().isoformat()
                }
            })
            return
        
        event_data = ""
        event_type = None
        
        for line in response.iter_lines(decode_unicode=True):
            if line:
                if line.startswith('event:'):
                    event_type = line.split(':', 1)[1].strip()
                elif line.startswith('data:'):
                    event_data = line.split(':', 1)[1].strip()
                elif line.startswith(':'):
                    # Heartbeat, continua
                    continue
                else:
                    continue
            else:
                if event_type and event_data:
                    try:
                        data = json.loads(event_data)
                        
                        # Adiciona evento à fila - não modifica st.session_state aqui
                        event_queue.put({
                            'type': event_type,
                            'data': data
                        })
                        
                        # Se é um evento de finalização
                        if event_type in ['disconnected', 'complete', 'error', 'timeout', 'cancelled']:
                            # Adicione uma solicitação para verificar o resultado
                            event_queue.put({
                                'type': 'check_result',
                                'data': {
                                    'message': 'Verificando resultado final',
                                    'timestamp': datetime.now().isoformat()
                                }
                            })
                            break
                    except json.JSONDecodeError as e:
                        print(f"Erro ao decodificar JSON: {e}")
                        print(f"Dados recebidos: {event_data}")
                    
                    event_data = ""
                    event_type = None
                    
    except requests.exceptions.Timeout:
        event_queue.put({
            'type': 'timeout',
            'data': {
                'message': 'Timeout na conexão SSE',
                'timestamp': datetime.now().isoformat()
            }
        })
    except Exception as e:
        event_queue.put({
            'type': 'error',
            'data': {
                'message': f'Erro na conexão SSE: {str(e)}',
                'timestamp': datetime.now().isoformat()
            }
        })

def verify_research_status():
    """Verifica o status atual da pesquisa no backend"""
    if not st.session_state.research_id:
        return False
        
    try:
        response = requests.get(
            f"http://localhost:8000/research/{st.session_state.research_id}",
            timeout=5
        )
        
        if response.status_code == 200:
            result_data = response.json()
            status = result_data.get("status", "unknown")
            
            # Atualiza status e resultado se necessário
            st.session_state.research_status = status
            
            if status in ["completed", "timeout", "failed", "cancelled"]:
                st.session_state.is_researching = False
                
                if "result" in result_data and result_data["result"]:
                    st.session_state.final_report = result_data["result"]
                    return True
            
            return status in ["completed", "timeout", "failed", "cancelled"]
        else:
            return False
    except Exception as e:
        print(f"Erro ao verificar status: {str(e)}")
        return False

def process_event_queue():
    """Processa eventos da fila"""
    try:
        # Processa eventos da fila
        events_processed = False
        while not st.session_state.event_queue.empty():
            events_processed = True
            event = st.session_state.event_queue.get_nowait()
            st.session_state.real_time_events.append(event)
            
            event_type = event.get('type', '')
            data = event.get('data', {})
            
            # Processa eventos específicos
            if event_type == 'start':
                st.session_state.research_progress = 10
                st.session_state.research_status = "running"
                
                # Iniciar temporizador quando a pesquisa começa
                if not st.session_state.research_start_time:
                    st.session_state.research_start_time = time.time()
            elif event_type == 'plan':
                current_progress = st.session_state.research_progress
                st.session_state.research_progress = min(current_progress + 10, 90)
            elif event_type == 'search':
                current_progress = st.session_state.research_progress
                st.session_state.research_progress = min(current_progress + 5, 90)
            elif event_type == 'analyze':
                current_progress = st.session_state.research_progress
                st.session_state.research_progress = min(current_progress + 10, 90)
            elif event_type == 'report':
                st.session_state.research_progress = 90
            elif event_type == 'complete':
                st.session_state.research_progress = 100
                st.session_state.is_researching = False
                st.session_state.research_status = "completed"
                st.session_state.research_start_time = None  # Reset do temporizador
                
                # Verifica se recebemos o relatório final
                if 'result' in data:
                    st.session_state.final_report = data.get('result', '')
                else:
                    # Se não tiver o resultado no evento, faça uma solicitação explícita
                    verify_research_status()
            elif event_type in ['error', 'timeout', 'cancelled', 'disconnected']:
                st.session_state.is_researching = False
                st.session_state.research_status = event_type if event_type != 'disconnected' else "completed"
                st.session_state.research_start_time = None  # Reset do temporizador
                
                # Verifica se recebemos algum resultado parcial
                if 'result' in data and data['result']:
                    st.session_state.final_report = data['result']
                else:
                    # Se não tiver o resultado no evento, faça uma solicitação explícita
                    verify_research_status()
            elif event_type == 'check_result':
                # Evento explícito para verificar o resultado
                if not st.session_state.final_report:
                    verify_research_status()
            
            # Atualiza mensagem de status
            status_message = format_status_message(event)
            if status_message:
                st.session_state.current_task = status_message
                st.session_state.last_status_update = status_message
        
        # Se processou eventos e a pesquisa está concluída, verifique o resultado final
        if events_processed and not st.session_state.is_researching and not st.session_state.final_report:
            verify_research_status()
            
    except queue.Empty:
        pass

def check_for_results():
    """Verifica se há resultados disponíveis no backend"""
    if not st.session_state.research_id:
        return False
        
    try:
        response = requests.get(
            f"http://localhost:8000/research/{st.session_state.research_id}",
            timeout=3
        )
        if response.status_code == 200:
            result_data = response.json()
            status = result_data.get("status", "")
            
            # Atualiza status
            if status:
                st.session_state.research_status = status
            
            # Se pesquisa concluída e temos resultado
            if status in ["completed", "timeout", "failed", "cancelled"] and "result" in result_data:
                if result_data["result"]:
                    st.session_state.final_report = result_data["result"]
                    st.session_state.is_researching = False
                    return True
            
            # Se pesquisa concluída, marque is_researching como falso
            if status in ["completed", "timeout", "failed", "cancelled"]:
                st.session_state.is_researching = False
                return True
            
            return False
    except:
        return False

def check_backend_status():
    """Verifica se o backend está funcionando (com cache)"""
    current_time = time.time()
    
    if 'backend_check_time' in st.session_state and 'backend_status' in st.session_state:
        if current_time - st.session_state.backend_check_time < 30:
            return st.session_state.backend_status

    try:
        response = requests.get("http://localhost:8000/health", timeout=3)
        status = response.status_code == 200
        st.session_state.backend_check_time = current_time
        st.session_state.backend_status = status
        return status
    except:
        st.session_state.backend_check_time = current_time
        st.session_state.backend_status = False
        return False

def start_research(query: str, max_iterations: int, llm_provider: str, 
                  api_key: Optional[str] = None, model_name: Optional[str] = None):
    """Inicia uma nova pesquisa"""
    try:
        request_data = {
            "query": query,
            "llm_provider": llm_provider,
            "max_iterations": max_iterations
        }
        
        if api_key:
            request_data["api_key"] = api_key
        if model_name:
            request_data["model_name"] = model_name
        
        response = requests.post(
            "http://localhost:8000/research", 
            json=request_data,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            research_id = result.get("research_id")
            
            st.session_state.research_id = research_id
            st.session_state.event_queue = queue.Queue()
            st.session_state.sse_thread = Thread(
                target=sse_consumer, 
                args=(research_id, st.session_state.event_queue)
            )
            st.session_state.sse_thread.daemon = True
            st.session_state.sse_thread.start()
            
            # Iniciar temporizador para a pesquisa
            st.session_state.research_start_time = time.time()
            st.session_state.research_status = "running"
            
            return True
        else:
            st.error(f"Erro ao iniciar pesquisa: {response.status_code}")
            return False
            
    except Exception as e:
        st.error(f"Erro ao conectar com backend: {str(e)}")
        return False

def render_status_badge():
    """Renderiza badge de status da pesquisa"""
    status = st.session_state.research_status
    
    if status == "running":
        st.markdown('<div class="status-badge status-running pulse">⚙️ EM ANDAMENTO</div>', unsafe_allow_html=True)
    elif status == "completed":
        st.markdown('<div class="status-badge status-complete">✅ CONCLUÍDO</div>', unsafe_allow_html=True)
    elif status == "timeout":
        st.markdown('<div class="status-badge status-warning">⏱️ TIMEOUT</div>', unsafe_allow_html=True)
    elif status in ["failed", "error"]:
        st.markdown('<div class="status-badge status-error">❌ ERRO</div>', unsafe_allow_html=True)
    elif status == "cancelled":
        st.markdown('<div class="status-badge status-warning">🛑 CANCELADO</div>', unsafe_allow_html=True)

def main():
    initialize_session_state()
    
    st.markdown("""
    <div class="main-header">
        <h1>🔍 Deep Research Service</h1>
        <p>Pesquisa Profunda com IA </p>
    </div>
    """, unsafe_allow_html=True)
    
    with st.sidebar:
        st.header("⚙️ Configurações")
        backend_status = check_backend_status()
        if backend_status:
            st.success("✅ Backend conectado")
        else:
            st.error("❌ Backend desconectado")
            st.info("Execute: `cd backend && uvicorn main:app --reload`")
        
        st.divider()
        
        llm_provider = st.selectbox(
            "🤖 Provedor LLM",
            ["openai", "ollama"],
            help="Escolha entre OpenAI ou Ollama local"
        )
        
        api_key = None
        model_name = None
        
        if llm_provider == "openai":
            api_key = st.text_input(
                "🔑 OpenAI API Key",
                type="password",
                help="Sua chave da API OpenAI"
            )
            model_name = st.selectbox(
                "🧠 Modelo OpenAI",
                ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo"],
                help="Modelo da OpenAI a ser usado"
            )
        else:  
            model_name = st.text_input(
                "🦙 Modelo Ollama",
                value="qwen2.5:7b",
                help="Nome do modelo Ollama (ex: llama2, mistral, codellama)"
            )
            st.info("⚠️ Certifique-se que o Ollama está rodando em localhost:11434")
        
        st.divider()
        
        st.subheader("🛠️ Configurações Avançadas")
        max_iterations = st.slider(
            "Máximo de iterações",
            min_value=1,
            max_value=10,
            value=2,
            help="Número máximo de ciclos de pesquisa"
        )
        
        # Opção para timeout
        max_timeout = st.slider(
            "Timeout (minutos)",
            min_value=5,
            max_value=60,
            value=30,
            help="Tempo máximo para execução da pesquisa"
        )
        st.session_state.max_research_time = max_timeout * 60  # Converte para segundos

    colPrincipal, colAndamento = st.columns([2, 1])
    
    with colPrincipal:
        st.header("🔍 Nova Pesquisa")

        query = st.text_area(
            "Digite sua consulta de pesquisa:",
            height=120,
            placeholder="Ex: Quais são as principais tendências em inteligência artificial para 2024? Como essas tecnologias estão impactando diferentes setores da economia?",
            value='Quais são as principais tendências em inteligência artificial para 2024? Como essas tecnologias estão impactando diferentes setores da economia?',
            help="Seja específico e detalhado para obter melhores resultados"
        )
        
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            can_research = bool(
                query and 
                backend_status and 
                (api_key if llm_provider == "openai" else model_name) and
                not st.session_state.is_researching
            )
            
            if st.button(
                "🚀 Iniciar Pesquisa",
                disabled=not can_research,
                type="primary",
                use_container_width=True
            ):
                st.session_state.real_time_events = []
                st.session_state.final_report = ""
                st.session_state.is_researching = True
                st.session_state.research_progress = 0
                st.session_state.current_task = "Iniciando pesquisa..."
                st.session_state.last_status_update = ""
                st.session_state.research_status = "not_started"
                
                if start_research(query, max_iterations, llm_provider, api_key, model_name):
                    st.rerun()
        
        with col2:
            if st.button(
                "🗑️ Limpar",
                use_container_width=True
            ):
                st.session_state.real_time_events = []
                st.session_state.final_report = ""
                st.session_state.is_researching = False
                st.session_state.research_progress = 0
                st.session_state.current_task = ""
                st.session_state.research_status = "not_started"
                st.rerun()
        
        with col3:
            if st.session_state.is_researching:
                if st.button(
                    "⏹️ Parar",
                    type="secondary",
                    use_container_width=True
                ):
                    if st.session_state.research_id:
                        try:
                            requests.delete(f"http://localhost:8000/research/{st.session_state.research_id}", timeout=3)
                        except:
                            pass
                    st.session_state.is_researching = False
                    st.session_state.research_start_time = None
                    st.session_state.research_status = "cancelled"
                    st.rerun()

        
    with colAndamento:
        st.header("📊 Progresso da Pesquisa")
        
        # Processa eventos da fila
        process_event_queue()
        
        # Exibe badge de status
        render_status_badge()
        
        # Exibe progresso e eventos
        if st.session_state.is_researching or st.session_state.real_time_events:
            
            status_label = st.session_state.current_task if st.session_state.current_task else "🔄 Aguardando atualizações..."
            status_state = "running" if st.session_state.is_researching else "complete"
            
            with st.status(status_label, state=status_state, expanded=False) as status:
                if not st.session_state.real_time_events:
                    status.write("⏳ Aguardando os primeiros eventos...")
                
                for event in reversed(st.session_state.real_time_events):
                    event_type = event.get('type', '')
                    data = event.get('data', {})
                    message = data.get('message', '')
                    details = data.get('details', {})
                    
                    icons = {
                        'start': '🚀',
                        'plan': '📋',
                        'search': '🔍',
                        'search_complete': '✅',
                        'analyze': '🔬',
                        'analyze_complete': '📊',
                        'report': '📝',
                        'report_complete': '📄',
                        'complete': '🎉',
                        'error': '❌',
                        'timeout': '⏱️',
                        'cancelled': '🛑',
                        'pipeline_start': '⚙️',
                        'decision': '🤔',
                        'connected': '🔗',
                        'disconnected': '🔌',
                        'check_result': '🔄'
                    }
                    
                    icon = icons.get(event_type, '📌')
                    
                    if event_type == 'search' and 'query' in details:
                        status.write(f"{icon} **Buscando:** {details['query']}")
                    elif event_type == 'plan' and 'query' in details:
                        status.write(f"{icon} **Nova query planejada:** {details['query']}")
                    elif event_type == 'analyze' and 'titles' in details:
                        status.write(f"{icon} **Analisando resultados**")
                        if details['titles']:
                            for title in details['titles'][:2]:
                                status.write(f"   📄 {title}")
                    elif event_type == 'search_complete':
                        count = details.get('count', 0)
                        status.write(f"{icon} **Busca concluída**")
                    elif event_type == 'analyze_complete':
                        iteration = details.get('iteration', 0)
                        status.write(f"{icon} **Análise concluída** - Iteração {iteration}")
                    elif event_type == 'report':
                        total = details.get('total_results', 0)
                        if total > 0:
                            status.write(f"{icon} **Gerando relatório final** ({total} resultados processados)")
                        status.write(f"{icon} **Gerando relatório final**")
                    elif event_type == 'complete':
                        status.write(f"{icon} **Pesquisa concluída com sucesso!**")
                    elif event_type == 'error':
                        status.write(f"{icon} **Erro:** {message}")
                    elif event_type == 'timeout':
                        status.write(f"{icon} **Timeout:** {message}")
                    elif event_type == 'cancelled':
                        status.write(f"{icon} **Cancelado:** {message}")
                    elif event_type == 'decision':
                        status.write(f"{icon} {message}")
                    elif event_type == 'check_result':
                        status.write(f"{icon} **Verificando resultado final**")
                    else:
                        status.write(f"{icon} {message}")
                    
                    if event_type == 'analyze_complete' and 'insights_preview' in details:
                        status.write(f"💡 **Preview:** {details['insights_preview'][:150]}...")
                    
                    elif event_type == 'report_complete' and 'word_count' in details:
                        word_count = details.get('word_count', 0)
                        status.write(f"📊 Relatório gerado com {word_count} palavras")
        else:
            st.info("Inicie uma nova pesquisa para visualizar o progresso em tempo real.")

        if st.session_state.final_report:
            st.success("✅ Relatório final disponível!")
    
    # VERIFICAÇÃO PERIÓDICA - Atualizado para ser mais robusto
    if st.session_state.is_researching:
        current_time = time.time()
        # Verifica a cada 3 segundos
        if current_time - st.session_state.last_check_time > 3:
            st.session_state.last_check_time = current_time
            
            # Verificar se há resultados e atualizar o estado se necessário
            if check_for_results():
                st.rerun()
            
            # Verificar timeout da pesquisa
            if st.session_state.research_start_time:
                elapsed_time = current_time - st.session_state.research_start_time
                if elapsed_time > st.session_state.max_research_time:
                    st.session_state.is_researching = False
                    st.session_state.research_status = "timeout"
                    # Tentar buscar resultado parcial
                    verify_research_status()
                    st.rerun()
    
    # VERIFICAÇÃO ADICIONAL - Para garantir que eventos de conclusão sejam capturados
    elif st.session_state.research_id and not st.session_state.final_report:
        current_time = time.time()
        # Verifica a cada 5 segundos se a pesquisa já concluiu mas não temos o relatório
        if current_time - st.session_state.last_check_time > 5:
            st.session_state.last_check_time = current_time
            if verify_research_status():
                st.rerun()
    
    # Exibe o relatório final se disponível
    if st.session_state.final_report:
        st.header("📋 Relatório Final")
        
        # Exibe o relatório markdown diretamente
        with st.container():
            st.markdown(st.session_state.final_report)
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.download_button(
                label="📥 Baixar Relatório (Markdown)",
                data=st.session_state.final_report,
                file_name=f"research_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown"
            )
        
        with col2:
            if st.button("📋 Copiar Relatório"):
                st.text_area(
                    "Texto do Relatório:",
                    value=st.session_state.final_report,
                    height=200
                )
        
        with col3:
            if st.button("🔄 Nova Pesquisa"):
                st.session_state.final_report = ""
                st.session_state.real_time_events = []
                st.session_state.research_status = "not_started"
                st.rerun()
    
    # Reexecuta enquanto estiver pesquisando
    if st.session_state.is_researching:
        time.sleep(0.5)
        st.rerun()
    
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #666; padding: 20px;'>
        <p><strong>🔍 Deep Research Service v3.0</strong></p>
        <p>🚀 Powered by Eduardo Gobatti </p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()