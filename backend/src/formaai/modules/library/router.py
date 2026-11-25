"""API router for user-private category, material, and visual asset operations."""

from collections.abc import Callable
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from formaai.infrastructure.storage import StorageServiceProtocol
from formaai.modules.identity.dependencies import (
    get_current_user,
    get_db,
    get_storage_service,
)
from formaai.modules.identity.models import UserAccount
from formaai.modules.library.models import Asset, Category, Material
from formaai.modules.library.schemas import (
    AssetCopyRequest,
    AssetListResponse,
    AssetResponse,
    CategoryCreateRequest,
    CategoryListResponse,
    CategoryResponse,
    CategoryUpdateRequest,
    ContentType,
    MaterialCreateRequest,
    MaterialListResponse,
    MaterialResponse,
    MaterialType,
    MaterialUpdateRequest,
)

category_router = APIRouter(prefix="/api/categories", tags=["categories"])
material_router = APIRouter(prefix="/api/materials", tags=["materials"])
asset_router = APIRouter(prefix="/api/assets", tags=["assets"])


def check_category_in_use(db: Session, category_id: int) -> bool:
    """Check if the category is referenced by any library materials or assets."""
    has_material = db.scalar(
        select(Material.id).where(Material.category_id == category_id).limit(1)
    )
    if has_material is not None:
        return True
    has_asset = db.scalar(
        select(Asset.id).where(Asset.category_id == category_id).limit(1)
    )
    return has_asset is not None


def get_category_usage_checker() -> Callable[[Session, int], bool]:
    """Dependency provider for category usage checker hook."""
    return check_category_in_use


# ---------------------------------------------------------------------------
# Category Endpoints
# ---------------------------------------------------------------------------


@category_router.get("", response_model=CategoryListResponse)
def list_categories(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    content_type: Annotated[
        ContentType | None,
        Query(description="Filter categories by content type"),
    ] = None,
) -> CategoryListResponse:
    """List categories owned by current user ordered by sort_order and id."""
    stmt = select(Category).where(Category.user_id == current_user.id)
    if content_type is not None:
        stmt = stmt.where(Category.content_type == content_type)
    stmt = stmt.order_by(Category.sort_order.asc(), Category.id.asc())

    categories = list(db.scalars(stmt).all())
    return CategoryListResponse(
        items=[CategoryResponse.model_validate(c) for c in categories],
        total=len(categories),
    )


@category_router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    body: CategoryCreateRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CategoryResponse:
    """Create a new user-private category with duplicate conflict prevention."""
    clean_name = body.name

    # 1. Application pre-check for fast 409 response
    existing = db.scalar(
        select(Category).where(
            Category.user_id == current_user.id,
            Category.content_type == body.content_type,
            Category.name == clean_name,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Category '{clean_name}' already exists in {body.content_type}"),
        )

    # 2. Determine sort order: append to the end if not specified
    if body.sort_order is None:
        max_order = db.scalar(
            select(func.max(Category.sort_order)).where(
                Category.user_id == current_user.id,
                Category.content_type == body.content_type,
            )
        )
        sort_order = 0 if max_order is None else max_order + 1
    else:
        sort_order = body.sort_order

    # 3. Create entity with race condition protection
    category = Category(
        user_id=current_user.id,
        content_type=body.content_type,
        name=clean_name,
        sort_order=sort_order,
    )
    try:
        db.add(category)
        db.commit()
        db.refresh(category)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Category '{clean_name}' already exists in {body.content_type}"),
        ) from exc

    return CategoryResponse.model_validate(category)


