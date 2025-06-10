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

# Configuração da página
st.set_page_config(
    page_title="Deep Research Service",
    page_icon="🔍",
    layout="wide"
)

# CSS personalizado
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
        'decision': 'plan-box'
    }
    return mapping.get(event_type, 'info-box')

def format_status_message(event: dict) -> str:
    """Formata mensagem do evento para o status"""
    event_type = event.get('type', '')
    data = event.get('data', {})
    message = data.get('message', '')
    details = data.get('details', {})
    
    # Formatação específica para o status baseado no tipo de evento
    if event_type == 'start':
        return f"🚀 Iniciando pesquisa"
    elif event_type == 'plan' and 'query' in details:
        return f"📋 Planejando: {details['query'][:50]}..." if len(details['query']) > 50 else f"📋 Planejando: {details['query']}"
    elif event_type == 'search' and 'query' in details:
        return f"🔍 Buscando: {details['query'][:50]}..." if len(details['query']) > 50 else f"🔍 Buscando: {details['query']}"
    elif event_type == 'search_complete':
        count = details.get('count', 0)
        return f"✅ Busca concluída: {count} resultados"
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
        return f"📝 Gerando relatório ({total} resultados)"
    elif event_type == 'report_complete':
        return f"📄 Relatório completo"
    elif event_type == 'complete':
        return f"🎉 Pesquisa concluída!"
    elif event_type == 'error':
        return f"❌ Erro: {message}"
    elif event_type == 'decision':
        return f"🤔 {message}"
    elif message:
        # Para mensagens genéricas, adiciona emoji baseado no tipo
        emojis = {
            'pipeline_start': '⚙️',
            'connected': '🔗',
            'disconnected': '🔌'
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
    
    # Formatação especial para diferentes tipos de eventos
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
    elif event_type == 'start':
        message = f"🚀 {message}"
    elif event_type == 'decision':
        message = f"🤔 {message}"
    
    return message

def sse_consumer(research_id: str, event_queue: queue.Queue):
    """Consome eventos SSE em thread separada"""
    try:
        url = f"http://localhost:8000/research/stream/{research_id}"
        headers = {
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
        }
        
        # Usa requests com stream=True para SSE
        response = requests.get(url, stream=True, headers=headers, timeout=300)
        
        if response.status_code != 200:
            event_queue.put({
                'type': 'error',
                'data': {
                    'message': f'Erro ao conectar SSE: Status {response.status_code}',
                    'timestamp': datetime.now().isoformat()
                }
            })
            return
        
        # Processa eventos manualmente linha por linha
        event_data = ""
        event_type = None
        
        for line in response.iter_lines(decode_unicode=True):
            if line:
                if line.startswith('event:'):
                    event_type = line.split(':', 1)[1].strip()
                elif line.startswith('data:'):
                    event_data = line.split(':', 1)[1].strip()
                else:
                    continue
            else:
                # Linha vazia indica fim do evento
                if event_type and event_data:
                    try:
                        data = json.loads(event_data)
                        event_queue.put({
                            'type': event_type,
                            'data': data
                        })
                        
                        # Para se evento de desconexão
                        if event_type == 'disconnected':
                            break
                            
                    except json.JSONDecodeError as e:
                        print(f"Erro ao decodificar JSON: {e}")
                        print(f"Dados recebidos: {event_data}")
                    
                    # Reset para próximo evento
                    event_data = ""
                    event_type = None
                    
    except requests.exceptions.Timeout:
        event_queue.put({
            'type': 'error',
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

def process_event_queue():
    """Processa eventos da fila"""
    try:
        while not st.session_state.event_queue.empty():
            event = st.session_state.event_queue.get_nowait()
            st.session_state.real_time_events.append(event)
            
            # Atualiza progresso baseado no tipo de evento
            event_type = event.get('type', '')
            data = event.get('data', {})
            
            if event_type == 'start':
                st.session_state.research_progress = 10
            elif event_type == 'plan':
                # Incrementa progresso baseado na iteração
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
                
                # Extrai relatório final - CORREÇÃO: Captura o resultado de forma mais robusta
                if 'result' in data:
                    st.session_state.final_report = data.get('result', '')
                    # Adiciona um print para depuração
                    # print(f"Relatório capturado: {st.session_state.final_report[:100]}...")
            elif event_type == 'error':
                st.session_state.is_researching = False
            
            # Atualiza a mensagem de status para cada evento
            status_message = format_status_message(event)
            if status_message:
                st.session_state.current_task = status_message
                # Armazena também a última atualização para comparação
                st.session_state.last_status_update = status_message
                
    except queue.Empty:
        pass

def check_backend_status():
    """Verifica se o backend está funcionando (com cache)"""
    # Usa cache do Streamlit para evitar múltiplas chamadas
    current_time = time.time()
    
    # Verifica se já temos um status recente (últimos 30 segundos)
    if 'backend_check_time' in st.session_state and 'backend_status' in st.session_state:
        if current_time - st.session_state.backend_check_time < 30:
            return st.session_state.backend_status
    
    # Faz nova verificação
    try:
        response = requests.get("http://localhost:8000/health", timeout=3)
        status = response.status_code == 200
        # Armazena no session state
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
        # Prepara dados da requisição
        request_data = {
            "query": query,
            "llm_provider": llm_provider,
            "max_iterations": max_iterations
        }
        
        if api_key:
            request_data["api_key"] = api_key
        if model_name:
            request_data["model_name"] = model_name
        
        # Inicia pesquisa
        response = requests.post(
            "http://localhost:8000/research", 
            json=request_data,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            research_id = result.get("research_id")
            
            # Inicia thread SSE
            st.session_state.research_id = research_id
            st.session_state.event_queue = queue.Queue()
            st.session_state.sse_thread = Thread(
                target=sse_consumer, 
                args=(research_id, st.session_state.event_queue)
            )
            st.session_state.sse_thread.daemon = True  # Corrigido: Thread daemon para finalizar com o programa principal
            st.session_state.sse_thread.start()
            
            return True
        else:
            st.error(f"Erro ao iniciar pesquisa: {response.status_code}")
            return False
            
    except Exception as e:
        st.error(f"Erro ao conectar com backend: {str(e)}")
        return False

def main():
    initialize_session_state()
    
    # Header principal
    st.markdown("""
    <div class="main-header">
        <h1>🔍 Deep Research Service</h1>
        <p>Pesquisa Profunda com IA </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Sidebar para configurações
    with st.sidebar:
        st.header("⚙️ Configurações")
        
        # Status do backend (com verificação manual)
        backend_status = check_backend_status()
        if backend_status:
            st.success("✅ Backend conectado")
        else:
            st.error("❌ Backend desconectado")
            st.info("Execute: `cd backend && uvicorn main:app --reload`")
        
        st.divider()
        
        # Seleção do provedor LLM
        llm_provider = st.selectbox(
            "🤖 Provedor LLM",
            ["openai", "ollama"],
            help="Escolha entre OpenAI ou Ollama local"
        )
        
        # Configurações específicas do provedor
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
        else:  # ollama
            model_name = st.text_input(
                "🦙 Modelo Ollama",
                value="qwen2.5:7b",
                help="Nome do modelo Ollama (ex: llama2, mistral, codellama)"
            )
            st.info("⚠️ Certifique-se que o Ollama está rodando em localhost:11434")
        
        st.divider()
        
        # Configurações avançadas
        st.subheader("🛠️ Configurações Avançadas")
        max_iterations = st.slider(
            "Máximo de iterações",
            min_value=1,
            max_value=10,
            value=5,
            help="Número máximo de ciclos de pesquisa"
        )

    # Layout principal com duas colunas
    colPrincipal, colAndamento = st.columns([2, 1])
    
    # Coluna Principal
    with colPrincipal:
        # Interface principal
        st.header("🔍 Nova Pesquisa")
        
        # Campo de consulta
        query = st.text_area(
            "Digite sua consulta de pesquisa:",
            height=120,
            placeholder="Ex: Quais são as principais tendências em inteligência artificial para 2024? Como essas tecnologias estão impactando diferentes setores da economia?",
            help="Seja específico e detalhado para obter melhores resultados"
        )
        
        # Validação e botões
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
                # Limpa estado anterior
                st.session_state.real_time_events = []
                st.session_state.final_report = ""
                st.session_state.is_researching = True
                st.session_state.research_progress = 0
                st.session_state.current_task = "Iniciando pesquisa..."
                st.session_state.last_status_update = ""
                
                # Inicia pesquisa
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
                    st.rerun()
        
        
    
    # Coluna de Andamento - CORREÇÃO: Garantindo que os eventos são exibidos
    with colAndamento:
        # Título sempre visível
        st.header("📊 Progresso da Pesquisa")
        
        # Processa eventos da fila
        process_event_queue()
        
        # Exibe a contagem de eventos para depuração
        # st.caption(f"Eventos recebidos: {len(st.session_state.real_time_events)}")
        
        # Verifica se há eventos para mostrar
        if st.session_state.is_researching or st.session_state.real_time_events:
            
            # Componente de status (indicador visual do estado atual)
            status_label = st.session_state.current_task if st.session_state.current_task else "🔄 Aguardando atualizações..."
            status_state = "running" if st.session_state.is_researching else "complete"
            
            # Status principal sempre expandido
            with st.status(status_label, state=status_state, expanded=False) as status:
                if not st.session_state.real_time_events:
                    status.write("⏳ Aguardando os primeiros eventos...")
                
                # Exibe eventos em ordem cronológica inversa (mais recentes primeiro)
                for event in reversed(st.session_state.real_time_events):
                    event_type = event.get('type', '')
                    data = event.get('data', {})
                    message = data.get('message', '')
                    details = data.get('details', {})
                    
                    # Ícones para os tipos de eventos
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
                        'pipeline_start': '⚙️',
                        'decision': '🤔',
                        'connected': '🔗',
                        'disconnected': '🔌'
                    }
                    
                    icon = icons.get(event_type, '📌')
                    
                    # Formatação baseada no tipo de evento
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
                        status.write(f"{icon} **Busca concluída:** {count} resultados encontrados")
                    elif event_type == 'analyze_complete':
                        iteration = details.get('iteration', 0)
                        status.write(f"{icon} **Análise concluída** - Iteração {iteration}")
                    elif event_type == 'report':
                        total = details.get('total_results', 0)
                        status.write(f"{icon} **Gerando relatório final** ({total} resultados processados)")
                    elif event_type == 'complete':
                        status.write(f"{icon} **Pesquisa concluída com sucesso!**")
                    elif event_type == 'error':
                        status.write(f"{icon} **Erro:** {message}")
                    elif event_type == 'decision':
                        status.write(f"{icon} {message}")
                    else:
                        status.write(f"{icon} {message}")
                    
                    # Detalhes extras para certos tipos de eventos
                    if event_type == 'analyze_complete' and 'insights_preview' in details:
                        status.write(f"💡 **Preview:** {details['insights_preview'][:150]}...")
                    
                    elif event_type == 'report_complete' and 'word_count' in details:
                        word_count = details.get('word_count', 0)
                        status.write(f"📊 Relatório gerado com {word_count} palavras")
        else:
            # Mensagem quando não há pesquisa em andamento
            st.info("Inicie uma nova pesquisa para visualizar o progresso em tempo real.")
        
        # Exibe o status do relatório final para depuração
        if st.session_state.final_report:
            st.success("✅ Relatório final disponível!")

    # Relatório final - CORREÇÃO: Simplificado para garantir exibição
    if st.session_state.final_report:
        st.header("###📋 Relatório Final")
        
        # Usando markdown para exibir o texto com formatação
        st.markdown(f"<div class='research-container'><div class='report-content'>{st.session_state.final_report}</div></div>", unsafe_allow_html=True)

        
        # Botões de ação
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.download_button(
                label="📥 Baixar Relatório (TXT)",
                data=st.session_state.final_report,
                file_name=f"research_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain"
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
                st.rerun()
        
    # Auto-refresh para atualizar eventos enquanto pesquisa está em andamento
    if st.session_state.is_researching:
        time.sleep(0.5)  # Pausa curta
        st.rerun()
    
    # Rodapé
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #666; padding: 20px;'>
        <p><strong>🔍 Deep Research Service v2.0</strong></p>
        <p>🚀 Powered by Eduardo Gobatti </p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()