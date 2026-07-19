from __future__ import annotations

import json
import re
import time
import logging
import urllib.parse
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.models import AiDraft, DraftAsset, PublishJob, User, AiGeneratedAsset
from backend.app.api.ai import _text_model_context, _recorded_text_task, get_text_ai_client
from backend.app.services.ai_service import TextAiClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weibo", tags=["weibo"])

# Cache session and its generated time
_SESSION_CACHE = {
    "session": None,
    "created_at": 0.0
}
SESSION_TTL = 3600  # Refresh visitor session every 1 hour

class GenerateDraftFromHotSearchRequest(BaseModel):
    word: str = Field(min_length=1, max_length=256)
    instruction: Optional[str] = None
    reference_tweets: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)


def _get_visitor_session(force_refresh: bool = False) -> Any:
    import requests
    
    now = time.time()
    cached = _SESSION_CACHE["session"]
    created_at = _SESSION_CACHE["created_at"]
    
    if cached and not force_refresh and (now - created_at < SESSION_TTL):
        return cached

    logger.info("Initializing new Weibo visitor session...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://weibo.com/"
    })
    
    try:
        # Step 1: Visit main page or directly genvisitor
        fp = '{"os":"1","browser":"Gecko60,0,0,0","fonts":"undefined","screenInfo":"1920*1080*24","plugins":""}'
        gen_url = "https://passport.weibo.com/visitor/genvisitor"
        res = session.post(gen_url, data={"cb": "gen_callback", "fp": fp}, timeout=10)
        
        match = re.search(r'gen_callback\((.*?)\)', res.text)
        if not match:
            raise RuntimeError("Failed to parse visitor callback response")
            
        data = json.loads(match.group(1))
        tid = data.get("data", {}).get("tid")
        if not tid:
            raise RuntimeError("Visitor TID not found in response")
            
        # Step 2: Get visitor incarnated cookies
        incarnate_url = f"https://passport.weibo.com/visitor/visitor?a=incarnate&t={tid}&w=2&c=100&gc=&cb=cross_domain&from=weibo"
        res2 = session.get(incarnate_url, timeout=10)
        res2.raise_for_status()
        
        # Session now contains SUB, SUBP cookies
        _SESSION_CACHE["session"] = session
        _SESSION_CACHE["created_at"] = now
        logger.info("Weibo visitor session successfully established.")
        return session
    except Exception as e:
        logger.error(f"Failed to establish Weibo visitor session: {e}")
        # Return a fallback session without visitor cookies
        fallback_session = requests.Session()
        fallback_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://weibo.com/"
        })
        return fallback_session


def _clean_html_tags(raw_html: str) -> str:
    """Helper to remove HTML tags like <a> or <br> from Weibo tweet content."""
    cleanr = re.compile(r'<[^>]+>')
    cleantext = re.sub(cleanr, '', raw_html)
    # Decode html entities if any
    import html
    return html.unescape(cleantext).strip()


def _strip_markdown(text: str) -> str:
    if not text:
        return text
    # Strip bold (**text** or __text__)
    text = re.sub(r'(\*\*|__)(.*?)\1', r'\2', text)
    # Strip italic (*text* or _text_)
    text = re.sub(r'(\*|_)(.*?)\1', r'\2', text)
    # Strip headers (# text)
    text = re.sub(r'(?m)^#{1,6}\s+', '', text)
    # Strip inline code (`text`)
    text = re.sub(r'`(.*?)`', r'\1', text)
    # Remove any leftover double asterisks
    text = text.replace("**", "").replace("__", "")
    return text



@router.get("/hot-search")
def get_weibo_hot_search() -> dict[str, Any]:
    import requests
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://s.weibo.com/',
            'Accept': 'application/json'
        }
        res = requests.get('https://weibo.com/ajax/side/hotSearch', headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json().get('data', {})
        
        realtime = data.get('realtime', [])
        cleaned_list = []
        rank_counter = 1
        for item in realtime:
            # Skip promotional items if flag is 1 (sometimes flag=1 means ad)
            if item.get('is_ad') or item.get('flag') == 1:
                continue
            
            # Map labels
            label = ""
            icon_desc = item.get('label_name', '').strip()
            if icon_desc:
                label = icon_desc
                
            cleaned_list.append({
                "rank": rank_counter,
                "word": item.get("word", ""),
                "num": item.get("num", 0),
                "label": label
            })
            rank_counter += 1
            if rank_counter > 50:
                break
                
        return {"items": cleaned_list}
    except Exception as e:
        logger.error(f"Failed to fetch Weibo hot search: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"获取微博热搜失败: {e}"
        )


@router.get("/hot-search/tweets")
def get_hot_search_tweets(keyword: str) -> dict[str, Any]:
    import urllib.parse
    
    # Try with cached session first, if redirects, refresh session
    for attempt in range(2):
        try:
            session = _get_visitor_session(force_refresh=(attempt > 0))
            q_encoded = urllib.parse.quote(keyword)
            search_url = f"https://weibo.com/ajax/statuses/search?q={q_encoded}"
            
            res = session.get(search_url, timeout=10)
            res.raise_for_status()
            
            data = res.json()
            # If ok is -100, we are redirected / not logged in (session expired)
            if data.get("ok") == -100:
                if attempt == 0:
                    logger.warning("Weibo visitor session expired, retrying with fresh session...")
                    continue
                else:
                    raise RuntimeError("Weibo session auth failed (-100)")
            
            statuses = data.get("statuses", [])
            tweets = []
            
            for status_item in statuses[:5]:
                text_raw = status_item.get("text", "")
                text_clean = _clean_html_tags(text_raw)
                
                # Fetch pictures if any
                image_urls = []
                pic_ids = status_item.get("pic_ids") or []
                pic_infos = status_item.get("pic_infos") or {}
                for pid in pic_ids:
                    info = pic_infos.get(pid)
                    if info:
                        # Prefer large size, fallbacks
                        url = info.get("large", {}).get("url") or info.get("original", {}).get("url") or info.get("thumbnail", {}).get("url")
                        if url:
                            proxy_url = f"/api/weibo/hot-search/image-proxy?url={urllib.parse.quote(url)}"
                            image_urls.append(proxy_url)
                            
                tweets.append({
                    "id": status_item.get("idstr") or str(status_item.get("id")),
                    "text": text_clean,
                    "created_at": status_item.get("created_at"),
                    "author": status_item.get("user", {}).get("screen_name", "未知用户"),
                    "image_urls": image_urls
                })
                
            return {"items": tweets}
        except Exception as e:
            if attempt == 1:
                logger.error(f"Failed to fetch tweets for keyword {keyword}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"检索热搜推文失败: {e}"
                )


@router.get("/hot-search/image-proxy")
def weibo_image_proxy(url: str):
    import requests
    if "sinaimg.cn" not in url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持代理新浪微博图片")
        
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://weibo.com/'
        }
        # verify=False is necessary due to frequent broken certificates on Sina CDN
        res = requests.get(url, headers=headers, verify=False, timeout=15)
        res.raise_for_status()
        
        content_type = res.headers.get("Content-Type") or "image/jpeg"
        from fastapi.responses import Response
        return Response(content=res.content, media_type=content_type)
    except Exception as e:
        logger.error(f"Failed to proxy Weibo image {url}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"代理图片失败: {e}"
        )


