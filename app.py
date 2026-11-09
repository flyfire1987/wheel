"""
🎡 大转盘 (Wheel of Fortune / 抽签转盘) — FastAPI 后端

功能:
  - 多套配置 (config), 每套包含一组关键词 (items)
  - 配置的增 / 删 / 改, 关键词的增 / 删 / 改 / 权重 / 已中标记
  - 历史记录 (最近 50 条)
  - 静态前端页面 (static/index.html)

设计要点:
  - 配置存 data/configs.json, 单文件, 原子写 (tmp + rename)
  - 每次写自动备份到 data/configs.json.bak
  - 权重用于服务端"预抽"落点, 前端负责动画展示
"""
from __future__ import annotations

import json
import os
import random
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "configs.json")
BACKUP_PATH = os.path.join(DATA_DIR, "configs.json.bak")
STATIC_DIR = os.path.join(BASE_DIR, "static")

MAX_CONFIGS = 30
MAX_ITEMS = 120
MAX_HISTORY = 50

app = FastAPI(title="Wheel", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ───────────────────────── 数据读写 ─────────────────────────
DEFAULT_DATA: Dict[str, Any] = {
    "version": 1,
    "active_config_id": "",
    "configs": [],
    "history": [],
}


def _load() -> Dict[str, Any]:
    """读取配置; 文件缺失/损坏时尝试备份, 再不行返回默认骨架。"""
    for path in (CONFIG_PATH, BACKUP_PATH):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict) and isinstance(data.get("configs"), list):
                return data
        except Exception as exc:  # noqa: BLE001
            print(f"[wheel] 读取 {path} 失败: {exc}", flush=True)
    return json.loads(json.dumps(DEFAULT_DATA))


