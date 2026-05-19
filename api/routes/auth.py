from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session
from typing import Any, Optional, Annotated
from datetime import timedelta
from jose import jwt, JWTError

from database import get_db
from models.user import User
from core.security import verify_password, create_access_token, get_password_hash
from core.config import settings
from schemas.user_schema import UserCreate, UserInDB, User as UserSchema
from schemas.user_schema import Token, TokenData
from repositories.user_repo import UserRepository

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# Dependency definition
SessionDep = Annotated[Session, Depends(get_db)]

def get_user_repo(session: SessionDep) -> UserRepository:
    return UserRepository(session=session)

UserRepoDep = Annotated[UserRepository, Depends(get_user_repo)]

def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], user_repo: UserRepoDep) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    user = user_repo.get_by_username(username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

CurrentUserDep = Annotated[User, Depends(get_current_user)]

@router.post("/register", response_model=UserSchema)
async def register_user(user: UserCreate, user_repo: UserRepoDep):
    """Register a new user"""
    existing_user = user_repo.get_by_username(username=user.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    hashed_password = get_password_hash(user.password)
    
    db_user = User(
        username=user.username,
        hashed_password=hashed_password,
        is_active=user.is_active,
        full_name=user.full_name,
        email=user.email
    )
    return user_repo.create(obj_in=db_user)

@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    user_repo: UserRepoDep
):
    user = user_repo.get_by_username(username=form_data.username)
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=user.username, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserSchema)
async def read_users_me(current_user: CurrentUserDep):
    """Get the current user"""
    return current_user