@router.post("/hot-search/generate-draft")
def generate_draft_from_hot_search(
    payload: GenerateDraftFromHotSearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    text_ai_client: TextAiClient = Depends(get_text_ai_client),
):
    model_config, api_key = _text_model_context(db, current_user)
    
    # 1. Prepare references and instructions
    reference_material = "\n\n".join(
        f"微博参考内容 {idx+1}:\n{tweet}" 
        for idx, tweet in enumerate(payload.reference_tweets)
    )
    
    default_instruction = (
        "将此热搜主题改写为一篇吸引人的小红书图文笔记。风格活泼、口语化，"
        "使用大量适宜的表情符号（Emoji）增加趣味，并自动推荐3-5个小红书话题。"
    )
    instruction = payload.instruction or default_instruction
    
    # 2. Call AI generate note
    task, result = _recorded_text_task(
        db=db,
        current_user=current_user,
        platform="xhs",
        task_type="weibo_hot_to_xhs",
        payload={"model_config_id": model_config.id, "topic": payload.word},
        action=lambda: text_ai_client.generate_note(
            model_config=model_config,
            api_key=api_key,
            topic=payload.word,
            reference=reference_material,
            instruction=instruction,
        ),
    )
    
    body_raw = result.get("body") or ""
    title_raw = result.get("title") or payload.word
    
    # 3. Parse tags from AI rewritten body
    # Standard Xiaohongshu tags are preceded by '#'
    raw_tags = re.findall(r'#([^\s#，,]+)', body_raw)
    tags = []
    seen_tags = set()
    for t in raw_tags:
        cleaned = t.strip()
        if cleaned and cleaned not in seen_tags:
            seen_tags.add(cleaned)
            tags.append({"id": "", "name": cleaned})
            
    body = _strip_markdown(body_raw)
    title = _strip_markdown(title_raw)
            
    # 4. Create AiDraft
    draft = AiDraft(
        user_id=current_user.id,
        platform="xhs",
        title=title,
        body=body,
        tags=tags,
    )
    db.add(draft)
    db.flush()
    
    # 5. Bind selected Weibo image URLs as DraftAssets
    if payload.image_urls:
        for idx, url in enumerate(payload.image_urls[:9]):  # XHS allows up to 9 images
            orig_url = url
            if "/image-proxy?url=" in url:
                parts = url.split("?url=")
                if len(parts) > 1:
                    orig_url = urllib.parse.unquote(parts[1])
                    
            db.add(DraftAsset(
                draft_id=draft.id,
                asset_type="image",
                url=orig_url,
                local_path="",
                sort_order=idx
            ))
    else:
        # If no Weibo images are available or selected, automatically generate a cover with AI
        try:
            from backend.app.api.ai import _image_model_context, get_image_ai_client
            # Verify if default image model is configured
            model_config, api_key = _image_model_context(db, current_user)
            image_ai_client = get_image_ai_client()
            
            # Formulate painting prompt
            paint_prompt = f"小红书风格插画，主题是：{title}。画面精美，色彩饱和，具有高度设计感与吸引力。"
            
            # Call AI draw
            logger.info(f"Generating automatic AI cover for draft #{draft.id} with prompt: {paint_prompt}")
            res = image_ai_client.generate_cover(
                model_config=model_config,
                api_key=api_key,
                prompt=paint_prompt,
                size="1024x1024",
                style="vivid"
            )
            img_url = res.get("url")
            if img_url:
                db.add(DraftAsset(
                    draft_id=draft.id,
                    asset_type="image",
                    url=img_url,
                    local_path="",
                    sort_order=0
                ))
                logger.info(f"Successfully generated and bound AI cover for draft #{draft.id}: {img_url}")
        except Exception as e:
            logger.warning(f"Skipped automatic AI cover generation for draft #{draft.id}: {e}")
        
    task.payload = {**(task.payload or {}), "result_draft_id": draft.id}
    db.commit()
    db.refresh(draft)
    
    return {"draft_id": draft.id, "title": title}
