"""回声冲突裁定的 HTTP 路由。

为兼容不同调用习惯，同一能力挂了多组路径别名；GET 查询串与 POST JSON 都收，
字段名做宽松归一（票号/票/ticket …）。核心四键严格为：
``正式集`` / ``旁证数`` / ``裁定标记`` / ``valid``。
"""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from app.services import 回声裁定 as S

路由 = APIRouter(prefix="/api")


async def 入参(request: Request) -> dict:
    p = {k: v for k, v in request.query_params.items()}
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                p.update(body)
        except Exception:
            pass
    return p


def _名册(p: dict):
    for k in ("边缘名册", "应确认边缘", "名册", "边缘们", "edges"):
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
        if isinstance(v, list):
            return [str(x) for x in v]
    return None


# ---------------- 策略 ----------------
@路由.api_route("/裁定策略", methods=["GET", "POST", "PUT"])
@路由.api_route("/策略", methods=["GET", "POST", "PUT"])
@路由.api_route("/回声冲突裁定/策略", methods=["GET", "POST", "PUT"])
@路由.api_route("/回声冲突裁定/裁定策略", methods=["GET", "POST", "PUT"])
async def 策略路由(request: Request):
    p = await 入参(request)
    if request.method == "GET" and not any(k in p for k in ("策略", "策略值", "strategy")):
        return S.总览()
    return await run_in_threadpool(S.设置策略, p)


# 题目原词「物化裁定策略表」的具名路由：GET 读表，带策略字段即设置。
@路由.api_route("/裁定策略表", methods=["GET", "POST", "PUT"])
@路由.api_route("/回声冲突裁定/裁定策略表", methods=["GET", "POST", "PUT"])
async def 策略表路由(request: Request):
    p = await 入参(request)
    带值 = any(k in p for k in ("策略", "策略值", "裁定策略", "strategy"))
    if request.method == "GET" and not 带值:
        return await run_in_threadpool(S.列策略表)
    if 带值:
        return await run_in_threadpool(S.设置策略, p)
    return await run_in_threadpool(S.列策略表)


# ---------------- 核证 / 裁定 ----------------
@路由.api_route("/核证", methods=["GET", "POST"])
@路由.api_route("/核证路由", methods=["GET", "POST"])
@路由.api_route("/裁定", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/核证", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/核证路由", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/裁定", methods=["GET", "POST"])
async def 核证路由(request: Request):
    p = await 入参(request)
    return await run_in_threadpool(S.核证, S.取票号(p) or None)


# ---------------- 正式集 / 正式证 ----------------
@路由.get("/正式集")
@路由.get("/正式证")
@路由.get("/回声冲突裁定/正式集")
@路由.get("/回声冲突裁定/正式证")
async def 正式证路由(request: Request):
    p = await 入参(request)
    t = S.取票号(p)
    if request.url.path.endswith("/正式证"):
        return S.列正式证(t)
    return await run_in_threadpool(S.核证, t or None)


# ---------------- 旁证历史 / 回声流水 ----------------
@路由.get("/旁证历史")
@路由.get("/旁证")
@路由.get("/回声冲突裁定/旁证历史")
async def 旁证路由(request: Request):
    p = await 入参(request)
    return S.列旁证(S.取票号(p))


@路由.get("/回声历史")
@路由.get("/回声流水")
@路由.get("/回读历史")
@路由.get("/回声冲突裁定/历史")
async def 流水路由(request: Request):
    p = await 入参(request)
    return S.列流水(S.取票号(p))


# ---------------- 放量门 ----------------
@路由.api_route("/放量门", methods=["GET", "POST"])
@路由.api_route("/放量", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/放量门", methods=["GET", "POST"])
async def 放量门路由(request: Request):
    p = await 入参(request)
    t = S.取票号(p)
    落账 = str(p.get("落账", p.get("记账", ""))).lower() in ("1", "true", "是", "yes") \
        or p.get("动作") in ("放量", "决策")
    return await run_in_threadpool(S.放量门, t, _名册(p), 落账)


# ---------------- 双序自动化 ----------------
@路由.api_route("/双序", methods=["GET", "POST"])
@路由.api_route("/双序自动化", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/双序", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/双序自动化", methods=["GET", "POST"])
async def 双序路由(request: Request):
    p = await 入参(request)
    return await run_in_threadpool(S.跑双序, p)


# ---------------- 并发演练 ----------------
@路由.api_route("/并发演练", methods=["GET", "POST"])
@路由.api_route("/冲突放量并发", methods=["GET", "POST"])
@路由.api_route("/冲突与放量并发", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/并发", methods=["GET", "POST"])
@路由.api_route("/回声冲突裁定/并发演练", methods=["GET", "POST"])
async def 并发路由(request: Request):
    p = await 入参(request)
    return await run_in_threadpool(S.并发演练, p)


# ---------------- 特性总览 / 同名智能入口 ----------------
_EDGE_KEYS = ("边缘键", "边缘", "边缘标识", "edge", "edgeKey", "edge_key", "edgeId")
_RESULT_KEYS = ("结果", "回声结果", "result", "verdict", "outcome")
_VERSION_KEYS = ("所见版本键", "所见版本", "版本键", "seenVersion", "version")
_STRATEGY_KEYS = ("策略", "策略值", "裁定策略", "strategy")


def _是回声写入(p: dict) -> bool:
    有缘 = any(k in p and p[k] not in (None, "") for k in _EDGE_KEYS)
    有果 = any(k in p and p[k] not in (None, "") for k in _RESULT_KEYS)
    有版 = any(k in p and p[k] not in (None, "") for k in _VERSION_KEYS)
    return 有缘 and (有果 or 有版)


@路由.api_route("/回声冲突裁定", methods=["GET", "POST", "PUT"])
@路由.api_route("/回声冲突裁定/总览", methods=["GET", "POST"])
@路由.get("/回声冲突")
async def 特性总览(request: Request):
    """与空桩同名的入口，按载荷辨义：

    - 带边缘+结果/版本 → 记一笔回声（同票冲突写，返回核证四键）；
    - 带策略 → 切换物化裁定策略；
    - 带票号 → 该票核证（严格四键）；
    - 都没有 → 全局总览（同样含 valid 与边缘并集「正式集」）。
    """
    from app.services import 回读
    p = await 入参(request)
    if request.method in ("POST", "PUT", "PATCH") and _是回声写入(p):
        return await run_in_threadpool(回读.记回读, p)
    if any(k in p and p[k] not in (None, "") for k in _STRATEGY_KEYS):
        await run_in_threadpool(S.设置策略, p)
    t = S.取票号(p)
    if t:
        return await run_in_threadpool(S.核证, t)
    return await run_in_threadpool(S.总览)


# ---------------- 特性前缀下的回声写入别名（主写入仍走 /api/回读） ----------------
@路由.post("/回声冲突裁定/回声")
@路由.post("/回声冲突裁定/回读")
@路由.post("/回声冲突")
async def 回声写入别名(request: Request):
    from app.services import 回读
    p = await 入参(request)
    return await run_in_threadpool(回读.记回读, p)


# ---------------- 特性夹具 ----------------
@路由.post("/回声冲突裁定/夹具")
async def 冲突夹具路由(request: Request):
    p = await 入参(request)
    return await run_in_threadpool(S.装冲突夹具, p)
