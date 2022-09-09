import os
from typing import List, Optional, Union

import uvicorn
from fastapi import Depends, FastAPI
from fastapi import File as FormFile
from fastapi import Form, Path, UploadFile
from libcloud.storage.drivers.local import LocalStorageDriver
from libcloud.storage.providers import get_driver
from libcloud.storage.types import (
    ContainerAlreadyExistsError,
    ObjectDoesNotExistError,
    Provider,
)
from pydantic import BaseModel
from sqlalchemy import Column
from sqlalchemy_file import File, ImageField
from sqlalchemy_file.exceptions import ValidationError
from sqlalchemy_file.processors import ThumbnailGenerator
from sqlalchemy_file.storage import StorageManager
from sqlalchemy_file.validators import SizeValidator
from sqlmodel import Field, Session, SQLModel, create_engine, select
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

engine = create_engine("sqlite:////tmp/example.db?check_same_thread=False", echo=True)


os.makedirs("/tmp/storage", 0o777, exist_ok=True)
driver = get_driver(Provider.LOCAL)("/tmp/storage")

# cls = get_driver(Provider.MINIO)
# driver = cls("minioadmin", "minioadmin", secure=False, host="127.0.0.1", port=9000)

try:
    driver.create_container(container_name="category")
except ContainerAlreadyExistsError:
    pass

container = driver.get_container(container_name="category")

StorageManager.add_storage("category", container)


class Thumbnail(BaseModel):
    path: str
    url: Optional[str]


class FileInfo(BaseModel):
    filename: str
    content_type: str
    path: str
    url: Optional[str]
    thumbnail: Thumbnail


class CategoryBase(SQLModel):
    id: Optional[int] = Field(None, primary_key=True)
    name: str = Field(None, min_length=3, max_length=100)


class Category(CategoryBase, table=True):
    image: Union[File, UploadFile, None] = Field(
        sa_column=Column(
            ImageField(
                upload_storage="category",
                validators=[SizeValidator(max_size="1M")],
                processors=[ThumbnailGenerator(thumbnail_size=(200, 200))],
            )
        )
    )


class CategoryOut(CategoryBase):
    image: Optional[FileInfo]


def category_form(
    name: str = Form(...),
    image: Optional[UploadFile] = FormFile(None),
):
    return Category(name=name, image=image)


app = FastAPI(title="SQLAlchemy-file Example", debug=True)


@app.get("/categories", response_model=List[CategoryOut])
def get_all():
    with Session(engine) as session:
        return session.execute(select(Category)).all()


@app.get("/categories/{id}", response_model=CategoryOut)
def get_one(id: int = Path(...)):
    with Session(engine) as session:
        category = session.get(Category, id)
        if category is not None:
            return category
        return JSONResponse({"detail": "Not found"}, status_code=404)


@app.post("/categories", response_model=CategoryOut)
def create_new(category: Category = Depends(category_form)):
    with Session(engine) as session:
        try:
            session.add(category)
            session.commit()
            session.refresh(category)
            return category
        except ValidationError as e:
            return JSONResponse(
                dict(error={"key": e.key, "msg": e.msg}), status_code=422
            )


@app.get("/medias/{storage}/{file_id}", response_class=FileResponse)
def serve_files(storage: str = Path(...), file_id: str = Path(...)):
    try:
        file = StorageManager.get_file(f"{storage}/{file_id}")
        if isinstance(file.object.driver, LocalStorageDriver):
            """If file is stored in local storage, just return a
            FileResponse with the fill full path."""
            return FileResponse(
                file.get_cdn_url(), media_type=file.content_type, filename=file.filename
            )
        elif file.get_cdn_url() is not None:
            """If file has public url, redirect to this url"""
            return RedirectResponse(file.get_cdn_url())
        else:
            """Otherwise, return a streaming response"""
            return StreamingResponse(
                file.object.as_stream(),
                media_type=file.content_type,
                headers={"Content-Disposition": f"attachment;filename={file.filename}"},
            )
    except ObjectDoesNotExistError:
        return JSONResponse({"detail": "Not found"}, status_code=404)


if __name__ == "__main__":
    SQLModel.metadata.create_all(engine)
    uvicorn.run(app, port=8000)
    # Navigate to http://127.0.0.1:8000/docs