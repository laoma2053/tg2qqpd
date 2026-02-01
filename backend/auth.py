import os
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from jose import jwt

SECRET = os.getenv("JWT_SECRET", "change_me")
ADMIN_PASS = os.getenv("ADMIN_PASS", "change_me")

security = HTTPBearer()

def login(password: str):
    if password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="invalid password")
    return jwt.encode({"user": "admin"}, SECRET)

def auth_required(token=Depends(security)):
    try:
        jwt.decode(token.credentials, SECRET)
    except Exception:
        raise HTTPException(status_code=401, detail="unauthorized")
