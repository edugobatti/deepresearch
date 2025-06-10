import time# main.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, AsyncGenerator, Any
import asyncio
from deep_research_agent import DeepResearchAgent
import uvicorn
import logging
import json
from datetime import datetime
import uuid
from collections import defaultdict

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Deep Research Service",
    description="Deep Research IA",
    version="2.0.0"
)

# Middleware CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Armazenamento em memória para eventos SSE
research_events = defaultdict(list)

class ResearchRequest(BaseModel):
    query: str
    llm_provider: str  # "openai" or "ollama"
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    max_iterations: int = 3

class ResearchResponse(BaseModel):
    result: str
    status: str = "completed"
    research_id: str

def create_sse_message(event_type: str, data: dict) -> str:
    """Cria mensagem SSE formatada"""
    # Garante que o JSON seja válido e em uma única linha
    json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    # Remove quebras de linha do JSON
    json_data = json_data.replace('\n', ' ').replace('\r', '')
    
    # Formato SSE padrão
    message = f"event: {event_type}\n"
    message += f"data: {json_data}\n\n"
    return message

async def event_stream(research_id: str) -> AsyncGenerator[str, None]:
    """Gera stream de eventos SSE"""
    logger.info(f"Cliente conectado ao stream: {research_id}")
    
    # Envia evento inicial de conexão
    yield create_sse_message("connected", {
        "message": "Conectado ao stream de eventos",
        "research_id": research_id,
        "timestamp": datetime.now().isoformat()
    })
    
    sent_index = 0
    max_wait_time = 300  # 5 minutos máximo
    start_time = time.time()
    
    while True:
        # Verifica timeout
        if time.time() - start_time > max_wait_time:
            yield create_sse_message("timeout", {
                "message": "Timeout do stream",
                "timestamp": datetime.now().isoformat()
            })
            break
            
        # Verifica se há novos eventos
        if research_id in research_events:
            events = research_events[research_id]
            
            # Envia apenas eventos novos
            while sent_index < len(events):
                event = events[sent_index]
                yield create_sse_message(event["type"], event["data"])
                sent_index += 1
                
                # Envia heartbeat para manter conexão viva
                if sent_index % 5 == 0:
                    yield ": heartbeat\n\n"
            
            # Verifica se a pesquisa foi concluída
            if events and events[-1]["type"] in ["complete", "error", "cancelled"]:
                logger.info(f"Pesquisa finalizada: {research_id}")
                break
        
        # Pequeno delay para não sobrecarregar
        await asyncio.sleep(0.1)
    
    # Evento final de desconexão
    yield create_sse_message("disconnected", {
        "message": "Stream finalizado",
        "timestamp": datetime.now().isoformat()
    })

@app.get("/research/stream/{research_id}")
async def research_stream(research_id: str, request: Request):
    """Endpoint SSE para streaming de eventos da pesquisa"""
    logger.info(f"Nova conexão SSE: {research_id}")
    
    async def event_generator():
        try:
            async for event in event_stream(research_id):
                if await request.is_disconnected():
                    logger.info(f"Cliente desconectado: {research_id}")
                    break
                yield event
        except Exception as e:
            logger.error(f"Erro no stream: {str(e)}")
            yield create_sse_message("error", {"error": str(e)})
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Desabilita buffering do Nginx
        }
    )

def status_callback_factory(research_id: str):
    """Cria callback para capturar status do agente"""
    def callback(status_type: str, message: str, data: Any = None):
        event = {
            "type": status_type,
            "data": {
                "message": message,
                "timestamp": datetime.now().isoformat(),
                "details": data or {}
            }
        }
        research_events[research_id].append(event)
        logger.info(f"[{research_id}] {status_type}: {message}")
    
    return callback

