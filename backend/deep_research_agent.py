from typing import Dict, Any, Optional, List, TypedDict, Callable
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from datetime import datetime
import asyncio
import re

try:
    from langchain_ollama import OllamaLLM
except ImportError:
    from langchain_community.llms import Ollama as OllamaLLM

# Importações das funções de busca
from search.google_search import execute_google_search, extract_web_content
from search.arxiv_search import execute_arxiv_search
from search.wikipedia_search import execute_wikipedia_search

class ResearchState(TypedDict):
    query: str
    search_results: List[Dict]
    site_summaries: List[Dict]  # Lista para armazenar resumos por site
    analysis: str
    final_report: str
    iteration: int
    max_iterations: int
    search_queries: List[str]
    sources: List[str]
    current_search_query: str
    search_mode: str  # Campo para controlar o tipo de busca (google, arxiv, wikipedia)

def execute_search(query: str, search_mode: str = "google", num_results: int = 5) -> List[Dict]:
    """Função helper para executar busca de acordo com o modo selecionado"""
    if search_mode == "arxiv":
        return execute_arxiv_search(query, num_results)
    elif search_mode == "wikipedia":
        return execute_wikipedia_search(query, num_results)
    else:  # default: google
        return execute_google_search(query, num_results)

class DeepResearchAgent:
    def __init__(self, llm_provider: str, api_key: Optional[str] = None, 
                 model_name: Optional[str] = None, status_callback: Optional[Callable[[str, str, Any], None]] = None):
        
        self.status_callback = status_callback
        
        if llm_provider == "openai":
            if not api_key:
                raise ValueError("API key necessária para OpenAI")
            self.llm = ChatOpenAI(
                openai_api_key=api_key,
                model=model_name or "gpt-3.5-turbo",
                temperature=0.7
            )
            self.is_chat_model = True
        elif llm_provider == "ollama":
            self.llm = OllamaLLM(
                model=model_name or "llama2",
                base_url="http://localhost:11434"
            )
            self.is_chat_model = False
        else:
            raise ValueError("Provider deve ser 'openai' ou 'ollama'")
        
        # Configura grafo
        self.setup_graph()
    
    def log_status(self, message: str, status_type: str = "info", data: Any = None):
        """Log com callback opcional"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"[{timestamp}] {message}")
        
        if self.status_callback:
            self.status_callback(status_type, message, data)
    
    def extract_content(self, response) -> str:
        """Extrai conteúdo da resposta do LLM de forma robusta"""
        if hasattr(response, 'content'):
            return response.content
        elif isinstance(response, str):
            return response
        else:
            return str(response)
    
    async def invoke_llm(self, prompt: str) -> str:
        """Invoca LLM de forma consistente"""
        try:
            if self.is_chat_model:
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            else:
                response = await self.llm.ainvoke(prompt)
            
            return self.extract_content(response)
        except Exception as e:
            self.log_status(f"Erro ao invocar LLM: {str(e)}", "error")
            raise e
    
    def setup_graph(self):
        """Configura o grafo LangGraph"""
        workflow = StateGraph(ResearchState)
        
        # Adiciona nós
        workflow.add_node("plan_search", self.plan_search)
        workflow.add_node("execute_search", self.execute_search)
        workflow.add_node("summarize_sites", self.summarize_sites)  # Nó para resumir sites individualmente
        workflow.add_node("analyze_results", self.analyze_results)
        workflow.add_node("generate_report", self.generate_report)
        
        # Define fluxo
        workflow.add_edge(START, "plan_search")
        workflow.add_edge("plan_search", "execute_search")
        workflow.add_edge("execute_search", "summarize_sites")  # Nova etapa no fluxo
        workflow.add_edge("summarize_sites", "analyze_results")
        workflow.add_conditional_edges(
            "analyze_results",
            self.should_continue,
            {
                "continue": "plan_search",
                "finish": "generate_report"
            }
        )
        workflow.add_edge("generate_report", END)
        
        self.graph = workflow.compile()
    
    async def plan_search(self, state: ResearchState) -> ResearchState:
        """Planeja próxima busca, definindo a query e o modo de busca (Google, arXiv, Wikipedia)"""
        self.log_status("Planejando próxima busca...", "plan")
        
        # Controle da alternância de fontes de busca
        iteration = state.get("iteration", 0)
        
        # Define a fonte de busca para esta iteração
        # Prioriza Google e arXiv sobre Wikipedia
        if iteration == 0:
            # Primeira iteração: busca normal no Google
            search_mode = "google"
            search_query = state["query"]
        elif iteration == 1:
            # Segunda iteração: busca no arXiv para conteúdo acadêmico
            search_mode = "arxiv"
            search_query = state["query"]
        elif iteration == 2:
            # Terceira iteração: busca na Wikipedia para contexto geral
            search_mode = "wikipedia"
            search_query = state["query"]
        else:
            # Iterações seguintes: alterna entre fontes priorizadas e refina a busca
            # Gera uma query refinada com base na análise anterior
            self.log_status(f"Gerando nova query baseada na análise anterior (Iteração {iteration})", "plan")
            
            prompt = f"""
                    A partir da consulta original: "{state['query']}"
                    E com base na análise realizada anteriormente: "{state.get('analysis', '')}"
                    
                    Gere uma nova **query de busca específica e refinada** que permita obter **informações complementares** e **ainda não exploradas**.
                    
                    Evite repetir elementos já investigados e foque em aspectos novos, mais profundos ou secundários que possam enriquecer a compreensão do tema.
                    
                    Retorne **apenas a nova query de busca**, sem explicações ou comentários.
                    """
            
            search_query = await self.invoke_llm(prompt)
            search_query = search_query.strip()
            
            # Alterna entre os modos de busca, priorizando Google e arXiv (2:1:1 ratio)
            search_mode_options = ["google", "arxiv", "google", "wikipedia"]
            search_mode = search_mode_options[iteration % 4]
        
        # Armazena o modo de busca e a query
        state["search_mode"] = search_mode
        state["current_search_query"] = search_query
        
        # Registra as queries utilizadas
        search_queries = state.get("search_queries", [])
        search_queries.append(f"{search_mode}: {search_query}")
        state["search_queries"] = search_queries
        
        self.log_status(f"Query planejada: {search_query} (Modo: {search_mode})", "plan", {
            "query": search_query,
            "mode": search_mode
        })
        
        return state
    
    async def execute_search(self, state: ResearchState) -> ResearchState:
        """Executa busca na fonte selecionada e extrai conteúdo das páginas"""
        query = state["current_search_query"]
        search_mode = state.get("search_mode", "google")
        
        self.log_status(f"Executando busca: {query} (Modo: {search_mode})", "search", {
            "query": query,
            "mode": search_mode
        })
        
        # Executa busca na fonte apropriada
        search_results = execute_search(query, search_mode=search_mode, num_results=5)
        
        # Filtra resultados inválidos
        valid_results = [r for r in search_results if not r.get('error')]
        self.log_status(f"Encontrados {len(valid_results)} resultados válidos em {search_mode}", "search", {
            "count": len(valid_results),
            "urls": [r.get('url', '') for r in valid_results[:3]],
            "source": search_mode
        })
        
        # Extrai conteúdo de cada resultado
        enriched_results = []
        for result in valid_results:
            url = result.get('url', '')
            if url:
                self.log_status(f"Extraindo conteúdo de: {url} ({search_mode})", "extract")
                
                # Extrai o conteúdo da página
                content_data = extract_web_content(url)
                
                # Adiciona resultado com conteúdo extraído
                enriched_results.append({
                    "title": content_data.get("title", result.get("title", "")),
                    "url": url,
                    "snippet": result.get("snippet", ""),
                    "content": content_data.get("content", ""),
                    "error": content_data.get("error", False),
                    "source_type": result.get("source_type", search_mode)
                })
                
                # Log do conteúdo extraído
                content_length = len(content_data.get("content", ""))
                self.log_status(f"Extraídos {content_length} caracteres de {url}", "extract_detail", {
                    "url": url,
                    "length": content_length,
                    "status": "ok" if content_length > 1000 else "baixo",
                    "source": search_mode
                })
        
        all_search_results = state.get("search_results", [])
        all_search_results.extend(enriched_results)
        state["search_results"] = all_search_results
        
        self.log_status(f"Busca {search_mode} concluída: {len(enriched_results)} páginas extraídas", "search_complete")
        
        return state
    
    async def summarize_sites(self, state: ResearchState) -> ResearchState:
        """Gera resumos individuais para cada site com tópicos"""
        self.log_status("Iniciando geração de resumos por site...", "site_summary")
        
        # Obtém os resultados da busca atual
        recent_results = state["search_results"][-5:]  # Últimos 5 resultados
        site_summaries = state.get("site_summaries", [])
        search_mode = state.get("search_mode", "google")
        
        for i, result in enumerate(recent_results):
            if result.get('error', True) or not result.get('content'):
                continue
                
            url = result.get('url', '')
            title = result.get('title', '')
            content = result.get('content', '')
            source_type = result.get('source_type', 'site')
            
            # Limita o conteúdo para processamento eficiente
            if len(content) > 8000:
                content = content[:8000] + "... [conteúdo truncado]"
            
            self.log_status(f"Gerando resumo para: {title} ({source_type})", "site_summary")
            
            # Adapta o prompt baseado no tipo de fonte
            if source_type == "arxiv":
                prompt = f"""
                    Analise o seguinte conteúdo extraído do artigo científico do arXiv "{title}" ({url}):
                    
                    {content}
                    
                    Crie um resumo estruturado seguindo estas diretrizes:
                    
                    1. **Identifique os principais tópicos acadêmicos** abordados no artigo.
                    2. **Para cada tópico**, forneça um título claro e uma descrição técnica concisa.
                    3. **Crie um resumo científico** do conteúdo completo, com 400 a 1000 palavras.
                    4. **Extraia quaisquer resultados, métodos ou conclusões importantes** encontrados no artigo.
                    5. **Destaque a relevância acadêmica** deste artigo para o tema da pesquisa.
                    
                    Formato esperado:
                    
                    ## Tópicos Principais
                    
                    ### [Tópico 1]
                    [Descrição técnica]
                    
                    ### [Tópico 2]
                    [Descrição técnica]
                    
                    ...
                    
                    ## Resumo Científico (300-600 palavras)
                    
                    [Seu resumo aqui]
                    
                    ## Dados e Resultados Relevantes
                    
                    - [Resultado/método 1]
                    - [Resultado/método 2]
                    ...
                    """
            elif source_type == "wikipedia":
                prompt = f"""
                    Analise o seguinte conteúdo extraído do artigo da Wikipedia "{title}" ({url}):
                    
                    {content}
                    
                    Crie um resumo estruturado seguindo estas diretrizes:
                    
                    1. **Identifique de 3 a 5 tópicos principais** abordados no artigo.
                    2. **Para cada tópico**, forneça um título claro e um resumo informativo.
                    3. **Crie um resumo enciclopédico** do conteúdo completo, com 300 a 600 palavras.
                    4. **Extraia fatos, datas, definições e informações contextuais** importantes.
                    5. **Destaque as conexões com outros tópicos relevantes** mencionados no artigo.
                    
                    Formato esperado:
                    
                    ## Tópicos Principais
                    
                    ### [Tópico 1]
                    [Resumo informativo]
                    
                    ### [Tópico 2]
                    [Resumo informativo]
                    
                    ...
                    
                    ## Resumo Enciclopédico (300-600 palavras)
                    
                    [Seu resumo aqui]
                    
                    ## Fatos e Informações Importantes
                    
                    - [Fato/definição 1]
                    - [Fato/definição 2]
                    ...
                    """
            else:  # google ou outro
                prompt = f"""
                    Analise o seguinte conteúdo extraído do site "{title}" ({url}):
                    
                    {content}
                    
                    Crie um resumo estruturado seguindo estas diretrizes:
                    
                    1. **Identifique de 3 a 5 tópicos principais** abordados no conteúdo.
                    2. **Para cada tópico**, forneça um título claro e uma breve descrição.
                    3. **Crie um resumo abrangente** do conteúdo completo, com 300 a 600 palavras.
                    4. **Extraia quaisquer fatos, estatísticas ou dados relevantes** encontrados no conteúdo.
                    
                    Formato esperado:
                    
                    ## Tópicos Principais
                    
                    ### [Tópico 1]
                    [Breve descrição]
                    
                    ### [Tópico 2]
                    [Breve descrição]
                    
                    ...
                    
                    ## Resumo Geral (300-600 palavras)
                    
                    [Seu resumo completo aqui]
                    
                    ## Dados Relevantes
                    
                    - [Fato/estatística 1]
                    - [Fato/estatística 2]
                    ...
                    """
            
            try:
                summary_content = await self.invoke_llm(prompt)
                
                site_summaries.append({
                    "title": title,
                    "url": url,
                    "summary": summary_content,
                    "source_type": source_type
                })
                
                self.log_status(f"Resumo gerado para: {title} ({source_type})", "site_summary_complete", {
                    "url": url,
                    "length": len(summary_content),
                    "source_type": source_type
                })
            except Exception as e:
                self.log_status(f"Erro ao gerar resumo para {url}: {str(e)}", "error")
        
        state["site_summaries"] = site_summaries
        self.log_status(f"Concluída geração de resumos para {len(site_summaries)} fontes ({search_mode})", "site_summaries_complete")
        
        return state
    
    async def analyze_results(self, state: ResearchState) -> ResearchState:
        """Analisa resumos dos sites para gerar análise consolidada"""
        iteration = state.get("iteration", 0) + 1
        self.log_status(f"Analisando resultados (Iteração {iteration}/{state.get('max_iterations', 5)})", "analyze")
        
        # Obtém os resumos de sites mais recentes
        recent_summaries = state.get("site_summaries", [])[-5:]
        search_mode = state.get("search_mode", "google")
        
        # Agrupa resumos por tipo de fonte
        google_summaries = [s for s in recent_summaries if s.get('source_type') == 'google']
        arxiv_summaries = [s for s in recent_summaries if s.get('source_type') == 'arxiv']
        wikipedia_summaries = [s for s in recent_summaries if s.get('source_type') == 'wikipedia']
        
        # Prepara texto consolidado de resumos por tipo
        summaries_text = ""
        
        if google_summaries:
            summaries_text += "\n=== RESUMOS DE SITES GERAIS ===\n"
            for i, summary in enumerate(google_summaries):
                summaries_text += f"\n--- RESUMO DO SITE {i+1} ---\n"
                summaries_text += f"Título: {summary.get('title', '')}\n"
                summaries_text += f"URL: {summary.get('url', '')}\n"
                summaries_text += f"Conteúdo:\n{summary.get('summary', '')}\n\n"
        
        if wikipedia_summaries:
            summaries_text += "\n=== RESUMOS DA WIKIPEDIA ===\n"
            for i, summary in enumerate(wikipedia_summaries):
                summaries_text += f"\n--- RESUMO WIKIPEDIA {i+1} ---\n"
                summaries_text += f"Título: {summary.get('title', '')}\n"
                summaries_text += f"URL: {summary.get('url', '')}\n"
                summaries_text += f"Conteúdo:\n{summary.get('summary', '')}\n\n"
        
        if arxiv_summaries:
            summaries_text += "\n=== RESUMOS DE ARTIGOS CIENTÍFICOS ===\n"
            for i, summary in enumerate(arxiv_summaries):
                summaries_text += f"\n--- RESUMO ARTIGO {i+1} ---\n"
                summaries_text += f"Título: {summary.get('title', '')}\n"
                summaries_text += f"URL: {summary.get('url', '')}\n"
                summaries_text += f"Conteúdo:\n{summary.get('summary', '')}\n\n"
        
        # Se não há resumos categorizados, usa o formato antigo
        if not summaries_text:
            for i, summary in enumerate(recent_summaries):
                summaries_text += f"\n--- RESUMO {i+1} ---\n"
                summaries_text += f"Título: {summary.get('title', '')}\n"
                summaries_text += f"URL: {summary.get('url', '')}\n"
                summaries_text += f"Conteúdo:\n{summary.get('summary', '')}\n\n"
        
        prompt = f"""
            Consulta original: "{state['query']}"
            
            Resumos das fontes analisadas:
            {summaries_text}
            
            Análise anterior: "{state.get('analysis', 'Nenhuma análise anterior')}"
            
            Com base nos resumos das diferentes fontes, realize as seguintes tarefas:
            
            1. **Consolidação das Informações por Tipo de Fonte**  
               - Identifique e consolide as principais informações das diferentes fontes.
               - Destaque os pontos de vista específicos de cada tipo de fonte (geral, enciclopédica, acadêmica).
               - Organize em **tópicos** com um **resumo detalhado** para cada um.
            
            2. **Identificação de Lacunas de Informação**  
               - Aponte quais aspectos da query original **ainda não foram respondidos** ou **estão insuficientemente explorados**.
               - Sugira o que ainda precisa ser pesquisado e em quais fontes.
            
            3. **Síntese Comparativa dos Insights**  
               - Apresente um **resumo comparando os principais aprendizados e fatos** encontrados nas diferentes fontes.
               - Destaque pontos de concordância e discordância entre as fontes.
               - Identifique a complementaridade entre as informações das diferentes fontes.
            
            Seja detalhado, baseando-se nas informações reais encontradas nos resumos.
            """
        
        self.log_status("Processando análise consolidada com LLM...", "analyze")
        analysis_content = await self.invoke_llm(prompt)
        
        # Atualiza análise
        current_analysis = state.get("analysis", "")
        state["analysis"] = current_analysis + "\n\n" + analysis_content
        state["iteration"] = iteration
        
        # Log de preview da análise
        insights = analysis_content[:200] + "..." if len(analysis_content) > 200 else analysis_content
        
        self.log_status(f"Análise concluída - Iteração {iteration} ({search_mode})", "analyze_complete", {
            "iteration": iteration,
            "mode": search_mode,
            "insights_preview": insights
        })
        
        return state
    
    def should_continue(self, state: ResearchState) -> str:
        """Decide se deve continuar pesquisando"""
        current_iteration = state.get("iteration", 0)
        max_iterations = state.get("max_iterations", 5)
        
        if current_iteration >= max_iterations:
            self.log_status(f"Número máximo de iterações atingido ({max_iterations})", "decision")
            return "finish"
        
        analysis = state.get("analysis", "")
        if len(analysis) > 2000 and "informações suficientes" in analysis.lower():
            self.log_status("Informações suficientes coletadas", "decision")
            return "finish"
        
        self.log_status(f"Continuando pesquisa - Iteração {current_iteration + 1} de {max_iterations}", "decision")
        return "continue"
    
    async def generate_report(self, state: ResearchState) -> ResearchState:
        """Gera relatório final"""
        self.log_status("Gerando relatório final...", "report")
        
        total_results = len(state.get("search_results", []))
        total_queries = len(state.get("search_queries", []))
        total_summaries = len(state.get("site_summaries", []))
        
        # Agrupa resumos por tipo de fonte
        google_summaries = [s for s in state.get("site_summaries", []) if s.get('source_type') == 'google']
        arxiv_summaries = [s for s in state.get("site_summaries", []) if s.get('source_type') == 'arxiv']
        wikipedia_summaries = [s for s in state.get("site_summaries", []) if s.get('source_type') == 'wikipedia']
        
        # Formata fontes para o prompt, agrupadas por tipo
        sources = []
        site_summaries_text = ""
        
        # Processa fontes da Wikipedia
        if wikipedia_summaries:
            site_summaries_text += "\n=== RESUMOS DE FONTES ENCICLOPÉDICAS (WIKIPEDIA) ===\n"
            for i, summary in enumerate(wikipedia_summaries):
                if summary.get("url") and summary.get("title"):
                    sources.append({
                        "title": summary.get("title", ""),
                        "url": summary.get("url", ""),
                        "type": "wikipedia"
                    })
                    
                    site_summaries_text += f"\n--- RESUMO WIKIPEDIA {i+1} ---\n"
                    site_summaries_text += f"Título: {summary.get('title', '')}\n"
                    site_summaries_text += f"URL: {summary.get('url', '')}\n"
                    site_summaries_text += f"Resumo:\n{summary.get('summary', '')}\n\n"
        
        # Processa fontes acadêmicas (arXiv)
        if arxiv_summaries:
            site_summaries_text += "\n=== RESUMOS DE FONTES ACADÊMICAS (ARXIV) ===\n"
            for i, summary in enumerate(arxiv_summaries):
                if summary.get("url") and summary.get("title"):
                    sources.append({
                        "title": summary.get("title", ""),
                        "url": summary.get("url", ""),
                        "type": "arxiv"
                    })
                    
                    site_summaries_text += f"\n--- RESUMO ARTIGO {i+1} ---\n"
                    site_summaries_text += f"Título: {summary.get('title', '')}\n"
                    site_summaries_text += f"URL: {summary.get('url', '')}\n"
                    site_summaries_text += f"Resumo:\n{summary.get('summary', '')}\n\n"
        
        # Processa fontes gerais (Google)
        if google_summaries:
            site_summaries_text += "\n=== RESUMOS DE FONTES GERAIS ===\n"
            for i, summary in enumerate(google_summaries):
                if summary.get("url") and summary.get("title"):
                    sources.append({
                        "title": summary.get("title", ""),
                        "url": summary.get("url", ""),
                        "type": "google"
                    })
                    
                    site_summaries_text += f"\n--- RESUMO SITE {i+1} ---\n"
                    site_summaries_text += f"Título: {summary.get('title', '')}\n"
                    site_summaries_text += f"URL: {summary.get('url', '')}\n"
                    site_summaries_text += f"Resumo:\n{summary.get('summary', '')}\n\n"
        
        # Para fontes não categorizadas
        other_summaries = [s for s in state.get("site_summaries", []) if s.get('source_type') not in ['google', 'arxiv', 'wikipedia']]
        if other_summaries:
            site_summaries_text += "\n=== RESUMOS DE OUTRAS FONTES ===\n"
            for i, summary in enumerate(other_summaries):
                if summary.get("url") and summary.get("title"):
                    sources.append({
                        "title": summary.get("title", ""),
                        "url": summary.get("url", ""),
                        "type": "other"
                    })
                    
                    site_summaries_text += f"\n--- RESUMO FONTE {i+1} ---\n"
                    site_summaries_text += f"Título: {summary.get('title', '')}\n"
                    site_summaries_text += f"URL: {summary.get('url', '')}\n"
                    site_summaries_text += f"Resumo:\n{summary.get('summary', '')}\n\n"
        
        # Remove duplicatas mantendo a informação de tipo
        unique_sources = []
        seen_urls = set()
        for source in sources:
            if source["url"] not in seen_urls:
                unique_sources.append(source)
                seen_urls.add(source["url"])
        
        # Formata fontes para o prompt, agrupadas por tipo
        sources_by_type = {
            "wikipedia": [s for s in unique_sources if s.get("type") == "wikipedia"],
            "arxiv": [s for s in unique_sources if s.get("type") == "arxiv"],
            "google": [s for s in unique_sources if s.get("type") == "google"],
            "other": [s for s in unique_sources if s.get("type") == "other"]
        }
        
        sources_text = ""
        
        if sources_by_type["wikipedia"]:
            sources_text += "\n### Fontes Enciclopédicas (Wikipedia)\n"
            for s in sources_by_type["wikipedia"]:
                sources_text += f"- {s['title']} ({s['url']})\n"
        
        if sources_by_type["arxiv"]:
            sources_text += "\n### Fontes Acadêmicas (arXiv)\n"
            for s in sources_by_type["arxiv"]:
                sources_text += f"- {s['title']} ({s['url']})\n"
        
        if sources_by_type["google"]:
            sources_text += "\n### Fontes Gerais\n"
            for s in sources_by_type["google"]:
                sources_text += f"- {s['title']} ({s['url']})\n"
        
        if sources_by_type["other"]:
            sources_text += "\n### Outras Fontes\n"
            for s in sources_by_type["other"]:
                sources_text += f"- {s['title']} ({s['url']})\n"
        
        state["sources"] = sources_text
        
        self.log_status(f"Compilando dados de {total_results} resultados e {total_queries} buscas em {len(unique_sources)} fontes", "report", {
            "total_results": total_results,
            "total_queries": total_queries,
            "sources_count": len(unique_sources),
            "wikipedia_count": len(sources_by_type["wikipedia"]),
            "arxiv_count": len(sources_by_type["arxiv"]),
            "google_count": len(sources_by_type["google"])
        })
        
        prompt = f"""
            **Query original de pesquisa:**  
            "{state['query']}"
            
            **Queries utilizadas ao longo da investigação:**  
            {', '.join(state.get('search_queries', []))}
            
            **Resumos dos conteúdos analisados por tipo de fonte:**
            {site_summaries_text}
            
            **Análise consolidada dos resultados:**  
            {state.get('analysis', '')}
            
            **Fontes consultadas:**
            {sources_text}
            
            O relatório final deve seguir estas diretrizes:
            
            1. **Responder à query original de forma estruturada e abrangente**
            
            2. **Organizar o relatório em seções que integrem informações de diferentes tipos de fontes:**
               - Incluir uma seção de visão geral/contexto (baseada principalmente em fontes enciclopédicas)
               - Incluir uma seção de aspectos técnicos/científicos (baseada principalmente em fontes acadêmicas)
               - Incluir uma seção de aplicações práticas/informações atualizadas (baseada principalmente em fontes gerais)
            
            3. **Para cada seção/tópico principal:**
               - Fornecer um título claro
               - Apresentar um resumo conciso (400-700 palavras) que sintetize as informações relevantes
               - Citar apropriadamente as fontes utilizadas
               - Destacar dados específicos, estatísticas ou fatos relevantes
            
            4. **Concluir com uma síntese integrativa:**
               - Resumir os principais achados 
               - Identificar como as diferentes fontes se complementam
               - Apontar qualquer questão não resolvida ou que mereça investigação adicional
            
            5. **Incluir todas as referências organizadas por tipo de fonte**
            
            Produza um relatório profissional e abrangente que integre harmoniosamente as informações de todos os tipos de fontes.
            """
        
        self.log_status("Processando relatório final com LLM...", "report")
        report_content = await self.invoke_llm(prompt)
        state["final_report"] = report_content
        
        self.log_status("Relatório final gerado com sucesso!", "report_complete", {
            "report_length": len(report_content),
            "word_count": len(report_content.split())
        })
        
        return state
    
    async def research(self, query: str, max_iterations: int = 3) -> str:
        """Executa pesquisa completa"""
        self.log_status(f"Iniciando pesquisa: {query}", "start", {"query": query, "max_iterations": max_iterations})
        
        initial_state: ResearchState = {
            "query": query,
            "max_iterations": max_iterations,
            "iteration": 0,
            "search_results": [],
            "site_summaries": [],  # Lista para armazenar resumos de sites
            "search_queries": [],
            "analysis": "",
            "final_report": "",
            "sources": [],
            "current_search_query": "",
            "search_mode": "google"  # Modo inicial de busca
        }
        
        try:
            self.log_status("Executando pipeline de pesquisa...", "pipeline_start")
            final_state = await self.graph.ainvoke(initial_state)
            self.log_status("Pesquisa concluída com sucesso!", "complete")
            
            # Verifica explicitamente se o relatório foi gerado
            final_report = final_state.get("final_report", "")
            if not final_report:
                self.log_status("AVISO: Relatório final vazio, gerando resumo padrão", "warning")
                final_report = f"""
                # Relatório de Pesquisa: {query}
                
                *Não foi possível gerar um relatório detalhado. Resumo básico:*
                
                ## Fontes consultadas
                {final_state.get("sources", "Nenhuma fonte disponível")}
                
                ## Análise 
                {final_state.get("analysis", "Nenhuma análise disponível")}
                """
            
            return final_report
        except Exception as e:
            error_msg = f"Erro durante pesquisa: {str(e)}"
            self.log_status(error_msg, "error", {"error": str(e)})
            return error_msg