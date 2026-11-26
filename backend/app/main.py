from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import seed
from app.routers import api
from app.routers.回声裁定 import 路由 as 回声路由

app = FastAPI(title="灰度轨", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _启动():
    seed.初始化库()


# 导入期也幂等建表一次：兼容未以 with TestClient(app) 触发 lifespan 的调用方。
try:
    seed.初始化库()
except Exception:
    pass


app.include_router(回声路由)
app.include_router(api)
