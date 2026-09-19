from fastapi import FastAPI

app = FastAPI(title="Scientific Home Cluster API")

@app.get("/")
async def root():
    return {"message": "Welcome to the Scientific Home Cluster API"}