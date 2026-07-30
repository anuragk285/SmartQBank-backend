from fastapi import FastAPI
from database import Base, engine
from routers import subjects, questions, topics
from fastapi.middleware.cors import CORSMiddleware
from redis_fastapi import FastAPIRedis

Base.metadata.create_all(bind=engine)
app = FastAPI()
app.include_router(subjects.router)
app.include_router(questions.router)
app.include_router(topics.router)

allow_origins=["https://smartqbank.netlify.app", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins, 
    allow_credentials=True,
    allow_methods=["*"],          
    allow_headers=["*"],           
)

FastAPIRedis(app).lifespan().caching()