def _save(data: Dict[str, Any]) -> None:
    """原子写入: 先写临时文件再 rename, 避免写一半进程被杀导致文件损坏。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        try:
            shutil.copy2(CONFIG_PATH, BACKUP_PATH)
        except Exception:  # noqa: BLE001
            pass
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, CONFIG_PATH)


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _find_config(data: Dict[str, Any], config_id: str) -> Dict[str, Any]:
    for cfg in data["configs"]:
        if cfg.get("id") == config_id:
            return cfg
    raise HTTPException(status_code=404, detail="配置不存在")


def _sanitize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """归一化单个关键词项, 保证字段类型正确。"""
    text = str(item.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="关键词不能为空")
    if len(text) > 40:
        raise HTTPException(status_code=400, detail="关键词最长 40 字")
    try:
        weight = int(item.get("weight", 1))
    except (TypeError, ValueError):
        weight = 1
    weight = max(1, min(20, weight))
    return {
        "id": item.get("id") or _new_id("i"),
        "text": text,
        "weight": weight,
        "used": bool(item.get("used", False)),
    }


# ───────────────────────── 请求模型 ─────────────────────────
class ItemIn(BaseModel):
    text: str = Field(..., description="关键词文本")
    weight: int = 1
    used: bool = False


class ItemPatch(BaseModel):
    text: Optional[str] = None
    weight: Optional[int] = None
    used: Optional[bool] = None


class ConfigIn(BaseModel):
    name: str = Field(..., description="配置名(领域)")
    emoji: str = "🎯"


class ConfigPatch(BaseModel):
    name: Optional[str] = None
    emoji: Optional[str] = None


class SpinResult(BaseModel):
    config_id: str
    item_id: Optional[str] = None
    text: str
    index: int
    # 各扇区中心角度(度), 前端用它做动画终点, 保证落点与展示一致
    angles: List[float] = []


# ───────────────────────── API: 读取 ─────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True, "configs": len(_load()["configs"])}


@app.get("/api/configs")
def get_configs():
    return _load()


@app.post("/api/spin")
def spin(payload: SpinResult):
    """服务端预抽结果 (按权重加权随机)。前端也可自己随机, 但走服务端能保证多端一致。"""
    data = _load()
    cfg = _find_config(data, payload.config_id)
    items = cfg.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="该配置还没有关键词")

    pool = [it for it in items if not it.get("used")] or items
    weights = [max(1, int(it.get("weight", 1))) for it in pool]
    chosen = random.choices(pool, weights=weights, k=1)[0]

    # 找到它在"原始 items"里的下标 (前端盘面按 items 顺序渲染)
    index = next((i for i, it in enumerate(items) if it["id"] == chosen["id"]), 0)
    n = len(items)
    step = 360.0 / n
    # 扇区 i 覆盖 [i*step, (i+1)*step), 中心在 i*step + step/2
    angles = [i * step + step / 2 for i in range(n)]

    return {
        "config_id": cfg["id"],
        "item_id": chosen["id"],
        "text": chosen["text"],
        "index": index,
        "angles": angles,
    }


# ───────────────────────── API: 配置 CRUD ─────────────────────────
@app.post("/api/configs")
def create_config(payload: ConfigIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="配置名不能为空")
    if len(name) > 20:
        raise HTTPException(status_code=400, detail="配置名最长 20 字")

    data = _load()
    if len(data["configs"]) >= MAX_CONFIGS:
        raise HTTPException(status_code=400, detail=f"最多 {MAX_CONFIGS} 套配置")
    if any(c["name"] == name for c in data["configs"]):
        raise HTTPException(status_code=400, detail=f"配置「{name}」已存在")

    cfg = {
        "id": _new_id("c"),
        "name": name,
        "emoji": (payload.emoji or "🎯")[:4],
        "items": [],
    }
    data["configs"].append(cfg)
    if not data.get("active_config_id"):
        data["active_config_id"] = cfg["id"]
    _save(data)
    return cfg


@app.patch("/api/configs/{config_id}")
def update_config(config_id: str, payload: ConfigPatch):
    data = _load()
    cfg = _find_config(data, config_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="配置名不能为空")
        if any(c["name"] == name and c["id"] != config_id for c in data["configs"]):
            raise HTTPException(status_code=400, detail=f"配置「{name}」已存在")
        cfg["name"] = name
    if payload.emoji is not None:
        cfg["emoji"] = (payload.emoji or "🎯")[:4]
    _save(data)
    return cfg


@app.delete("/api/configs/{config_id}")
def delete_config(config_id: str):
    data = _load()
    cfg = _find_config(data, config_id)
    data["configs"] = [c for c in data["configs"] if c["id"] != config_id]
    if data.get("active_config_id") == config_id:
        data["active_config_id"] = data["configs"][0]["id"] if data["configs"] else ""
    _save(data)
    return {"ok": True, "deleted": cfg["name"]}


@app.post("/api/configs/active")
def set_active(payload: Dict[str, str]):
    config_id = payload.get("config_id", "")
    data = _load()
    _find_config(data, config_id)
    data["active_config_id"] = config_id
    _save(data)
    return {"ok": True, "active_config_id": config_id}


# ───────────────────────── API: 关键词 CRUD ─────────────────────────
@app.post("/api/configs/{config_id}/items")
def add_item(config_id: str, payload: ItemIn):
    data = _load()
    cfg = _find_config(data, config_id)
    items = cfg.setdefault("items", [])
    if len(items) >= MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"单套配置最多 {MAX_ITEMS} 个关键词")
    if any(it["text"] == payload.text.strip() for it in items):
        raise HTTPException(status_code=400, detail=f"关键词「{payload.text.strip()}」已存在")
    item = _sanitize_item({"text": payload.text, "weight": payload.weight, "used": payload.used})
    items.append(item)
    _save(data)
    return item


@app.patch("/api/configs/{config_id}/items/{item_id}")
def update_item(config_id: str, item_id: str, payload: ItemPatch):
    data = _load()
    cfg = _find_config(data, config_id)
    item = next((it for it in cfg.get("items", []) if it["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="关键词不存在")

    merged = {
        "id": item_id,
        "text": payload.text if payload.text is not None else item["text"],
        "weight": payload.weight if payload.weight is not None else item["weight"],
        "used": payload.used if payload.used is not None else item["used"],
    }
    clean = _sanitize_item(merged)
    if any(it["text"] == clean["text"] and it["id"] != item_id for it in cfg["items"]):
        raise HTTPException(status_code=400, detail=f"关键词「{clean['text']}」已存在")
    item.update(clean)
    _save(data)
    return item


@app.delete("/api/configs/{config_id}/items/{item_id}")
def delete_item(config_id: str, item_id: str):
    data = _load()
    cfg = _find_config(data, config_id)
    before = len(cfg.get("items", []))
    cfg["items"] = [it for it in cfg.get("items", []) if it["id"] != item_id]
    if len(cfg["items"]) == before:
        raise HTTPException(status_code=404, detail="关键词不存在")
    _save(data)
    return {"ok": True}


# ───────────────────────── API: 批量操作 ─────────────────────────
@app.post("/api/configs/{config_id}/items/bulk")
def bulk_items(config_id: str, payload: Dict[str, Any]):
    """批量: 用换行/逗号分隔的文本一次性添加多个关键词。"""
    raw = str(payload.get("text", ""))
    sep = payload.get("separator", "newline")
    parts = raw.split("\n") if sep == "newline" else raw.replace("，", ",").split(",")
    names = [p.strip() for p in parts if p.strip()]

    data = _load()
    cfg = _find_config(data, config_id)
    items = cfg.setdefault("items", [])
    added, skipped = [], []
    for name in names:
        if len(items) >= MAX_ITEMS:
            break
        if any(it["text"] == name for it in items):
            skipped.append(name)
            continue
        if len(name) > 40:
            skipped.append(name)
            continue
        item = _sanitize_item({"text": name})
        items.append(item)
        added.append(item["text"])
    _save(data)
    return {"added": added, "skipped": skipped, "total": len(items)}


@app.post("/api/configs/{config_id}/items/clear_used")
def clear_used(config_id: str):
    data = _load()
    cfg = _find_config(data, config_id)
    n = 0
    for it in cfg.get("items", []):
        if it.get("used"):
            it["used"] = False
            n += 1
    _save(data)
    return {"ok": True, "cleared": n}


@app.post("/api/configs/{config_id}/items/mark_used")
def mark_used(config_id: str, payload: Dict[str, Any]):
    """把指定关键词标记为已中 (或全部清空 used)。"""
    item_id = payload.get("item_id")
    used = bool(payload.get("used", True))
    data = _load()
    cfg = _find_config(data, config_id)
    if item_id is None:  # 清空全部
        return clear_used(config_id)
    item = next((it for it in cfg.get("items", []) if it["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="关键词不存在")
    item["used"] = used
    _save(data)
    return item


# ───────────────────────── API: 历史 ─────────────────────────
@app.get("/api/history")
def get_history(limit: int = 20):
    data = _load()
    return {"history": data.get("history", [])[: max(1, min(limit, MAX_HISTORY))]}


@app.post("/api/history")
def add_history(payload: Dict[str, Any]):
    data = _load()
    cfg_name = str(payload.get("config_name", ""))[:20]
    text = str(payload.get("text", ""))[:40]
    entry = {
        "id": _new_id("h"),
        "config_id": payload.get("config_id", ""),
        "config_name": cfg_name,
        "text": text,
        "ts": int(time.time()),
    }
    data.setdefault("history", []).insert(0, entry)
    data["history"] = data["history"][:MAX_HISTORY]
    _save(data)
    return entry


@app.delete("/api/history")
def clear_history():
    data = _load()
    data["history"] = []
    _save(data)
    return {"ok": True}


# ───────────────────────── 静态页面 ─────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    print("[wheel] 启动 http://127.0.0.1:3200", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=3200, log_level="info")