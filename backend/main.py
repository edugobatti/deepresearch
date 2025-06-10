# main.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, AsyncGenerator, Any, Dict
import asyncio
from deep_research_agent import DeepResearchAgent
import uvicorn
import logging
import json
from datetime import datetime
import uuid
from collections import defaultdict
import time


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Deep Research Service",
    description="Deep Research IA",
    version="2.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Armazenamento de eventos e estado das pesquisas
research_events = defaultdict(list)
research_tasks = {}  # Novo dicionário para armazenar estado e resultados das pesquisas

class ResearchRequest(BaseModel):
    query: str
    llm_provider: str 
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    max_iterations: int = 3

class ResearchResponse(BaseModel):
    result: str
    status: str = "processing"
    research_id: str

def create_sse_message(event_type: str, data: dict) -> str:
    """Cria mensagem SSE formatada"""
    json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    json_data = json_data.replace('\n', ' ').replace('\r', '')

    message = f"event: {event_type}\n"
    message += f"data: {json_data}\n\n"
    return message

async def event_stream(research_id: str) -> AsyncGenerator[str, None]:
    """Gera stream de eventos SSE"""
    logger.info(f"Cliente conectado ao stream: {research_id}")

    yield create_sse_message("connected", {
        "message": "Conectado ao stream de eventos",
        "research_id": research_id,
        "timestamp": datetime.now().isoformat()
    })
    
    sent_index = 0
    max_wait_time = 1800  # 30 minutos de timeout (aumentado de 5 para 30 minutos)
    start_time = time.time()
    last_heartbeat = time.time()
    
    while True:
        current_time = time.time()
        
        # Verifica timeout global
        if current_time - start_time > max_wait_time:
            yield create_sse_message("timeout", {
                "message": "Timeout do stream",
                "timestamp": datetime.now().isoformat()
            })
            
            # Verifica se temos resultado parcial para enviar
            if research_id in research_tasks and "partial_result" in research_tasks[research_id]:
                yield create_sse_message("complete", {
                    "message": "Pesquisa concluída parcialmente devido a timeout",
                    "result": research_tasks[research_id]["partial_result"],
                    "timestamp": datetime.now().isoformat()
                })
            
            break
        
        # Envia heartbeat a cada 15 segundos
        if current_time - last_heartbeat > 15:
            yield ": heartbeat\n\n"
            last_heartbeat = current_time

        # Verifica resultado disponível
        if research_id in research_tasks and research_tasks[research_id].get("status") == "completed":
            # Verifica se já enviamos evento de conclusão
            completion_sent = False
            for event in research_events[research_id]:
                if event["type"] == "complete":
                    completion_sent = True
                    break
                    
            # Se não enviamos, adiciona evento de conclusão com o resultado
            if not completion_sent and "result" in research_tasks[research_id]:
                result_event = {
                    "type": "complete",
                    "data": {
                        "message": "Pesquisa concluída com sucesso",
                        "result": research_tasks[research_id]["result"],
                        "timestamp": datetime.now().isoformat()
                    }
                }
                research_events[research_id].append(result_event)
        
        # Processa eventos pendentes
        if research_id in research_events:
            events = research_events[research_id]

            while sent_index < len(events):
                event = events[sent_index]
                yield create_sse_message(event["type"], event["data"])
                sent_index += 1
                
                # Heartbeat a cada 5 eventos
                if sent_index % 5 == 0:
                    yield ": heartbeat\n\n"
            
            # Verifica se temos eventos finais
            if events and events[-1]["type"] in ["complete", "error", "cancelled"]:
                logger.info(f"Pesquisa finalizada: {research_id}")
                break
        
        # Pequena pausa antes da próxima verificação
        await asyncio.sleep(0.1)
    
    yield create_sse_message("disconnected", {
        "message": "Stream finalizado",
        "timestamp": datetime.now().isoformat()
    })

@app.get("/research/stream/{research_id}")
async def research_stream(research_id: str, request: Request):
    """Endpoint SSE para streaming de eventos da pesquisa"""
    logger.info(f"Nova conexão SSE: {research_id}")
    
    # Verifica se a pesquisa existe
    if research_id not in research_events:
        return StreamingResponse(
            [create_sse_message("error", {
                "message": "Pesquisa não encontrada",
                "timestamp": datetime.now().isoformat()
            })],
            media_type="text/event-stream"
        )
    
    async def event_generator():
        try:
            async for event in event_stream(research_id):
                if await request.is_disconnected():
                    logger.info(f"Cliente desconectado: {research_id}")
                    break
                yield event
        except Exception as e:
            logger.error(f"Erro no stream: {str(e)}")
            yield create_sse_message("error", {
                "message": f"Erro no stream: {str(e)}",
                "timestamp": datetime.now().isoformat()
            })
    
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
        
        # Atualiza estado da pesquisa
        if research_id in research_tasks:
            research_tasks[research_id]["last_update"] = datetime.now().isoformat()
            research_tasks[research_id]["last_message"] = message
            
            # Captura a análise como resultado parcial quando disponível
            if status_type == "analyze_complete" and data and "insights_preview" in data:
                partial = data.get("insights_preview", "")
                if partial:
                    current_partial = research_tasks[research_id].get("partial_result", "")
                    if current_partial:
                        research_tasks[research_id]["partial_result"] = f"{current_partial}\n\n## Nova Análise\n\n{partial}"
                    else:
                        research_tasks[research_id]["partial_result"] = f"# Resultados Parciais\n\n{partial}"
            
            # Atualiza progresso
            if status_type == "start":
                research_tasks[research_id]["progress"] = 10
            elif status_type == "plan":
                research_tasks[research_id]["progress"] = min(research_tasks[research_id].get("progress", 0) + 5, 90)
            elif status_type == "search":
                research_tasks[research_id]["progress"] = min(research_tasks[research_id].get("progress", 0) + 5, 90)
            elif status_type == "analyze":
                research_tasks[research_id]["progress"] = min(research_tasks[research_id].get("progress", 0) + 10, 90)
            elif status_type == "report":
                research_tasks[research_id]["progress"] = 90
                
        logger.info(f"[{research_id}] {status_type}: {message}")
    
    return callback

