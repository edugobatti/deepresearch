#arxiv_search.py
from typing import Dict, List
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from langchain_core.tools import tool

@tool
def arxiv_search_tool(query: str) -> List[Dict]:
    """Ferramenta para buscar papers no arXiv"""
    try:
        base_url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": 3,
            "sortBy": "relevance",
            "sortOrder": "descending"
        }
        
        response = requests.get(base_url, params=params, timeout=10)
        
        if response.status_code != 200:
            return [{"error": f"Erro na busca arXiv: Status {response.status_code}", "source_type": "arxiv"}]
        
        # Parse XML response
        root = ET.fromstring(response.content)
        
        # Define namespace
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        
        results = []
        entries = root.findall(".//atom:entry", ns)
        
        for entry in entries:
            title_elem = entry.find("atom:title", ns)
            summary_elem = entry.find("atom:summary", ns)
            link_elem = entry.find("./atom:link[@title='pdf']", ns)
            
            if title_elem is not None and link_elem is not None:
                title = title_elem.text.strip() if title_elem.text else "Sem título"
                summary = summary_elem.text.strip() if summary_elem is not None and summary_elem.text else "Sem resumo"
                url = link_elem.get("href", "").replace("pdf", "abs")  # Get abstract page instead of PDF
                
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": summary[:200] + "..." if len(summary) > 200 else summary,
                    "source_type": "arxiv"
                })
        
        return results
    except Exception as e:
        return [{"error": f"Erro na busca arXiv: {str(e)}", "source_type": "arxiv"}]

def execute_arxiv_search(query: str, num_results: int = 5) -> List[Dict]:
    """Função helper para executar busca no arXiv"""
    return arxiv_search_tool.invoke({"query": query})

def extract_arxiv_content(url: str, timeout: int, headers: Dict) -> Dict[str, str]:
    """Extrai conteúdo específico de páginas do arXiv"""
    try:
        # Converte URL do PDF para URL da página de resumo se necessário
        if url.endswith('.pdf'):
            url = url.replace('/pdf/', '/abs/').rstrip('.pdf')
        
        response = requests.get(url, timeout=timeout, headers=headers)
        
        if response.status_code != 200:
            return {
                "title": url,
                "url": url,
                "content": f"Erro ao acessar página arXiv: Status {response.status_code}",
                "error": True
            }
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extrai título
        title = "Artigo arXiv"
        title_elem = soup.select_one('.title')
        if title_elem:
            title = title_elem.get_text().replace('Title:', '').strip()
        
        # Extrai autores
        authors = []
        author_elems = soup.select('.authors a')
        for author in author_elems:
            authors.append(author.get_text().strip())
        
        # Extrai resumo
        abstract = ""
        abstract_elem = soup.select_one('.abstract')
        if abstract_elem:
            abstract = abstract_elem.get_text().replace('Abstract:', '').strip()
        
        # Extrai outras informações relevantes
        categories = []
        cat_elems = soup.select('.tablecell.subjects .arxiv-link')
        for cat in cat_elems:
            categories.append(cat.get_text().strip())
        
        date_submitted = ""
        date_elem = soup.select_one('.dateline')
        if date_elem:
            date_submitted = date_elem.get_text().strip()
        
        # Combina tudo em um conteúdo estruturado
        content = f"""
        Título: {title}
        
        Autores: {', '.join(authors)}
        
        Data: {date_submitted}
        
        Categorias: {', '.join(categories)}
        
        Resumo:
        {abstract}
        
        URL: {url}
        """
        
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
            "content": f"Erro ao processar página arXiv: {str(e)}",
            "error": True
        }