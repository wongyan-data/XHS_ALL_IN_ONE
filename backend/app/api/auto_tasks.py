from __future__ import annotations

import random
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.platforms.xhs.crawl import _data_items, _normalize_search_item
from backend.app.api.platforms.xhs.pc import (
    _get_owned_pc_account_cookies,
    get_xhs_pc_api_adapter_factory,
)
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.security import decrypt_text
from backend.app.core.time import shanghai_now
from backend.app.models import (
    AccountCookieVersion,
    AiDraft,
    AutoTask,
    ModelConfig,
    PlatformAccount,
    PublishAsset,
    PublishJob,
    Task,
    User,
)
from backend.app.schemas.common import paginated
from backend.app.services.ai_service import OpenAICompatibleTextClient

router = APIRouter(prefix="/auto-tasks", tags=["auto-tasks"])


class AutoTaskCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    keywords: list[str] = Field(default_factory=list)
    task_type: str = Field(default="xhs_keyword", pattern="^(xhs_keyword|weibo_hot|weibo_entertainment|group_consolidation)$")
    pc_account_id: Optional[int] = None
    creator_account_id: int
    ai_instruction: str = Field(default="", max_length=2000)
    schedule_type: str = Field(default="manual", pattern="^(manual|daily|weekly|interval)$")
    schedule_time: str = Field(default="09:00", max_length=5)
    schedule_days: str = Field(default="", max_length=64)
    schedule_interval_hours: int = Field(default=24, ge=1, le=168)


class AutoTaskUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    keywords: Optional[list[str]] = None
    task_type: Optional[str] = Field(default=None, pattern="^(xhs_keyword|weibo_hot|weibo_entertainment|group_consolidation)$")
    ai_instruction: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = Field(default=None, pattern="^(active|paused|completed)$")
    schedule_type: Optional[str] = Field(default=None, pattern="^(manual|daily|weekly|interval)$")
    schedule_time: Optional[str] = Field(default=None, max_length=5)
    schedule_days: Optional[str] = Field(default=None, max_length=64)
    schedule_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)


def _serialize_auto_task(task: AutoTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "name": task.name,
        "task_type": getattr(task, "task_type", "xhs_keyword"),
        "keywords": task.keywords or [],
        "pc_account_id": task.pc_account_id,
        "creator_account_id": task.creator_account_id,
        "ai_instruction": task.ai_instruction,
        "status": task.status,
        "schedule_type": task.schedule_type,
        "schedule_time": task.schedule_time,
        "schedule_days": task.schedule_days,
        "schedule_interval_hours": task.schedule_interval_hours,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "total_published": task.total_published,
        "created_at": task.created_at.isoformat(),
    }


def _get_owned_auto_task(db: Session, current_user: User, task_id: int) -> AutoTask:
    auto_task = db.scalars(
        select(AutoTask).where(AutoTask.id == task_id, AutoTask.user_id == current_user.id)
    ).first()
    if auto_task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto task not found")
    return auto_task


def _verify_account_ownership(db: Session, current_user: User, account_id: int, expected_sub_type: str) -> PlatformAccount:
    account = db.get(PlatformAccount, account_id)
    if (
        account is None
        or account.user_id != current_user.id
        or account.platform != "xhs"
        or account.sub_type != expected_sub_type
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"XHS {expected_sub_type} account not found",
        )
    return account


def _get_account_cookies(db: Session, account_id: int) -> str:
    from backend.app.api.publish import _cookies_to_string

    cookie_version = db.scalars(
        select(AccountCookieVersion)
        .where(AccountCookieVersion.platform_account_id == account_id)
        .order_by(AccountCookieVersion.created_at.desc(), AccountCookieVersion.id.desc())
    ).first()
    if cookie_version is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account has no cookies")
    return _cookies_to_string(decrypt_text(cookie_version.encrypted_cookies))