@app.post("/research", response_model=ResearchResponse)
async def research_endpoint(request: ResearchRequest):
    """Endpoint para iniciar pesquisa com streaming"""
    research_id = str(uuid.uuid4())
    logger.info(f"Nova pesquisa iniciada: {research_id} - {request.query[:50]}...")
    
    # Inicializa o estado da pesquisa
    research_tasks[research_id] = {
        "status": "started",
        "query": request.query,
        "config": {
            "provider": request.llm_provider,
            "model": request.model_name,
            "max_iterations": request.max_iterations
        },
        "start_time": datetime.now().isoformat(),
        "progress": 0,
        "partial_result": ""
    }
    
    research_events[research_id].append({
        "type": "init",
        "data": {
            "message": f"Pesquisa iniciada: {request.query}",
            "query": request.query,
            "timestamp": datetime.now().isoformat(),
            "details": {
                "provider": request.llm_provider,
                "model": request.model_name,
                "max_iterations": request.max_iterations
            }
        }
    })
    
    # Inicia tarefa em background
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
        
        # Atualiza estado
        research_tasks[research_id]["status"] = "running"
        
        # Cria instância do agente com callback
        agent = DeepResearchAgent(
            llm_provider=request.llm_provider,
            api_key=request.api_key,
            model_name=request.model_name,
            status_callback=status_callback_factory(research_id)
        )
        
        logger.info(f"[{research_id}] Agente criado, iniciando pesquisa...")

        # Executa a pesquisa com timeout
        try:
            result = await asyncio.wait_for(
                agent.research(
                    query=request.query,
                    max_iterations=request.max_iterations
                ),
                timeout=1800  # 30 minutos de timeout (aumentado de 10 para 30 minutos)
            )
            
            # Atualiza estado e adiciona evento de conclusão
            research_tasks[research_id]["status"] = "completed"
            research_tasks[research_id]["result"] = result
            research_tasks[research_id]["progress"] = 100
            research_tasks[research_id]["completion_time"] = datetime.now().isoformat()
            
            # Adiciona evento de conclusão
            research_events[research_id].append({
                "type": "complete",
                "data": {
                    "message": "Pesquisa concluída com sucesso",
                    "timestamp": datetime.now().isoformat(),
                    "result": result
                }
            })
            
            logger.info(f"[{research_id}] Pesquisa concluída com sucesso")
            
        except asyncio.TimeoutError:
            # Tratamento de timeout
            error_msg = "Timeout na execução da pesquisa"
            logger.error(f"[{research_id}] {error_msg}")
            
            # Pega resultado parcial se existir
            partial_result = research_tasks[research_id].get("partial_result", "")
            if not partial_result:
                partial_result = "# Resultado Parcial (Timeout)\n\nA pesquisa excedeu o tempo limite antes de ser concluída."
            
            # Atualiza estado
            research_tasks[research_id]["status"] = "timeout"
            research_tasks[research_id]["result"] = partial_result
            
            # Adiciona evento de erro com resultado parcial
            research_events[research_id].append({
                "type": "error",
                "data": {
                    "message": error_msg,
                    "timestamp": datetime.now().isoformat(),
                    "error": "timeout",
                    "result": partial_result
                }
            })
            
    except Exception as e:
        error_msg = f"Erro durante pesquisa: {str(e)}"
        logger.error(f"[{research_id}] {error_msg}")

        # Pega resultado parcial se existir
        partial_result = research_tasks[research_id].get("partial_result", "")
        
        # Atualiza estado
        research_tasks[research_id]["status"] = "failed"
        research_tasks[research_id]["error"] = str(e)
        if partial_result:
            research_tasks[research_id]["result"] = f"# Resultado Parcial (Erro)\n\n{partial_result}\n\n## Erro\n\n{str(e)}"
        
        # Adiciona evento de erro
        research_events[research_id].append({
            "type": "error",
            "data": {
                "message": error_msg,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
                "result": partial_result if partial_result else None
            }
        })

