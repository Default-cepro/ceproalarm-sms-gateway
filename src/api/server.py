from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import asyncio

app = FastAPI()

incoming_sms_queue = asyncio.Queue()

@app.get("/webhook/sms")
async def validate():
    return {"success": True}

@app.post("/webhook/sms")
async def receive_sms(request: Request):
    data = await request.json()

    print("DATA RECIBIDA:", data)

    phone = data.get("from")
    message = data.get("message")

    await incoming_sms_queue.put({
        "phone": phone,
        "message": message
    })
    

    return JSONResponse(
        status_code=200,
        content={"success": True}
    )
