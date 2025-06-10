# deep_research_agent.py
from typing import Dict, Any, Optional, List, Annotated, TypedDict, Callable
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from googlesearch import search
import asyncio
import json
import requests
from datetime import datetime
import re
from bs4 import BeautifulSoup


try:
    from newspaper import Article
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False
    print("Biblioteca newspaper3k não encontrada. Para melhor extração de conteúdo, instale com: pip install newspaper3k")


try:
    from langchain_ollama import OllamaLLM
except ImportError:

    from langchain_community.llms import Ollama as OllamaLLM

class ResearchState(TypedDict):
    query: str
    search_results: List[Dict]
    analysis: str
    final_report: str
    iteration: int
    max_iterations: int
    search_queries: List[str]
    sources: List[str]
    current_search_query: str

@tool
def google_search_tool(query: str) -> List[Dict]:
    """Ferramenta para buscar no Google usando googlesearch-python"""
    try:
        results = []
        # Busca com número fixo de resultados
        search_results = list(search(query, num_results=5, sleep_interval=1))
        
        for i, url in enumerate(search_results):
            try:
                results.append({
                    "title": url,  
                    "url": url,
                    "snippet": f"Resultado {i+1} para '{query}'"
                })
            except:
                results.append({
                    "title": url,
                    "url": url,
                    "snippet": f"Resultado {i+1} para '{query}'"
                })
        
        return results
    except Exception as e:
        return [{"error": f"Erro na busca: {str(e)}"}]

def execute_google_search(query: str, num_results: int = 5) -> List[Dict]:
    """Função helper para executar busca no Google"""
    return google_search_tool.invoke({"query": query})