def _calculate_next_run_at(task: AutoTask) -> None:
    from datetime import timedelta
    now = shanghai_now()
    if task.schedule_type == "manual":
        task.next_run_at = None
    elif task.schedule_type == "daily":
        h, m = (task.schedule_time or "09:00").split(":")
        next_time = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        if next_time <= now:
            next_time += timedelta(days=1)
        task.next_run_at = next_time
    elif task.schedule_type == "weekly":
        h, m = (task.schedule_time or "09:00").split(":")
        days = [int(d) for d in (task.schedule_days or "").split(",") if d.strip().isdigit()]
        if not days:
            task.next_run_at = None
            return
        for offset in range(1, 8):
            candidate = now + timedelta(days=offset)
            if candidate.isoweekday() in days:
                task.next_run_at = candidate.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                return
    elif task.schedule_type == "interval":
        task.next_run_at = now + timedelta(hours=task.schedule_interval_hours)


@router.get("")
def list_auto_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tasks = db.scalars(
        select(AutoTask)
        .where(AutoTask.user_id == current_user.id)
        .order_by(AutoTask.created_at.desc(), AutoTask.id.desc())
    ).all()
    return paginated([_serialize_auto_task(t) for t in tasks], page, page_size)


@router.post("")
def create_auto_task(
    payload: AutoTaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.task_type in ("xhs_keyword", "group_consolidation"):
        if payload.task_type == "xhs_keyword" and not payload.pc_account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="小红书关键词监控任务必须选择 PC 账号")
        if payload.pc_account_id:
            _verify_account_ownership(db, current_user, payload.pc_account_id, "pc")
    _verify_account_ownership(db, current_user, payload.creator_account_id, "creator")

    pc_id = payload.pc_account_id if payload.task_type in ("xhs_keyword", "group_consolidation") else payload.creator_account_id

    auto_task = AutoTask(
        user_id=current_user.id,
        name=payload.name,
        task_type=payload.task_type,
        keywords=payload.keywords,
        pc_account_id=pc_id,
        creator_account_id=payload.creator_account_id,
        ai_instruction=payload.ai_instruction,
        schedule_type=payload.schedule_type,
        schedule_time=payload.schedule_time,
        schedule_days=payload.schedule_days,
        schedule_interval_hours=payload.schedule_interval_hours,
        status="active",
    )
    _calculate_next_run_at(auto_task)
    db.add(auto_task)
    db.commit()
    db.refresh(auto_task)
    return _serialize_auto_task(auto_task)


