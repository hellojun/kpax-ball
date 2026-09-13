"""知识图谱 API — 图谱数据 + 搜索。"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/graph", tags=["graph"])


class GraphResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]


@router.get("/data", response_model=GraphResponse)
async def get_graph_data():
    """获取知识图谱数据用于可视化。"""
    from app.services.zep_manager import get_graph_data
    return get_graph_data()


class SearchRequest(BaseModel):
    query: str
    limit: int = 5


class SearchResult(BaseModel):
    results: list[dict]


@router.post("/search", response_model=SearchResult)
async def search_graph(body: SearchRequest):
    """搜索知识图谱。"""
    from app.services.zep_manager import search_knowledge
    return {"results": search_knowledge(body.query, body.limit)}


class TeamHistoryResponse(BaseModel):
    context: str


@router.get("/team/{team_name}")
async def get_team_history(team_name: str):
    """获取球队的历史分析洞察。"""
    from app.services.zep_manager import get_team_insights
    return {"context": get_team_insights(team_name)}