@category_router.patch(
    "/{category_id}",
    response_model=CategoryResponse,
    status_code=status.HTTP_200_OK,
)
def update_category(
    category_id: int,
    body: CategoryUpdateRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CategoryResponse:
    """Update a user-private category name and/or sort_order."""
    category = db.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == current_user.id,
        )
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )

    # 1. Update name if provided
    if body.name is not None:
        clean_name = body.name
        if clean_name.lower() != category.name.lower():
            conflict = db.scalar(
                select(Category).where(
                    Category.user_id == current_user.id,
                    Category.content_type == category.content_type,
                    Category.name == clean_name,
                    Category.id != category.id,
                )
            )
            if conflict is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Category '{clean_name}' already exists in "
                        f"{category.content_type}"
                    ),
                )
        category.name = clean_name

    # 2. Update sort_order if provided
    if body.sort_order is not None:
        category.sort_order = body.sort_order

    try:
        db.commit()
        db.refresh(category)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Category '{body.name}' already exists in {category.content_type}"
            ),
        ) from exc

    return CategoryResponse.model_validate(category)


@category_router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_category(
    category_id: int,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    usage_checker: Annotated[
        Callable[[Session, int], bool], Depends(get_category_usage_checker)
    ],
) -> Response:
    """Safely delete a user-private category if not referenced."""
    category = db.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == current_user.id,
        )
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )

    if usage_checker(db, category.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete category: it is referenced by existing library items",
        )

    db.delete(category)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Material Endpoints
# ---------------------------------------------------------------------------