@router.patch("/{task_id}")
def update_auto_task(
    task_id: int,
    payload: AutoTaskUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auto_task = _get_owned_auto_task(db, current_user, task_id)

    if payload.name is not None:
        auto_task.name = payload.name
    if payload.task_type is not None:
        auto_task.task_type = payload.task_type
    if payload.keywords is not None:
        auto_task.keywords = payload.keywords
    if payload.ai_instruction is not None:
        auto_task.ai_instruction = payload.ai_instruction
    if payload.status is not None:
        auto_task.status = payload.status

    schedule_changed = False
    if payload.schedule_type is not None:
        auto_task.schedule_type = payload.schedule_type
        schedule_changed = True
    if payload.schedule_time is not None:
        auto_task.schedule_time = payload.schedule_time
        schedule_changed = True
    if payload.schedule_days is not None:
        auto_task.schedule_days = payload.schedule_days
        schedule_changed = True
    if payload.schedule_interval_hours is not None:
        auto_task.schedule_interval_hours = payload.schedule_interval_hours
        schedule_changed = True
    if schedule_changed:
        _calculate_next_run_at(auto_task)

    db.commit()
    db.refresh(auto_task)
    return _serialize_auto_task(auto_task)


@router.delete("/{task_id}")
def delete_auto_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auto_task = _get_owned_auto_task(db, current_user, task_id)
    db.delete(auto_task)
    db.commit()
    return {"id": task_id, "status": "deleted"}


def _execute_weibo_auto_task(db: Session, auto_task: AutoTask, tracking_task: Optional[Task] = None) -> dict[str, Any]:
    import random
    import requests
    import urllib.parse
    import json
    import re
    import logging
    from backend.app.services.ai_service import OpenAICompatibleTextClient
    from backend.app.api.weibo import _clean_html_tags, _get_visitor_session, _strip_markdown
    from backend.app.models import AiDraft, PublishJob, PublishAsset, DraftAsset, AccountCookieVersion, ModelConfig
    from backend.app.core.time import shanghai_now
    from backend.app.core.security import decrypt_text
    from backend.app.adapters.xhs.creator_api_adapter import XhsCreatorApiAdapter
    
    local_logger = logging.getLogger(__name__)

    # 1 & 2. Fetch and filter topics if hot search task
    topic_label = ""
    if auto_task.task_type in ("weibo_hot", "weibo_entertainment"):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://s.weibo.com/',
            'Accept': 'application/json'
        }
        
        topics = []
        if auto_task.task_type == "weibo_entertainment":
            # Fetch Weibo Entertainment Board from s.weibo.com
            session = _get_visitor_session()
            url = 'https://s.weibo.com/top/summary?cate=entrank'
            res = session.get(url, timeout=10)
            res.raise_for_status()
            
            # Regex search for class td-02 td cells
            pattern = re.compile(r'<td class="td-02">.*?<a href="/weibo\?q=([^&"]+).*?>(.*?)</a>', re.DOTALL)
            matches = pattern.findall(res.text)
            
            for idx, (q, text) in enumerate(matches):
                word = urllib.parse.unquote(q).strip("#")
                num = 0
                label = ""
                # Find span following the link inside that td block to extract category name and search volume
                td_pattern = re.compile(r'<td class="td-02">.*?<a href="/weibo\?q=' + re.escape(q) + r'.*?>(.*?)</a>.*?<span>(.*?)</span>', re.DOTALL)
                td_match = td_pattern.search(res.text)
                if td_match:
                    span_content = td_match.group(2).strip()
                    span_parts = span_content.split()
                    if span_parts:
                        if len(span_parts) > 1 and not span_parts[0].isdigit():
                            label = span_parts[0]
                            num = int(span_parts[1]) if span_parts[1].isdigit() else 0
                        elif span_parts[0].isdigit():
                            num = int(span_parts[0])
                
                topics.append({
                    "word": word,
                    "num": num,
                    "label": label
                })
        else:
            # Fetch Weibo Main Hot Search Board
            url = 'https://weibo.com/ajax/side/hotSearch'
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            realtime = res.json().get('data', {}).get('realtime', [])
            for item in realtime:
                if item.get('is_ad') or item.get('flag') == 1:
                    continue
                topics.append({
                    "word": item.get("word", ""),
                    "num": item.get("num", 0),
                    "label": item.get("label_name", "").strip() or item.get("flag_desc", "").strip()
                })

        # Filter topics based on keywords
        keywords = auto_task.keywords or []
        matched_topics = []
        if keywords:
            for t in topics:
                word_lower = t["word"].lower()
                label_lower = t["label"].lower() if t["label"] else ""
                if any(k.lower() in word_lower or k.lower() in label_lower for k in keywords):
                    matched_topics.append(t)
        else:
            # If no keywords are configured, match all
            matched_topics = topics
            
        if not matched_topics:
            msg = "没有匹配当前关键词/分类过滤器的微博热搜"
            if tracking_task:
                tracking_task.status = "failed"
                tracking_task.progress = 100
                tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
                db.commit()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
            
        # Pick the top-ranking topic (first item after filtering)
        best_topic = matched_topics[0]
        keyword = best_topic["word"]
        topic_label = best_topic["label"]
    else:
        # group_consolidation task type - directly pick keyword from keywords
        keywords = auto_task.keywords or []
        if not keywords:
            msg = "未配置特定团体监控关键词"
            if tracking_task:
                tracking_task.status = "failed"
                tracking_task.progress = 100
                tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
                db.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        keyword = random.choice(keywords)
        topic_label = "特定团体监控"
        
    if tracking_task:
        tracking_task.progress = 30
        tracking_task.payload = {
            **(tracking_task.payload or {}),
            "keyword": keyword,
            "topic_label": topic_label
        }
        db.flush()
        
    # 4. Search tweets for this topic (robust with session retry)
    statuses = []
    for attempt in range(2):
        try:
            session = _get_visitor_session(force_refresh=(attempt > 0))
            q_encoded = urllib.parse.quote(keyword)
            search_url = f"https://weibo.com/ajax/statuses/search?q={q_encoded}"
            res = session.get(search_url, timeout=10)
            res.raise_for_status()
            
            data = res.json()
            if data.get("ok") == -100:
                if attempt == 0:
                    local_logger.warning("Weibo visitor session expired in auto task, retrying with fresh session...")
                    continue
                else:
                    raise RuntimeError("Weibo session auth failed in auto task (-100)")
            
            statuses = data.get("statuses", [])
            break
        except Exception as e:
            if attempt == 1:
                local_logger.error(f"Auto task {auto_task.id} failed to fetch tweets for keyword {keyword}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"检索热搜推文失败: {e}"
                )

    if not statuses:
        msg = f"未找到关于该热搜的推文背景: {keyword}"
        if tracking_task:
            tracking_task.status = "failed"
            tracking_task.progress = 100
            tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
            db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        
    # Extract reference material (first 3 tweets) and pictures (scan up to 15 tweets)
    reference_tweets = []
    selected_images = []
    
    for s in statuses[:3]:
        text_clean = _clean_html_tags(s.get("text", ""))
        reference_tweets.append(text_clean)
        
    for s in statuses[:15]:
        pic_ids = s.get("pic_ids") or []
        pic_infos = s.get("pic_infos") or {}
        
        # If this tweet is a retweet and has no pictures on the retweet text, look inside the original tweet
        if not pic_ids and s.get("retweeted_status"):
            retweeted = s.get("retweeted_status")
            pic_ids = retweeted.get("pic_ids") or []
            pic_infos = retweeted.get("pic_infos") or {}
            
        for pid in pic_ids:
            info = pic_infos.get(pid)
            if info:
                img_url = info.get("large", {}).get("url") or info.get("original", {}).get("url") or info.get("thumbnail", {}).get("url")
                if img_url and img_url not in selected_images:
                    selected_images.append(img_url)
                    
    xhs_notes = []
    web_results = []
    
    if auto_task.task_type == "group_consolidation":
        # Search XHS notes for target group
        if auto_task.pc_account_id:
            try:
                pc_account = db.get(PlatformAccount, auto_task.pc_account_id)
                if pc_account:
                    pc_cookies = _get_account_cookies(db, auto_task.pc_account_id)
                    from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter
                    adapter = XhsPcApiAdapter(pc_cookies)
                    success, msg, raw_payload = adapter.search_note(keyword, page=1)
                    if success:
                        from backend.app.api.platforms.xhs.crawl import _data_items, _normalize_search_item
                        items = _data_items(raw_payload)
                        xhs_notes = [_normalize_search_item(item) for item in items][:5]
            except Exception as exc:
                local_logger.warning(f"Auto task {auto_task.id} XHS note search failed: {exc}")
                
        # Search Web (Baidu) for target group
        try:
            from backend.app.services.search_service import search_baidu
            web_results = search_baidu(keyword, limit=5)
        except Exception as exc:
            local_logger.warning(f"Auto task {auto_task.id} web search failed: {exc}")
            
        # Collect XHS note cover images
        for n in xhs_notes:
            img_list = n.get("image_urls") or []
            for url in img_list:
                if url and url not in selected_images:
                    selected_images.append(url)
                    
        # Consolidate reference material
        ref_parts = []
        if reference_tweets:
            ref_parts.append("### 微博热议动态：\n" + "\n".join(
                f"- {tweet}" for tweet in reference_tweets
            ))
        if xhs_notes:
            ref_parts.append("### 小红书热门笔记动态：\n" + "\n".join(
                f"- 标题: {n.get('title')} | 摘要: {n.get('content', '')[:100]}"
                for n in xhs_notes if n.get('title')
            ))
        if web_results:
            ref_parts.append("### 网页最新新闻与动态摘要：\n" + "\n".join(
                f"- 标题: {r['title']} | 摘要: {r['snippet']}"
                for r in web_results
            ))
        reference_material = "\n\n".join(ref_parts)
        
        ai_instruction = auto_task.ai_instruction or (
            "你是一个资深垂直领域运营博主。请根据提供的微博动态、小红书热点以及最新网页新闻，"
            "进行内容整合和情报提炼，为粉丝撰写一篇信息量饱满、语气活泼的小红书每日动态追踪日报。"
            "合理搭配表情符号（Emoji），并在末尾推荐3-5个小红书话题。"
        )
    else:
        reference_material = "\n\n".join(
            f"微博参考内容 {idx+1}:\n{tweet}" 
            for idx, tweet in enumerate(reference_tweets)
        )
        ai_instruction = auto_task.ai_instruction or (
            "将此热搜主题改写为一篇吸引人的小红书图文笔记。风格活泼、口语化，"
            "使用大量适宜的表情符号（Emoji）增加趣味，并自动推荐3-5个小红书话题。"
        )
        
    # 5. Call AI text client to rewrite title & body
    model_config = db.scalars(
        select(ModelConfig).where(
            ModelConfig.user_id == auto_task.user_id,
            ModelConfig.model_type == "text",
            ModelConfig.is_default.is_(True),
        )
    ).first()
    if model_config is None:
        msg = "未配置默认文本模型"
        if tracking_task:
            tracking_task.status = "failed"
            tracking_task.progress = 100
            tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
            db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        
    api_key = decrypt_text(model_config.encrypted_api_key) if model_config.encrypted_api_key else ""
    text_client = OpenAICompatibleTextClient()
    
    ai_result = text_client.generate_note(
        model_config=model_config,
        api_key=api_key,
        topic=keyword,
        reference=reference_material,
        instruction=ai_instruction,
    )
    
    raw_body = ai_result.get("body") or ""
    raw_title = ai_result.get("title") or keyword
    
    # Parse tags from raw body before stripping
    raw_tags = re.findall(r'#([^\s#，,]+)', raw_body)
    tags = []
    seen_tags = set()
    for t in raw_tags:
        cleaned = t.strip()
        if cleaned and cleaned not in seen_tags:
            seen_tags.add(cleaned)
            tags.append({"id": "", "name": cleaned})
            
    body = _strip_markdown(raw_body)
    title = _strip_markdown(raw_title)
    
    # Create AiDraft
    draft = AiDraft(
        user_id=auto_task.user_id,
        platform="xhs",
        title=title,
        body=body,
        tags=tags,
    )
    db.add(draft)
    db.flush()
    
    if tracking_task:
        tracking_task.progress = 70
        tracking_task.payload = {**(tracking_task.payload or {}), "draft_id": draft.id}
        db.flush()
        
    # 6. Fallback cover generation if no images
    img_urls = []
    if selected_images:
        img_urls = selected_images[:9]
    else:
        # Generate cover (prefer AI, fallback to PIL plain text cover)
        ai_img_url = None
        try:
            from backend.app.api.ai import _image_model_context, get_image_ai_client
            img_model_config, img_api_key = _image_model_context(db, auto_task.user_id)
            image_ai_client = get_image_ai_client()
            paint_prompt = f"小红书风格插画，主题是：{title}。画面精美，色彩饱和，具有高度设计感与吸引力。"
            res = image_ai_client.generate_cover(
                model_config=img_model_config,
                api_key=img_api_key,
                prompt=paint_prompt,
                size="1024x1024",
                style="vivid"
            )
            ai_img_url = res.get("url")
        except Exception as e:
            local_logger.warning(f"Auto task {auto_task.id} AI image generation failed: {e}")
            
        # Draw PIL text cover if AI image fails
        if not ai_img_url:
            try:
                from uuid import uuid4
                from pathlib import Path
                from backend.app.core.config import get_settings
                from backend.app.services.image_util import compose_cover_image
                
                file_name = f"xhs-upload-u{auto_task.user_id}-{uuid4().hex}.png"
                media_dir = Path(get_settings().storage_dir) / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                output_path = media_dir / file_name
                
                compose_cover_image(
                    output_path=output_path,
                    title=title,
                    body=body[:200] + ("..." if len(body) > 200 else ""),
                    width=1080,
                    height=1440,
                    background_color="#fafaf8",
                    accent_color="#111111"
                )
                ai_img_url = f"/api/files/media/{file_name}"
            except Exception as e:
                local_logger.error(f"Auto task {auto_task.id} PIL cover draw failed: {e}")
                
        if ai_img_url:
            img_urls = [ai_img_url]
            
    # Save image assets to Draft
    for idx, url in enumerate(img_urls):
        db.add(DraftAsset(
            draft_id=draft.id,
            asset_type="image",
            url=url,
            local_path="",
            sort_order=idx
        ))
    db.flush()
    
    # 7. Get Creator cookies & Upload assets to Creator API
    creator_cv = db.scalars(
        select(AccountCookieVersion)
        .where(AccountCookieVersion.platform_account_id == auto_task.creator_account_id)
        .order_by(AccountCookieVersion.created_at.desc())
    ).first()
    if not creator_cv:
        msg = "Creator 账号未绑定 Cookies，请先在网页端登录"
        if tracking_task:
            tracking_task.status = "failed"
            tracking_task.progress = 100
            tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
            db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        
    from backend.app.api.publish import _cookies_to_string
    creator_cookies_str = _cookies_to_string(decrypt_text(creator_cv.encrypted_cookies))
        
    creator_adapter = XhsCreatorApiAdapter(creator_cookies_str)
    file_infos = []
    for url in img_urls:
        try:
            payload = creator_adapter.upload_media(url, "image")
            file_infos.append(payload)
        except Exception as exc:
            local_logger.warning(f"Auto task {auto_task.id} image upload failed: {exc}")
            
    if not file_infos:
        msg = "无法将任何配图上传至小红书创作者后台"
        if tracking_task:
            tracking_task.status = "failed"
            tracking_task.progress = 100
            tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
            db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=msg)
        
    # 8. Create PublishJob & Publish Assets
    job = PublishJob(
        user_id=auto_task.user_id,
        platform_account_id=auto_task.creator_account_id,
        source_draft_id=draft.id,
        platform="xhs",
        title=draft.title,
        body=draft.body,
        publish_mode="immediate",
        status="publishing",
    )
    db.add(job)
    db.flush()
    
    for info in file_infos:
        db.add(PublishAsset(
            publish_job_id=job.id,
            asset_type="image",
            file_path="",
            upload_status="uploaded",
            creator_media_id=info.get("fileIds", ""),
            creator_upload_info=json.dumps(info, ensure_ascii=False),
        ))
    db.flush()
    
    if tracking_task:
        tracking_task.progress = 90
        db.flush()
        
    # 9. Post Note
    try:
        note_info = {
            "title": job.title,
            "desc": job.body,
            "media_type": "image",
            "image_file_infos": file_infos,
            "type": 0,
            "postTime": None,
        }
        result = creator_adapter.post_note(note_info)
        job.status = "published"
        job.external_note_id = ""
        for key in ("note_id", "noteId", "id"):
            v = result.get(key) or (result.get("data", {}) or {}).get(key)
            if v:
                job.external_note_id = str(v)
                break
        job.published_at = shanghai_now()
    except Exception as exc:
        job.status = "failed"
        job.publish_error = str(exc)[:500]
        local_logger.warning(f"Auto task {auto_task.id} publish failed: {exc}")
        
    auto_task.total_published = (auto_task.total_published or 0) + 1
    auto_task.last_run_at = shanghai_now()
    _calculate_next_run_at(auto_task)
    
    if tracking_task:
        tracking_task.status = "completed"
        tracking_task.progress = 100
        tracking_task.payload = {
            **(tracking_task.payload or {}),
            "publish_job_id": job.id,
            "rewritten_length": len(body),
        }
        
    db.commit()
    return {
        "auto_task": _serialize_auto_task(auto_task),
        "keyword": keyword,
        "source_note": {
            "note_id": keyword,
            "title": keyword,
            "likes": 0,
            "collects": 0,
            "comments": 0,
        },
        "draft": {
            "id": draft.id,
            "title": draft.title,
            "body": draft.body,
            "created_at": draft.created_at.isoformat(),
        },
        "publish_job": {
            "id": job.id,
            "status": job.status,
            "platform_account_id": job.platform_account_id,
        },
    }


