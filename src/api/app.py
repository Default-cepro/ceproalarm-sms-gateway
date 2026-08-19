from fastapi import FastAPI

from .webhooks import register_routes

app = FastAPI()
register_routes(app)