@material_router.post(
    "",
    response_model=MaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_material(
    body: MaterialCreateRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MaterialResponse:
    """Create a new reusable text research, outline, or analysis material."""
    category = db.scalar(
        select(Category).where(
            Category.id == body.category_id,
            Category.user_id == current_user.id,
        )
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )

    material = Material(
        user_id=current_user.id,
        category_id=body.category_id,
        material_type=body.material_type,
        title=body.title,
        content=body.content,
        sources=[s.model_dump(mode="json") for s in body.sources],
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return MaterialResponse.model_validate(material)


@material_router.get("", response_model=MaterialListResponse)
def list_materials(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    category_id: Annotated[
        int | None,
        Query(description="Filter materials by category ID"),
    ] = None,
    material_type: Annotated[
        MaterialType | None,
        Query(description="Filter materials by material type"),
    ] = None,
) -> MaterialListResponse:
    """List materials owned by current user with optional filters."""
    stmt = select(Material).where(Material.user_id == current_user.id)
    if category_id is not None:
        stmt = stmt.where(Material.category_id == category_id)
    if material_type is not None:
        stmt = stmt.where(Material.material_type == material_type)
    stmt = stmt.order_by(Material.created_at.desc(), Material.id.desc())

    materials = list(db.scalars(stmt).all())
    return MaterialListResponse(
        items=[MaterialResponse.model_validate(m) for m in materials],
        total=len(materials),
    )


@material_router.get("/{material_id}", response_model=MaterialResponse)
def get_material(
    material_id: int,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MaterialResponse:
    """Get detail of a specific material owned by current user."""
    material = db.scalar(
        select(Material).where(
            Material.id == material_id,
            Material.user_id == current_user.id,
        )
    )
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )
    return MaterialResponse.model_validate(material)


@material_router.patch("/{material_id}", response_model=MaterialResponse)
def update_material(
    material_id: int,
    body: MaterialUpdateRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MaterialResponse:
    """Update title, content, sources, or reassign category of a material."""
    material = db.scalar(
        select(Material).where(
            Material.id == material_id,
            Material.user_id == current_user.id,
        )
    )
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    if body.category_id is not None and body.category_id != material.category_id:
        target_cat = db.scalar(
            select(Category).where(
                Category.id == body.category_id,
                Category.user_id == current_user.id,
            )
        )
        if target_cat is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target category not found",
            )
        material.category_id = body.category_id

    if body.title is not None:
        material.title = body.title
    if body.content is not None:
        material.content = body.content
    if body.sources is not None:
        material.sources = [s.model_dump(mode="json") for s in body.sources]

    db.commit()
    db.refresh(material)
    return MaterialResponse.model_validate(material)


@material_router.delete(
    "/{material_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_material(
    material_id: int,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Delete a material owned by current user."""
    material = db.scalar(
        select(Material).where(
            Material.id == material_id,
            Material.user_id == current_user.id,
        )
    )
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )
    db.delete(material)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Asset Endpoints
# ---------------------------------------------------------------------------


@asset_router.post(
    "/upload",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset(
    category_id: Annotated[int, Form(...)],
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageServiceProtocol, Depends(get_storage_service)],
    name: Annotated[str | None, Form()] = None,
) -> AssetResponse:
    """Upload a visual asset and link it to a user-private category."""
    category = db.scalar(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == current_user.id,
        )
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Uploaded file cannot be empty",
        )

    clean_name = name.strip() if name and name.strip() else (file.filename or "asset")
    mime_type = file.content_type or "application/octet-stream"

    try:
        file_key, file_size = storage.save_file(
            file_data=file_bytes,
            filename=file.filename or "asset",
            content_type=mime_type,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded asset",
        ) from exc

    asset = Asset(
        user_id=current_user.id,
        category_id=category_id,
        name=clean_name,
        file_key=file_key,
        mime_type=mime_type,
        file_size=file_size,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return AssetResponse.model_validate(asset)


@asset_router.post(
    "/{asset_id}/copy",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def copy_asset_to_category(
    asset_id: int,
    body: AssetCopyRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AssetResponse:
    """Copy an existing asset to another category as an independent record."""
    source_asset = db.scalar(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.user_id == current_user.id,
        )
    )
    if source_asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    target_category = db.scalar(
        select(Category).where(
            Category.id == body.target_category_id,
            Category.user_id == current_user.id,
        )
    )
    if target_category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target category not found",
        )

    cloned_name = body.new_name if body.new_name is not None else source_asset.name

    cloned_asset = Asset(
        user_id=current_user.id,
        category_id=body.target_category_id,
        name=cloned_name,
        file_key=source_asset.file_key,
        mime_type=source_asset.mime_type,
        file_size=source_asset.file_size,
    )
    db.add(cloned_asset)
    db.commit()
    db.refresh(cloned_asset)
    return AssetResponse.model_validate(cloned_asset)


@asset_router.get("", response_model=AssetListResponse)
def list_assets(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    category_id: Annotated[
        int | None,
        Query(description="Filter assets by category ID"),
    ] = None,
    mime_type: Annotated[
        str | None,
        Query(description="Filter assets by MIME type"),
    ] = None,
) -> AssetListResponse:
    """List assets owned by current user with optional filters."""
    stmt = select(Asset).where(Asset.user_id == current_user.id)
    if category_id is not None:
        stmt = stmt.where(Asset.category_id == category_id)
    if mime_type is not None:
        stmt = stmt.where(Asset.mime_type == mime_type)
    stmt = stmt.order_by(Asset.created_at.desc(), Asset.id.desc())

    assets = list(db.scalars(stmt).all())
    return AssetListResponse(
        items=[AssetResponse.model_validate(a) for a in assets],
        total=len(assets),
    )


@asset_router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: int,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AssetResponse:
    """Get detail of a specific visual asset owned by current user."""
    asset = db.scalar(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.user_id == current_user.id,
        )
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )
    return AssetResponse.model_validate(asset)


@asset_router.get(
    "/{asset_id}/file",
    status_code=status.HTTP_200_OK,
)
def get_asset_file(
    asset_id: int,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageServiceProtocol, Depends(get_storage_service)],
) -> Response:
    """Retrieve raw visual asset file content for preview or download."""
    asset = db.scalar(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.user_id == current_user.id,
        )
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )
    file_bytes = storage.get_file(asset.file_key)
    if file_bytes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset file content not found in storage",
        )
    return Response(
        content=file_bytes,
        media_type=asset.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{asset.name}"'},
    )


@asset_router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_asset(
    asset_id: int,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Delete a visual asset owned by current user."""
    asset = db.scalar(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.user_id == current_user.id,
        )
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )
    db.delete(asset)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