@router.post("/{task_id}/run")
def run_auto_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    adapter_factory=Depends(get_xhs_pc_api_adapter_factory),
):
    auto_task = _get_owned_auto_task(db, current_user, task_id)

    # Verify account ownership (Weibo tasks don't need pc accounts checks)
    if auto_task.task_type in ("xhs_keyword", "group_consolidation"):
        _verify_account_ownership(db, current_user, auto_task.pc_account_id, "pc")
    _verify_account_ownership(db, current_user, auto_task.creator_account_id, "creator")

    # Create a tracking task
    tracking_task = Task(
        user_id=current_user.id,
        platform="xhs",
        task_type="auto_ops_run",
        status="running",
        progress=10,
        payload={"auto_task_id": auto_task.id, "auto_task_name": auto_task.name},
    )
    db.add(tracking_task)
    db.flush()

    # Route according to task_type
    if auto_task.task_type in ("weibo_hot", "weibo_entertainment"):
        return _execute_weibo_auto_task(db, auto_task, tracking_task)

    # 1. Pick a random keyword
    keywords = auto_task.keywords or []
    if not keywords:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": "No keywords configured"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No keywords configured")

    keyword = random.choice(keywords)
    tracking_task.payload = {**(tracking_task.payload or {}), "keyword": keyword}
    db.flush()

    # 2. Search notes using PC adapter
    pc_cookies = _get_owned_pc_account_cookies(db, current_user, auto_task.pc_account_id)
    adapter = adapter_factory(pc_cookies)
    success, message, raw_payload = adapter.search_note(keyword, page=1)
    if not success:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": message or "Search failed"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message or "XHS search failed")

    tracking_task.progress = 30
    db.flush()

    # 3. Normalize and pick top-engagement note
    items = _data_items(raw_payload)
    normalized_items = [_normalize_search_item(item) for item in items]
    if not normalized_items:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": "No notes found for keyword"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No notes found for keyword")

    # Limit to crawl_count and pick best by engagement
    candidates = normalized_items[:10]
    best_note = max(
        candidates,
        key=lambda n: (n.get("likes", 0) + n.get("collects", 0) + n.get("comments", 0) + n.get("shares", 0)),
    )

    tracking_task.progress = 50
    tracking_task.payload = {
        **(tracking_task.payload or {}),
        "source_note_id": best_note.get("note_id"),
        "source_title": best_note.get("title"),
        "candidates_count": len(candidates),
    }
    db.flush()

    # 4. Create an AiDraft from the best note
    draft = AiDraft(
        user_id=current_user.id,
        platform="xhs",
        title=str(best_note.get("title") or ""),
        body=str(best_note.get("content") or ""),
    )
    db.add(draft)
    db.flush()

    tracking_task.progress = 60
    tracking_task.payload = {**(tracking_task.payload or {}), "draft_id": draft.id}
    db.flush()

    # 5. AI rewrite using the task's instruction
    model_config = db.scalars(
        select(ModelConfig).where(
            ModelConfig.user_id == current_user.id,
            ModelConfig.model_type == "text",
            ModelConfig.is_default.is_(True),
        )
    ).first()
    if model_config is None:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": "Default text model not configured"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Default text model is not configured")

    api_key = decrypt_text(model_config.encrypted_api_key) if model_config.encrypted_api_key else ""
    text_client = OpenAICompatibleTextClient()

    try:
        rewritten_body = text_client.rewrite_note(
            model_config=model_config,
            api_key=api_key,
            title=draft.title,
            body=draft.body,
            instruction=auto_task.ai_instruction or "改写为原创小红书笔记，保持核心信息，提升表达和语感",
        )
        draft.body = rewritten_body
    except Exception as exc:
        tracking_task.status = "failed"
        tracking_task.progress = 100
        tracking_task.payload = {**(tracking_task.payload or {}), "error": f"AI rewrite failed: {exc}"}
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AI rewrite failed: {exc}") from exc

    # 5b. Title rewrite (non-fatal)
    try:
        rewritten_title = text_client._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt="你是小红书标题创作专家。",
            user_prompt=f"为以下小红书笔记改写一个吸引人的标题（15字以内）：\n\n原标题：{draft.title}\n\n正文：{draft.body[:200]}",
            temperature=0.8,
        )
        draft.title = rewritten_title.strip().strip('"').strip("'").strip("《》")
    except Exception:
        pass  # title rewrite failure is not fatal

    tracking_task.progress = 80
    db.flush()

    # 6. Get Creator cookies & Upload assets to Creator API
    creator_cv = db.scalars(
        select(AccountCookieVersion)
        .where(AccountCookieVersion.platform_account_id == auto_task.creator_account_id)
        .order_by(AccountCookieVersion.created_at.desc())
    ).first()
    if not creator_cv:
        msg = "Creator 账号未绑定 Cookies，请先在网页端登录"
        if tracking_task:
            tracking_task.status = "failed"
            tracking_task.progress = 100
            tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
            db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        
    from backend.app.api.publish import _cookies_to_string
    creator_cookies_str = _cookies_to_string(decrypt_text(creator_cv.encrypted_cookies))
    creator_adapter = XhsCreatorApiAdapter(creator_cookies_str)
    
    image_urls = best_note.get("image_urls", [])
    if not isinstance(image_urls, list):
        image_urls = []
        
    file_infos = []
    for url in image_urls[:9]:
        if isinstance(url, str) and url:
            try:
                payload = creator_adapter.upload_media(url, "image")
                file_infos.append(payload)
            except Exception as exc:
                local_logger.warning(f"Auto task {auto_task.id} image upload failed: {exc}")
                
    # If no images found on source note, draw cover image
    if not file_infos:
        try:
            from uuid import uuid4
            from pathlib import Path
            from backend.app.core.config import get_settings
            from backend.app.services.image_util import compose_cover_image
            
            file_name = f"xhs-upload-u{auto_task.user_id}-{uuid4().hex}.png"
            media_dir = Path(get_settings().storage_dir) / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            output_path = media_dir / file_name
            
            compose_cover_image(
                output_path=output_path,
                title=draft.title,
                body=draft.body[:200] + ("..." if len(draft.body) > 200 else ""),
                width=1080,
                height=1440,
                background_color="#fafaf8",
                accent_color="#111111"
            )
            cover_url = f"/api/files/media/{file_name}"
            payload = creator_adapter.upload_media(cover_url, "image")
            file_infos.append(payload)
        except Exception as exc:
            local_logger.error(f"Auto task {auto_task.id} PIL cover upload failed: {exc}")

    if not file_infos:
        msg = "无法将任何配图上传至小红书创作者后台"
        if tracking_task:
            tracking_task.status = "failed"
            tracking_task.progress = 100
            tracking_task.payload = {**(tracking_task.payload or {}), "error": msg}
            db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=msg)

    # Create PublishJob & Publish Assets
    publish_job = PublishJob(
        user_id=current_user.id,
        platform_account_id=auto_task.creator_account_id,
        source_draft_id=draft.id,
        platform="xhs",
        title=draft.title,
        body=draft.body,
        publish_mode="immediate",
        status="publishing",
    )
    db.add(publish_job)
    db.flush()

    for info in file_infos:
        db.add(PublishAsset(
            publish_job_id=publish_job.id,
            asset_type="image",
            file_path="",
            upload_status="uploaded",
            creator_media_id=info.get("fileIds", ""),
            creator_upload_info=json.dumps(info, ensure_ascii=False),
        ))
    db.flush()

    # Post Note to Creator API immediately
    try:
        note_info = {
            "title": publish_job.title,
            "desc": publish_job.body,
            "media_type": "image",
            "image_file_infos": file_infos,
            "type": 0,
            "postTime": None,
        }
        result = creator_adapter.post_note(note_info)
        publish_job.status = "published"
        publish_job.external_note_id = ""
        for key in ("note_id", "noteId", "id"):
            v = result.get(key) or (result.get("data", {}) or {}).get(key)
            if v:
                publish_job.external_note_id = str(v)
                break
        publish_job.published_at = shanghai_now()
    except Exception as exc:
        publish_job.status = "failed"
        publish_job.publish_error = str(exc)[:500]
        local_logger.warning(f"Auto task {auto_task.id} publish failed: {exc}")

    # 7. Update auto task counters
    auto_task.total_published = (auto_task.total_published or 0) + 1
    auto_task.last_run_at = shanghai_now()
    _calculate_next_run_at(auto_task)

    tracking_task.status = "completed"
    tracking_task.progress = 100
    tracking_task.payload = {
        **(tracking_task.payload or {}),
        "publish_job_id": publish_job.id,
        "rewritten_length": len(draft.body),
    }

    db.commit()
    db.refresh(auto_task)
    db.refresh(draft)
    db.refresh(publish_job)

    return {
        "auto_task": _serialize_auto_task(auto_task),
        "keyword": keyword,
        "source_note": {
            "note_id": best_note.get("note_id"),
            "title": best_note.get("title"),
            "likes": best_note.get("likes", 0),
            "collects": best_note.get("collects", 0),
            "comments": best_note.get("comments", 0),
        },
        "draft": {
            "id": draft.id,
            "title": draft.title,
            "body": draft.body,
            "created_at": draft.created_at.isoformat(),
        },
        "publish_job": {
            "id": publish_job.id,
            "status": publish_job.status,
            "platform_account_id": publish_job.platform_account_id,
        },
    }
