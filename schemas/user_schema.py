from pydantic import BaseModel

class UserBase(BaseModel):
    username: str
    email: str
    full_name: str | None = None
    is_active: bool = True


class UserCreate(UserBase):
    password: str
    

class UserUpdate(UserBase):
    pass


class UserInDB(UserBase):
    id: int
    hashed_password: str
    

class User(UserInDB):  # This is what you will get from the API (public view)
    pass

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None