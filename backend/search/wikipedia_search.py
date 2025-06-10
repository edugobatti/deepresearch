#wikipedia_search.py
from typing import Dict, List
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse
from langchain_core.tools import tool

@tool
def wikipedia_search_tool(query: str) -> List[Dict]:
    """Ferramenta para buscar artigos na Wikipedia"""
    try:
        # URL de busca da API da Wikipedia
        search_url = "https://pt.wikipedia.org/w/api.php"
        search_params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1
        }
        
        search_response = requests.get(search_url, params=search_params, timeout=10)
        
        if search_response.status_code != 200:
            return [{"error": f"Erro na busca Wikipedia: Status {search_response.status_code}", "source_type": "wikipedia"}]
        
        search_data = search_response.json()
        search_results = search_data.get("query", {}).get("search", [])
        
        results = []
        for result in search_results:
            title = result.get("title", "")
            snippet = BeautifulSoup(result.get("snippet", ""), "html.parser").get_text()
            
            # Monta URL da página
            url = f"https://pt.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "source_type": "wikipedia"
            })
        
        return results
    except Exception as e:
        return [{"error": f"Erro na busca Wikipedia: {str(e)}", "source_type": "wikipedia"}]

def execute_wikipedia_search(query: str, num_results: int = 5) -> List[Dict]:
    """Função helper para executar busca na Wikipedia"""
    return wikipedia_search_tool.invoke({"query": query})

def extract_wikipedia_content(url: str, timeout: int, headers: Dict) -> Dict[str, str]:
    """Extrai conteúdo específico de páginas da Wikipedia"""
    try:
        response = requests.get(url, timeout=timeout, headers=headers)
        
        if response.status_code != 200:
            return {
                "title": url,
                "url": url,
                "content": f"Erro ao acessar página Wikipedia: Status {response.status_code}",
                "error": True
            }
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove elementos indesejados
        for element in soup.find_all(['script', 'style', 'footer', 'header', 'aside', 'iframe', 'nav']):
            element.decompose()
        
        # Extrai título
        title = "Artigo Wikipedia"
        title_elem = soup.select_one('#firstHeading')
        if title_elem:
            title = title_elem.get_text().strip()
        
        # Extrai conteúdo do artigo
        content = ""
        article = soup.select_one('#mw-content-text')
        if article:
            # Remove elementos de navegação, tabelas e outros elementos não relevantes
            for element in article.select('.navbox, .vertical-navbox, .infobox, .sidebar, table'):
                element.decompose()
            
            # Obtém todos os parágrafos e seções de conteúdo
            paragraphs = []
            
            # Adiciona parágrafos iniciais (antes do sumário)
            intro_content = ""
            for p in article.select('p'):
                if p.parent.get('id') != 'toc' and not p.select('.mw-empty-elt'):
                    intro_text = p.get_text().strip()
                    if intro_text:
                        paragraphs.append(intro_text)
            
            # Adiciona seções com seus títulos
            for section in article.select('h2, h3, h4'):
                section_title = section.get_text().strip()
                if 'Referências' in section_title or 'Ver também' in section_title or 'Bibliografia' in section_title:
                    continue
                
                # Remove numeração e editar
                section_title = re.sub(r'\[\w+\]', '', section_title).strip()
                
                if section_title:
                    paragraphs.append(f"\n## {section_title}\n")
                
                # Adiciona parágrafos desta seção
                next_node = section.next_sibling
                while next_node and next_node.name not in ['h2', 'h3', 'h4']:
                    if next_node.name == 'p':
                        para_text = next_node.get_text().strip()
                        if para_text:
                            paragraphs.append(para_text)
                    elif next_node.name == 'ul':
                        for li in next_node.select('li'):
                            li_text = li.get_text().strip()
                            if li_text:
                                paragraphs.append(f"- {li_text}")
                    
                    next_node = next_node.next_sibling
            
            content = "\n\n".join(paragraphs)
        
        # Limpa o conteúdo
        content = re.sub(r'\[\d+\]', '', content)  # Remove referências numéricas
        content = re.sub(r'\s+', ' ', content)     # Normaliza espaços
        
        # Limita tamanho
        max_content_length = 20000
        if len(content) > max_content_length:
            content = content[:max_content_length] + "... [conteúdo truncado]"
        
        return {
            "title": title,
            "url": url,
            "content": content,
            "error": False
        }
        
    except Exception as e:
        return {
            "title": url,
            "url": url,
            "content": f"Erro ao processar página Wikipedia: {str(e)}",
            "error": True
        }