def extract_web_content(url: str, timeout: int = 10) -> Dict[str, str]:
    """
    Extrai o conteúdo de uma página web, incluindo texto principal.
    
    Args:
        url: URL da página a ser acessada
        timeout: Tempo máximo de espera pela resposta
        
    Returns:
        Dicionário com título, URL e conteúdo extraído
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        if NEWSPAPER_AVAILABLE:
            try:
                article = Article(url)
                article.download()
                article.parse()
                
                if article.text and len(article.text) > 500:
                    return {
                        "title": article.title or url,
                        "url": url,
                        "content": article.text,
                        "error": False
                    }
            except Exception as e:
                print(f"Falha ao extrair com newspaper3k: {str(e)}. Tentando método alternativo.")

        response = requests.get(url, timeout=timeout, headers=headers)

        if response.status_code != 200:
            return {
                "title": url,
                "url": url,
                "content": f"Erro ao acessar página: Status {response.status_code}",
                "error": True
            }
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'button']):
            element.decompose()
        
        title = url
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text().strip()
        
        main_content = ""
        
        content_selectors = [
            'article', 'main', 'div[role="main"]', 
            'div[class*="content"]', 'div[id*="content"]',
            'div[class*="article"]', 'div[id*="article"]',
            'div[class*="post"]', 'div[id*="post"]',
            'div[class*="body"]', 'div[id*="body"]',
            'section[class*="content"]', 'section[id*="content"]'
        ]
        
        main_elements = []
        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                main_elements.extend(elements)
        
        if main_elements:
            substantial_elements = [elem for elem in main_elements if len(elem.get_text(strip=True)) > 100]
            if substantial_elements:
                main_content = max([elem.get_text(separator=' ', strip=True) for elem in substantial_elements], key=len)
        
        if len(main_content) < 1000:
            paragraphs = soup.find_all('p')
            substantial_paragraphs = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20]
            if substantial_paragraphs:
                main_content = ' '.join(substantial_paragraphs)
        
        if len(main_content) < 1000:
            body = soup.find('body')
            if body:
                main_content = body.get_text(separator=' ', strip=True)
        
        main_content = re.sub(r'\s+', ' ', main_content).strip()
          
        max_content_length = 20000  
        if len(main_content) > max_content_length:
            main_content = main_content[:max_content_length] + "... [conteúdo truncado]"
        
 
        content_length = len(main_content)
        print(f"Extraídos {content_length} caracteres de {url}")
        if content_length < 1000:
            print(f"AVISO: Pouco conteúdo extraído de {url}")
        
        return {
            "title": title,
            "url": url,
            "content": main_content,
            "error": False
        }
        
    except Exception as e:
        return {
            "title": url,
            "url": url,
            "content": f"Erro ao processar página: {str(e)}",
            "error": True
        }

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
        workflow.add_node("analyze_results", self.analyze_results)
        workflow.add_node("generate_report", self.generate_report)
        
        # Define fluxo
        workflow.add_edge(START, "plan_search")
        workflow.add_edge("plan_search", "execute_search")
        workflow.add_edge("execute_search", "analyze_results")
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
        """Planeja próxima busca"""
        self.log_status("Planejando próxima busca...", "plan")
        
        if state.get("iteration", 0) == 0:
            search_query = state["query"]
        else:
            self.log_status(f"Gerando nova query baseada na análise anterior (Iteração {state.get('iteration', 0) + 1})", "plan")
            
            prompt = f"""
                    A partir da consulta original: "{state['query']}"
                    E com base na análise realizada anteriormente: "{state.get('analysis', '')}"
                    
                    Gere uma nova **query de busca específica e refinada** que permita obter **informações complementares** e **ainda não exploradas**.
                    
                    Evite repetir elementos já investigados e foque em aspectos novos, mais profundos ou secundários que possam enriquecer a compreensão do tema.
                    
                    Retorne **apenas a nova query de busca**, sem explicações ou comentários.
                    """
            
            search_query = await self.invoke_llm(prompt)
            search_query = search_query.strip()
        
        state["current_search_query"] = search_query
        search_queries = state.get("search_queries", [])
        search_queries.append(search_query)
        state["search_queries"] = search_queries
        
        self.log_status(f"Query planejada: {search_query}", "plan", {"query": search_query})
        return state
    
    async def execute_search(self, state: ResearchState) -> ResearchState:
        """Executa busca no Google e extrai conteúdo das páginas"""
        query = state["current_search_query"]
        self.log_status(f"Executando busca: {query}", "search", {"query": query})
        
        search_results = execute_google_search(query, num_results=5)
        

        valid_results = [r for r in search_results if not r.get('error')]
        self.log_status(f"Encontrados {len(valid_results)} resultados válidos", "search", {
            "count": len(valid_results),
            "urls": [r.get('url', '') for r in valid_results[:3]]  
        })
        

        enriched_results = []
        for result in valid_results:
            url = result.get('url', '')
            if url:
                self.log_status(f"Extraindo conteúdo de: {url}", "extract")
                

                content_data = extract_web_content(url)
                

                enriched_results.append({
                    "title": content_data.get("title", result.get("title", "")),
                    "url": url,
                    "snippet": result.get("snippet", ""),
                    "content": content_data.get("content", ""),
                    "error": content_data.get("error", False)
                })
                

                content_length = len(content_data.get("content", ""))
                self.log_status(f"Extraídos {content_length} caracteres de {url}", "extract_detail", {
                    "url": url,
                    "length": content_length,
                    "status": "ok" if content_length > 1000 else "baixo"
                })
        
        all_search_results = state.get("search_results", [])
        all_search_results.extend(enriched_results)
        state["search_results"] = all_search_results
        
        self.log_status(f"Busca concluída: {len(enriched_results)} páginas extraídas", "search_complete")
        
        return state
    
    async def analyze_results(self, state: ResearchState) -> ResearchState:
        """Analisa resultados da busca incluindo conteúdo extraído"""
        iteration = state.get("iteration", 0) + 1
        self.log_status(f"Analisando resultados (Iteração {iteration}/{state.get('max_iterations', 5)})", "analyze")
        
        recent_results = state["search_results"][-5:] 
        

        results_summary = []
        for r in recent_results:
            if not r.get('error'):
                results_summary.append(r.get('title', '')[:50] + '...')
        
        self.log_status(f"Processando {len(results_summary)} resultados com conteúdo", "analyze", {
            "titles": results_summary[:3] 
        })
        

        results_text = ""
        for i, r in enumerate(recent_results):
            if not r.get('error'):

                content = r.get('content', '')

                if len(content) > 5000:
                    content = content[:5000] + "... [conteúdo truncado]"
                
                results_text += f"\n--- DOCUMENTO {i+1} ---\n"
                results_text += f"Título: {r.get('title', '')}\n"
                results_text += f"URL: {r.get('url', '')}\n"
                results_text += f"Conteúdo:\n{content}\n\n"
        
        prompt = f"""
            Consulta original: "{state['query']}"
            
            Resultados da busca:
            {results_text}
            
            Análise anterior: "{state.get('analysis', 'Nenhuma análise anterior')}"
            
            Com base nos conteúdos extraídos das páginas, realize as seguintes tarefas:
            
            1. **Extração de Informações Relevantes**  
               - Identifique e extraia as principais informações.  
               - Organize em **tópicos** com um **resumo detalhado** para cada um.
            
            2. **Identificação de Lacunas de Informação**  
               - Aponte quais aspectos da query original **ainda não foram respondidos** ou **estão insuficientemente explorados**.  
               - Sugira o que ainda precisa ser pesquisado.
            
            3. **Síntese dos Principais Insights**  
               - Apresente um **resumo com os principais aprendizados e fatos** encontrados nos conteúdos analisados.
               - Inclua dados específicos, estatísticas ou informações concretas encontradas nos textos.
            
            
            Seja detalhado, baseando-se nas informações reais encontradas nos conteúdos.
            """
        
        self.log_status("Processando análise com LLM...", "analyze")
        analysis_content = await self.invoke_llm(prompt)
        

        current_analysis = state.get("analysis", "")
        state["analysis"] = current_analysis + "\n\n" + analysis_content
        state["iteration"] = iteration
        

        insights = analysis_content[:200] + "..." if len(analysis_content) > 200 else analysis_content
        
        self.log_status(f"Análise concluída - Iteração {iteration}", "analyze_complete", {
            "iteration": iteration,
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
        

        sources = []
        for result in state.get("search_results", []):
            if not result.get("error", False) and result.get("url") and result.get("title"):
                sources.append({
                    "title": result.get("title", ""),
                    "url": result.get("url", "")
                })
        
        unique_sources = []
        seen_urls = set()
        for source in sources:
            if source["url"] not in seen_urls:
                unique_sources.append(source)
                seen_urls.add(source["url"])
        
        # Formata fontes para o prompt
        sources_text = "\n".join([f"- {s['title']} ({s['url']})" for s in unique_sources])
        state["sources"] = sources_text
        
        self.log_status(f"Compilando dados de {total_results} resultados e {total_queries} buscas", "report", {
            "total_results": total_results,
            "total_queries": total_queries,
            "sources_count": len(unique_sources)
        })
        
        prompt = f"""
            **Query original de pesquisa:**  
            "{state['query']}"
            
            **Queries utilizadas ao longo da investigação:**  
            {', '.join(state.get('search_queries', []))}
            
            **Análise consolidada dos resultados:**  
            {state.get('analysis', '')}
            
            **Fontes consultadas:**
            {sources_text}
            
            O conteúdo deve seguir estas diretrizes:
            
            1.  **Responder à query original de forma etruturada.  
            2.  **Incluir todos os insights relevantes** extraídos das análises e resultados anteriores.  
            3.  **Organizar o relatório em seções/tópicos** para facilitar a leitura e compreensão.
            4.  **Fazer um resumo sobre as informações colhidas por topico (entre 200 a 600 palavras)**  
            5.  **Citar as principais fontes** de informação utilizadas ao longo do texto.
            6.  **Apresentar dados concretos e específicos** encontrados durante a pesquisa.
            7.  **Concluir com um resumo ** dos principais achados e sua relevância.
            8.  **Sempre colocar os links de referencia**.
            """

        
        self.log_status("Processando relatório com LLM...", "report")
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
            "search_queries": [],
            "analysis": "",
            "final_report": "",
            "sources": [],
            "current_search_query": ""
        }
        
        try:
            self.log_status("Executando pipeline de pesquisa...", "pipeline_start")
            final_state = await self.graph.ainvoke(initial_state)
            self.log_status("Pesquisa concluída com sucesso!", "complete")
            return final_state.get("final_report", "Erro ao gerar relatório")
        except Exception as e:
            error_msg = f"Erro durante pesquisa: {str(e)}"
            self.log_status(error_msg, "error", {"error": str(e)})
            return error_msg