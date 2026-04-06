from jose import jwt, JWTError
from jose.exceptions import ExpiredSignatureError
from cryptography.fernet import Fernet
from datetime import datetime, timedelta
import base64
import ast
import os
from typing import Tuple, Optional, Dict, Any
from fastapi import Header, Request, HTTPException, status

JWT_SECRET = os.getenv("JWT_SECRET")

def extract_bearer_or_token(authorization: Optional[str], token_header: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if token_header:
        return token_header.strip()
    return None

async def get_current_user_from_any(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Header(default=None),
):
    """
    쿠키(__Host-session) -> Authorization: Bearer -> token 헤더 순으로 JWT 추출 후 검증
    FastAPI dependency 로 사용
    """
    jwt_token = request.cookies.get("__Host-session") or extract_bearer_or_token(authorization, token)
    if not jwt_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    err, user_id = verify_jwt_token(jwt_token)
    if err:
        raise HTTPException(status_code=err["status_code"], detail=err["error"])
    return {"id": user_id}

def get_fernet_key() -> bytes:
    """JWT 시크릿을 기반으로 Fernet 키 생성"""
    key = JWT_SECRET.encode()[:32].ljust(32, b'0')
    return base64.urlsafe_b64encode(key)

def encrypt_user_data(user_data: Dict[str, Any]) -> str:
    """사용자 데이터 암호화"""
    fernet = Fernet(get_fernet_key())
    encrypted_payload = fernet.encrypt(str(user_data).encode())
    return encrypted_payload.decode()

def decrypt_user_data(encrypted_data: str) -> Optional[Dict[str, Any]]:
    """암호화된 사용자 데이터 복호화"""
    try:
        fernet = Fernet(get_fernet_key())
        decrypted_bytes = fernet.decrypt(encrypted_data.encode())
        decrypted_str = decrypted_bytes.decode()
        return ast.literal_eval(decrypted_str)
    except Exception:
        return None

def create_jwt_token(user_data: Dict[str, Any], expires_days: int = 7) -> str:
    """JWT 토큰 생성"""
    encrypted_data = encrypt_user_data(user_data)
    payload = {
        "data": encrypted_data,
        "exp": datetime.utcnow() + timedelta(days=expires_days)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_jwt_token(token: str) -> Tuple[Optional[Dict], Optional[int]]:
    """JWT 토큰 검증 및 사용자 ID 추출"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        encrypted_data = payload.get("data")

        if not encrypted_data:
            return {"error": "Invalid token payload", "status_code": 401}, None

        user_data = decrypt_user_data(encrypted_data)
        if not user_data:
            return {"error": "Failed to decrypt user data", "status_code": 401}, None

        user_id = user_data.get("id")
        if not user_id:
            return {"error": "Invalid user data", "status_code": 401}, None

        return None, int(user_id)

    except ExpiredSignatureError:
        return {"error": "Token expired", "status_code": 401}, None
    except JWTError:
        return {"error": "Invalid token", "status_code": 401}, None
    except Exception:
        return {"error": "Token verification failed", "status_code": 500}, None
