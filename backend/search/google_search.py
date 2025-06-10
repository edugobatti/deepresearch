from typing import Dict, List
from googlesearch import search
import requests
from bs4 import BeautifulSoup
import re

try:
    from newspaper import Article
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False
    print("Biblioteca newspaper3k não encontrada. Para melhor extração de conteúdo, instale com: pip install newspaper3k")

from langchain_core.tools import tool

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
                    "snippet": f"Resultado {i+1} para '{query}'",
                    "source_type": "google"
                })
            except:
                results.append({
                    "title": url,
                    "url": url,
                    "snippet": f"Resultado {i+1} para '{query}'",
                    "source_type": "google"
                })
        
        return results
    except Exception as e:
        return [{"error": f"Erro na busca Google: {str(e)}", "source_type": "google"}]

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
        # Casos especiais redirecionados para módulos específicos
        if 'arxiv.org' in url:
            try:
                # Importação dinâmica para evitar referências circulares
                from arxiv_search import extract_arxiv_content
                return extract_arxiv_content(url, timeout, headers)
            except ImportError:
                print("Módulo arxiv_search não disponível, usando extração genérica")
        
        if 'wikipedia.org' in url:
            try:
                # Importação dinâmica para evitar referências circulares
                from backend.search.wikipedia_search import extract_wikipedia_content
                return extract_wikipedia_content(url, timeout, headers)
            except ImportError:
                print("Módulo wikipedia_search não disponível, usando extração genérica")
            
        # Para outros sites, tenta newspaper3k primeiro
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
        
        # Remove todos os elementos não textuais
        for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'button', 'meta', 'link', 'noscript']):
            element.decompose()
        
        # Extração do título
        title = url
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text().strip()
        
        main_content = ""
        
        # Seletores para identificar o conteúdo principal
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
        
        # Tenta extrair de parágrafos se o conteúdo principal não foi encontrado
        if len(main_content) < 1000:
            paragraphs = soup.find_all('p')
            substantial_paragraphs = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20]
            if substantial_paragraphs:
                main_content = ' '.join(substantial_paragraphs)
        
        # Último recurso: extrai todo o texto do corpo
        if len(main_content) < 1000:
            body = soup.find('body')
            if body:
                main_content = body.get_text(separator=' ', strip=True)
        
        # Limpa espaços extras
        main_content = re.sub(r'\s+', ' ', main_content).strip()
          
        # Limita o tamanho do conteúdo para processamento
        max_content_length = 20000  
        if len(main_content) > max_content_length:
            main_content = main_content[:max_content_length] + "... [conteúdo truncado]"
        
        # Log do conteúdo extraído
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