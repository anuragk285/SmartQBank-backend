from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from routers import subjects, questions, topics
from redis_fastapi import FastAPIRedis

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)

allow_origins = [
    "https://smartqbank.netlify.app",
    "http://localhost:5173",
    "http://192.168.0.168:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FastAPIRedis(app).lifespan().caching()

app.include_router(subjects.router)
app.include_router(questions.router)
app.include_router(topics.router)