@app.get("/research/{research_id}")
async def get_research_result(research_id: str):
    """
    Obtém o resultado da pesquisa pelo ID.
    Se a pesquisa estiver concluída, retorna o resultado.
    Se estiver em andamento, retorna o resultado parcial.
    """
    if research_id not in research_tasks:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    
    task_info = research_tasks[research_id]
    status = task_info.get("status", "unknown")
    
    # Se a pesquisa estiver concluída, retorna o resultado
    if status == "completed" and "result" in task_info:
        return {
            "research_id": research_id,
            "status": status,
            "result": task_info["result"]
        }
    # Se a pesquisa estiver em timeout ou falha, retorna o resultado parcial
    elif status in ["timeout", "failed"]:
        result = task_info.get("result", "")
        error = task_info.get("error", "Erro desconhecido")
        return {
            "research_id": research_id,
            "status": status,
            "error": error,
            "result": result
        }
    # Se a pesquisa ainda estiver em andamento, retorna o resultado parcial
    elif status in ["running", "started"]:
        partial_result = task_info.get("partial_result", "")
        progress = task_info.get("progress", 0)
        return {
            "research_id": research_id,
            "status": status,
            "progress": progress,
            "result": partial_result,
            "message": "Pesquisa em andamento"
        }
    else:
        return {
            "research_id": research_id,
            "status": status,
            "message": "Status desconhecido"
        }

@app.get("/research/{research_id}/status")
async def get_research_status(research_id: str):
    """Obtém status atual da pesquisa"""
    if research_id not in research_tasks:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    
    task_info = research_tasks[research_id]
    
    # Informações básicas
    response = {
        "research_id": research_id,
        "status": task_info.get("status", "unknown"),
        "progress": task_info.get("progress", 0),
        "start_time": task_info.get("start_time", ""),
        "last_update": task_info.get("last_update", ""),
        "last_message": task_info.get("last_message", ""),
        "query": task_info.get("query", "")
    }
    
    # Adiciona informações específicas conforme o status
    if task_info.get("status") == "completed":
        response["completion_time"] = task_info.get("completion_time", "")
        response["has_result"] = "result" in task_info
    elif task_info.get("status") in ["timeout", "failed"]:
        response["error"] = task_info.get("error", "")
        response["has_partial_result"] = bool(task_info.get("partial_result", ""))
    
    # Número de eventos
    response["event_count"] = len(research_events.get(research_id, []))
    
    return response

@app.delete("/research/{research_id}")
async def cancel_research(research_id: str):
    """Cancela uma pesquisa em andamento"""
    if research_id not in research_tasks:
        raise HTTPException(status_code=404, detail="Pesquisa não encontrada")
    
    task_info = research_tasks[research_id]
    
    # Só pode cancelar se estiver em andamento
    if task_info.get("status") not in ["started", "running"]:
        return {
            "message": f"Pesquisa não pode ser cancelada. Status atual: {task_info.get('status')}",
            "research_id": research_id
        }
    
    # Atualiza estado
    task_info["status"] = "cancelled"
    
    # Adiciona evento de cancelamento
    research_events[research_id].append({
        "type": "cancelled",
        "data": {
            "message": "Pesquisa cancelada pelo usuário",
            "timestamp": datetime.now().isoformat()
        }
    })
    
    return {"message": "Pesquisa cancelada", "research_id": research_id}

@app.get("/cleanup-old-research")
async def cleanup_old_research():
    """Limpa pesquisas antigas (admin)"""
    now = datetime.now()
    count = 0
    
    for research_id in list(research_tasks.keys()):
        task_info = research_tasks[research_id]
        
        # Converte string de data para objeto datetime
        start_time_str = task_info.get("start_time", "")
        if not start_time_str:
            continue
            
        try:
            start_time = datetime.fromisoformat(start_time_str)
            # Se a pesquisa tem mais de 24 horas
            if (now - start_time).days >= 1:
                del research_tasks[research_id]
                if research_id in research_events:
                    del research_events[research_id]
                count += 1
        except:
            pass
    
    return {"message": f"Limpeza concluída. {count} pesquisas removidas."}

@app.get("/health")
async def health_check():
    """Endpoint de verificação de saúde"""
    active_count = 0
    completed_count = 0
    failed_count = 0
    
    for task_id, task_info in research_tasks.items():
        status = task_info.get("status", "unknown")
        if status in ["started", "running"]:
            active_count += 1
        elif status == "completed":
            completed_count += 1
        elif status in ["failed", "timeout", "cancelled"]:
            failed_count += 1
    
    return {
        "status": "healthy",
        "message": "Deep Research Service está funcionando",
        "version": "2.0.0",
        "tasks": {
            "active": active_count,
            "completed": completed_count,
            "failed": failed_count,
            "total": len(research_tasks)
        },
        "events": {
            "total": sum(len(events) for events in research_events.values())
        }
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
            "result": "GET /research/{research_id}",
            "stream": "GET /research/stream/{research_id}",
            "status": "GET /research/{research_id}/status",
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