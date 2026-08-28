"""图片上传与多模态检索（V3）。

设计约束（DESIGN.md）：
- 数据库不存二进制，只存 storage_url / object key；生产换成 S3 只需改 _save()
- 图片缺失时该维度**不参与评分**，绝不能记 0 分
- CLIP 的视觉侧与文本侧同一空间，因此「用户只有文字、工作人员只有照片」也能匹配
"""
from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ai.image_provider import get_image_provider, image_enabled
from ..config import settings
from ..db import commit, get_session
from .. import repository as repo

router = APIRouter(tags=["images"])

_ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _storage_root() -> Path:
    root = Path(settings.image_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _save(item_id: str, filename: str, data: bytes) -> Path:
    """落盘并按内容哈希命名——同一张图重复上传不会占两份空间。"""
    suffix = Path(filename or "").suffix.lower() or ".jpg"
    digest = hashlib.sha256(data).hexdigest()[:32]
    target_dir = _storage_root() / item_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{digest}{suffix}"
    if not path.exists():
        path.write_bytes(data)
    return path


@router.post("/api/items/{item_id}/images")
async def upload_image(item_id: str, file: UploadFile = File(...),
                       is_primary: bool = Form(False),
                       session: Session = Depends(get_session)):
    if not image_enabled():
        raise HTTPException(503, "图像匹配未启用：设置 LF_IMAGE_PROVIDER=clip_onnx")

    exists = session.execute(text("SELECT 1 FROM item_records WHERE id = :i"),
                             {"i": item_id}).fetchone()
    if not exists:
        raise HTTPException(404, "记录不存在")

    ctype = file.content_type or mimetypes.guess_type(file.filename or "")[0]
    if ctype not in _ALLOWED:
        raise HTTPException(415, f"不支持的图片类型：{ctype}")

    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    if len(data) > settings.image_max_bytes:
        raise HTTPException(413, f"图片超过 {settings.image_max_bytes // 1024 // 1024}MB")

    path = _save(item_id, file.filename or "", data)
    image_id = repo.add_item_image(session, item_id, storage_url=str(path),
                                   image_type=ctype, is_primary=is_primary)
    generated = repo.build_image_embedding(session, item_id, str(path))

    repo.record_audit(session, actor_id=None, action="IMAGE_UPLOADED",
                      entity_type="item_records", entity_id=item_id,
                      after={"image_id": image_id, "bytes": len(data)})
    commit(session)
    return {
        "image_id": image_id,
        "item_id": item_id,
        "bytes": len(data),
        "embedding_generated": generated,
        "url": f"/api/images/{image_id}",
    }


@router.get("/api/images/{image_id}")
def get_image(image_id: str, session: Session = Depends(get_session)):
    row = session.execute(text(
        "SELECT storage_url, image_type FROM item_images WHERE id = :i"),
        {"i": image_id}).fetchone()
    if row is None:
        raise HTTPException(404, "图片不存在")
    path = Path(row[0])
    if not path.exists():
        raise HTTPException(410, "图片文件已不在存储中")
    return FileResponse(path, media_type=row[1] or "image/jpeg")


@router.get("/api/items/{item_id}/images")
def list_images(item_id: str, session: Session = Depends(get_session)):
    rows = session.execute(text(
        "SELECT id::text, image_type, is_primary, created_at FROM item_images "
        "WHERE item_id = :i ORDER BY is_primary DESC, created_at"),
        {"i": item_id}).fetchall()
    return {
        "item_id": item_id,
        "images": [
            {"image_id": r[0], "url": f"/api/images/{r[0]}",
             "image_type": r[1], "is_primary": r[2], "created_at": r[3]}
            for r in rows
        ],
    }


@router.post("/api/search/by-image")
async def search_by_image(file: UploadFile = File(...),
                          type: str = Form("FOUND"),
                          top_k: int = Form(20),
                          session: Session = Depends(get_session)):
    """以图搜物：用户上传自己物品的旧照片，找工作人员登记的拾获物。"""
    if not image_enabled():
        raise HTTPException(503, "图像匹配未启用：设置 LF_IMAGE_PROVIDER=clip_onnx")

    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    tmp = _storage_root() / "_query" / f"{uuid.uuid4().hex}{Path(file.filename or '').suffix or '.jpg'}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        vec = get_image_provider().embed_image(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)

    return {"query": "image", "results": _image_search(session, vec, type, top_k)}


# CLIP ViT-B-32 只在英文语料上训练过。实测（scripts/eval_images.py）：
#   英文 query  5/5 命中，余弦 0.28~0.34
#   日文 query  2/5 命中，余弦 0.23~0.25（基本是噪声水平）
# 对一个部署在日本的系统这是硬伤。
#
# 解法不是换更大的模型，而是复用**已有的**同义词标准化层：
# 抽取层本来就把「黒い」「リュック」归一成了英文 canonical（black / bag），
# 于是可以拼出一句规范的英文 CLIP prompt 再去检索。
# 词典白建了才可惜——这正是第 ① 层存在的价值之一。
def build_clip_prompt(parsed: dict) -> str | None:
    """结构化属性 -> 英文 CLIP prompt。抽不出东西就返回 None。"""
    attrs = {a["attribute_code"]: a["value_text"] for a in parsed.get("attributes", [])}
    color = attrs.get("color")
    material = attrs.get("material")
    category = parsed.get("category")
    brand = parsed.get("brand")

    words = [w for w in (color, material, brand) if w]
    noun = (category or "").replace("_", " ") or None
    if not noun and not words:
        return None
    phrase = " ".join(words + ([noun] if noun else []))
    article = "an" if phrase[:1].lower() in "aeiou" else "a"
    return f"a photo of {article} {phrase}"


@router.post("/api/search/text-to-image")
def search_text_to_image(payload: dict, session: Session = Depends(get_session)):
    """以文搜图：用户没有照片，只有一句描述，也要能匹配到工作人员拍的照片。

    走 CLIP 文本侧，与 IMAGE 向量同一空间——这正是选 CLIP 而不是纯视觉模型的原因。
    """
    if not image_enabled():
        raise HTTPException(503, "图像匹配未启用：设置 LF_IMAGE_PROVIDER=clip_onnx")
    query = (payload or {}).get("query", "")
    if not query.strip():
        raise HTTPException(400, "query 不能为空")

    from ..ai.extraction import query_understanding

    parsed = query_understanding(query)
    prompt = build_clip_prompt(parsed)
    vec = get_image_provider().embed_text(prompt or query)

    return {
        "query": query,
        # 把改写后的 prompt 一并返回：检索为什么这么走，必须是可见的
        "clip_prompt": prompt,
        "understood": {"category": parsed.get("category"),
                       "brand": parsed.get("brand"),
                       "attributes": {a["attribute_code"]: a["value_text"]
                                      for a in parsed.get("attributes", [])}},
        "results": _image_search(session, vec,
                                 (payload or {}).get("type", "FOUND"),
                                 int((payload or {}).get("top_k", 20))),
    }


_IMG_SEARCH_SQL = """
SELECT e.item_id::text, 1 - (e.embedding <=> CAST(:v AS vector)) AS sim,
       r.raw_description,
       (SELECT id::text FROM item_images WHERE item_id = e.item_id
        ORDER BY is_primary DESC LIMIT 1) AS image_id
FROM embeddings e
JOIN item_records r ON r.id = e.item_id
WHERE e.embedding_type = 'IMAGE'
  AND e.status = 'ACTIVE'
  AND e.model_name = :model
  AND r.record_type = :rtype
  AND r.status = 'ACTIVE'
ORDER BY e.embedding <=> CAST(:v AS vector)
LIMIT :limit
"""


def _image_search(session: Session, vector: list[float], rtype: str,
                  top_k: int) -> list[dict]:
    from ..db import vector_literal

    rows = session.execute(text(_IMG_SEARCH_SQL), {
        "v": vector_literal(vector),
        # 只和同一个模型产出的向量比较——跨模型比余弦是没有意义的
        "model": get_image_provider().model,
        "rtype": rtype,
        "limit": top_k,
    }).fetchall()
    return [
        {"record_id": r[0], "image_similarity": round(float(r[1]), 4),
         "raw_description": r[2],
         "image_url": f"/api/images/{r[3]}" if r[3] else None}
        for r in rows
    ]