@app.post("/research", response_model=ResearchResponse)
async def research_endpoint(request: ResearchRequest):
    """Endpoint para iniciar pesquisa com streaming"""
    research_id = str(uuid.uuid4())
    logger.info(f"Nova pesquisa iniciada: {research_id} - {request.query[:50]}...")
    
    # Adiciona evento inicial
    research_events[research_id].append({
        "type": "init",
        "data": {
            "message": f"Pesquisa iniciada: {request.query}",
            "query": request.query,
            "timestamp": datetime.now().isoformat(),
            "config": {
                "provider": request.llm_provider,
                "model": request.model_name,
                "max_iterations": request.max_iterations
            }
        }
    })
    
    # Executa pesquisa em background
    asyncio.create_task(execute_research(research_id, request))
    
    return ResearchResponse(
        result="Pesquisa iniciada. Use o endpoint /research/stream/{research_id} para acompanhar o progresso.",
        status="processing",
        research_id=research_id
    )

async def execute_research(research_id: str, request: ResearchRequest):
    """Executa a pesquisa em background"""
    try:
        # Validações
        if not request.query.strip():
            raise ValueError("Query não pode estar vazia")
        
        if request.llm_provider == "openai" and not request.api_key:
            raise ValueError("API key necessária para OpenAI")
        
        if request.llm_provider not in ["openai", "ollama"]:
            raise ValueError("Provider deve ser 'openai' ou 'ollama'")
        
        # Cria instância do agente com callback
        agent = DeepResearchAgent(
            llm_provider=request.llm_provider,
            api_key=request.api_key,
            model_name=request.model_name,
            status_callback=status_callback_factory(research_id)
        )
        
        logger.info(f"[{research_id}] Agente criado, iniciando pesquisa...")
        
        # Executa pesquisa
        result = await agent.research(
            query=request.query,
            max_iterations=request.max_iterations
        )
        
        # Adiciona evento de conclusão
        research_events[research_id].append({
            "type": "complete",
            "data": {
                "message": "Pesquisa concluída com sucesso",
                "result": result,
                "timestamp": datetime.now().isoformat()
            }
        })
        
        logger.info(f"[{research_id}] Pesquisa concluída com sucesso")
        
    except Exception as e:
        error_msg = f"Erro durante pesquisa: {str(e)}"
        logger.error(f"[{research_id}] {error_msg}")
        
        # Adiciona evento de erro
        research_events[research_id].append({
            "type": "error",
            "data": {
                "message": error_msg,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        })

@app.get("/research/status/{research_id}")
async def get_research_status(research_id: str):
    """Obtém status atual da pesquisa"""
    if research_id not in research_events:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    
    events = research_events[research_id]
    if not events:
        return {"status": "not_started", "events": []}
    
    last_event = events[-1]
    status = "processing"
    
    if last_event["type"] == "complete":
        status = "completed"
    elif last_event["type"] == "error":
        status = "failed"
    
    return {
        "status": status,
        "total_events": len(events),
        "last_event": last_event,
        "events": events[-10:]  # Últimos 10 eventos
    }

@app.delete("/research/{research_id}")
async def cancel_research(research_id: str):
    """Cancela uma pesquisa em andamento"""
    if research_id not in research_events:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    
    # Adiciona evento de cancelamento
    research_events[research_id].append({
        "type": "cancelled",
        "data": {
            "message": "Pesquisa cancelada pelo usuário",
            "timestamp": datetime.now().isoformat()
        }
    })
    
    return {"message": "Pesquisa cancelada", "research_id": research_id}

@app.get("/health")
async def health_check():
    """Endpoint de verificação de saúde"""
    active_researches = len([k for k, v in research_events.items() 
                           if v and v[-1]["type"] not in ["complete", "error", "cancelled"]])
    
    return {
        "status": "healthy",
        "message": "Deep Research Service está funcionando",
        "version": "2.0.0",
        "active_researches": active_researches,
        "total_researches": len(research_events)
    }

@app.get("/")
async def root():
    """Endpoint raiz"""
    return {
        "message": "Deep Research Service API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "research": "POST /research",
            "stream": "GET /research/stream/{research_id}",
            "status": "GET /research/status/{research_id}",
            "cancel": "DELETE /research/{research_id}",
            "health": "GET /health"
        }
    }

if __name__ == "__main__":
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        log_level="info"
    )