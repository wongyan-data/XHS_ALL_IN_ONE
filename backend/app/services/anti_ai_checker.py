import re
from typing import Any, Dict, List, Optional

def check_anti_ai(title: str, body: str, voice: Optional[str] = None) -> Dict[str, Any]:
    """
    Checks the given title and body against the 9 Anti-AI rules.
    Returns:
        score: int (0 to 100)
        passed_rules_count: int (0 to 9)
        results: List[Dict[str, Any]] (detailed list of the 9 checks)
        suggestion: str (overall suggestion)
    """
    results = []
    
    # Preprocess body to extract paragraphs and sentences
    paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
    
    # Split sentences using common punctuation marks
    sentence_delimiters = re.compile(r"[。！\n？!?]")
    sentences = [s.strip() for s in sentence_delimiters.split(body) if s.strip()]
    
    # ----------------------------------------------------
    # ① Burstiness (句长抖动)
    # ----------------------------------------------------
    burstiness_passed = True
    burstiness_details = []
    burstiness_violations = []
    
    # Check 1: Adjacent three sentence length variation
    has_large_diff = False
    if len(sentences) >= 2:
        for i in range(len(sentences) - 1):
            diff = abs(len(sentences[i]) - len(sentences[i+1]))
            if diff > 15:
                has_large_diff = True
                break
    if not has_large_diff:
        burstiness_passed = False
        burstiness_details.append("缺少句长抖动：相邻两句话的字数差没有出现过大于15字的情况。建议穿插极长或极短的句子。")
        
    # Check 2: Insertion of short sentences (5-12 chars) after long sentences
    long_sentences = [s for s in sentences if len(s) > 25]
    short_sentences = [s for s in sentences if 5 <= len(s) <= 12]
    if len(long_sentences) >= 3 and not short_sentences:
        burstiness_passed = False
        burstiness_details.append("长短句失衡：存在较多长句（>25字），但全文没有插入5-12字的短语破句（如“对。”、“这事离谱。”）。")
        
    # Check 3: Consecutive standard long sentences (25-40 chars)
    consecutive_long_count = 0
    max_consecutive_long = 0
    for s in sentences:
        if 25 <= len(s) <= 40:
            consecutive_long_count += 1
            max_consecutive_long = max(max_consecutive_long, consecutive_long_count)
        else:
            consecutive_long_count = 0
            
    if max_consecutive_long >= 4:
        burstiness_passed = False
        burstiness_details.append("句式死板：出现了连续4句及以上25-40字的标准长句，缺乏韵律感。")
        burstiness_violations.append(f"最长连续标准长句数: {max_consecutive_long}句")
        
    if burstiness_passed:
        burstiness_details.append("句长抖动分布自然，长短句结合合理，具有良好韵律。")
        
    results.append({
        "id": "burstiness",
        "name": "句长抖动与长短句穿插",
        "passed": burstiness_passed,
        "details": "；".join(burstiness_details),
        "violations": burstiness_violations
    })
    
    # ----------------------------------------------------
    # ② 句式多样性 (禁用词清单)
    # ----------------------------------------------------
    forbidden_words = [
        "首先", "其次", "最后", "不仅", "一方面", "值得一提的是", 
        "不可否认", "毋庸置疑", "综上所述", "总而言之", "由此可见", 
        "众所周知", "不难发现", "显而易见", "在.*背景下", "随着.*发展", 
        "站在.*角度", "让我们一起来", "归根结底", "无论如何"
    ]
    diversity_passed = True
    diversity_violations = []
    
    for word in forbidden_words:
        # Support simple regex for background patterns
        pattern = re.compile(word)
        matches = pattern.findall(body)
        if len(matches) > 1:
            diversity_passed = False
            diversity_violations.append(f"“{word}”出现了 {len(matches)} 次")
            
    results.append({
        "id": "forbidden_patterns",
        "name": "句式多样性 (禁用八股词)",
        "passed": diversity_passed,
        "details": "全文多次使用了AI常用的八股连接词（多于1次）。建议使用大白话替换。" if not diversity_passed else "没有出现AI八股连接词的重复滥用。",
        "violations": diversity_violations
    })
    
    # ----------------------------------------------------
    # ③ AI 高频词黑名单
    # ----------------------------------------------------
    ai_buzzwords = [
        "赋能", "打造", "聚焦", "深度融合", "生态", "闭环", "链路", "抓手",
        "价值链", "护城河", "方法论", "底层逻辑", "生态位", "结构化思维",
        "提升效率", "助力", "全链路", "一站式", "端到端", "量变到质变",
        "引领", "颠覆", "革命性", "前所未有", "核心竞争力", "范式",
        "降本增效", "数字化转型", "产业升级", "破局", "出圈", "沉淀",
        "深耕", "蓝图", "新篇章"
    ]
    buzzwords_passed = True
    buzzwords_violations = []
    
    for word in ai_buzzwords:
        count = body.count(word)
        if count > 2:
            buzzwords_passed = False
            buzzwords_violations.append(f"“{word}”出现了 {count} 次")
            
    results.append({
        "id": "ai_buzzwords",
        "name": "AI高频词黑名单",
        "passed": buzzwords_passed,
        "details": "检测到AI高频行话/大词，个别词频超过2次（如“赋能”、“沉淀”等）。建议降维成大白话。" if not buzzwords_passed else "无超标的AI高频行业大词。",
        "violations": buzzwords_violations
    })
    
    # ----------------------------------------------------
    # ④ 开头破冰规则
    # ----------------------------------------------------
    hook_passed = True
    hook_details = []
    if paragraphs:
        first_para = paragraphs[0]
        macro_patterns = ["近年来", "随着", "在", "伴随着", "如今"]
        starts_macro = False
        for p in macro_patterns:
            if first_para.startswith(p):
                starts_macro = True
                break
        if starts_macro:
            hook_passed = False
            hook_details.append(f"开头第一句使用宏观背景引入（“{first_para[:10]}...”），AI味过重。")
            
        # Check if first paragraph contains personal narrative or digits or quotes (human icebreakers)
        has_digit = bool(re.search(r"\d+", first_para))
        has_quote = "“" in first_para or "”" in first_para or '"' in first_para or "'" in first_para
        has_question = "？" in first_para or "?" in first_para
        has_first_person = "我" in first_para or "我们" in first_para
        
        if not (has_digit or has_quote or has_question or has_first_person):
            hook_details.append("开头缺乏具体场景、具体数字、金句对话或个人立场作为破冰钩子。")
            # If it doesn't have positive human markers, we flag it as warning/fail
            if not hook_passed or len(first_para) > 100:
                hook_passed = False
    else:
        hook_passed = False
        hook_details.append("文章正文内容为空。")
        
    if hook_passed:
        hook_details.append("开头破冰段落表达具体自然，没有宏观八股感。")
        
    results.append({
        "id": "hook_check",
        "name": "开头场景化破冰",
        "passed": hook_passed,
        "details": "；".join(hook_details),
        "violations": []
    })
    
    # ----------------------------------------------------
    # ⑤ 人称和立场 (POV)
    # ----------------------------------------------------
    pov_passed = True
    pov_details = []
    
    first_person_count = body.count("我")
    if first_person_count < 3:
        pov_passed = False
        pov_details.append(f"第一人称“我”仅出现 {first_person_count} 次（需≥3次）。缺乏个人立场和主观真实感。")
        
    uncertainty_words = ["可能", "估计", "也许", "感觉", "感觉我", "想不通", "还没完全想明白", "存疑"]
    has_uncertainty = any(word in body for word in uncertainty_words)
    if not has_uncertainty:
        pov_passed = False
        pov_details.append("表达过于全知冷静。建议增加类似“我可能说错”、“这只是我的感觉”等富有人气的不确定表达。")
        
    if pov_passed:
        pov_details.append("人称立场鲜明，有主观情感与判断力，避免了AI的全知陈述语气。")
        
    results.append({
        "id": "pov_check",
        "name": "主观立场与不确定表达",
        "passed": pov_passed,
        "details": "；".join(pov_details),
        "violations": [f"“我”出现次数: {first_person_count}"]
    })
    
    # ----------------------------------------------------
    # ⑥ 事实密度
    # ----------------------------------------------------
    density_passed = True
    density_details = []
    
    # Split text into chunks of ~500 chars
    chunk_size = 500
    chunks = [body[i:i+chunk_size] for i in range(0, len(body), chunk_size)]
    
    failed_chunks = 0
    for idx, chunk in enumerate(chunks):
        has_number = bool(re.search(r"\d+", chunk))
        # Search for English words/brands/proper nouns (e.g. GPT, OpenAI, TFBOYS) or specific version names
        has_proper_noun = bool(re.search(r"[a-zA-Z]{2,}", chunk)) or "TF" in chunk or "时代少年团" in chunk
        if not (has_number or has_proper_noun):
            failed_chunks += 1
            
    if failed_chunks > 0:
        density_passed = False
        density_details.append(f"事实密度偏低：有 {failed_chunks} 个500字文本区间缺乏具体数字、专有名词、产品名或版本号。")
    else:
        density_details.append("事实密度达标，每段均有具体数字或实体专有名词支撑。")
        
    results.append({
        "id": "fact_density",
        "name": "信息与事实密度",
        "passed": density_passed,
        "details": "；".join(density_details),
        "violations": [f"缺乏事实的500字区间数: {failed_chunks}"] if failed_chunks > 0 else []
    })
    
    # ----------------------------------------------------
    # ⑦ 标点多样性
    # ----------------------------------------------------
    punct_passed = True
    punct_details = []
    
    dash_count = body.count("——")
    q_count = body.count("？") + body.count("?")
    paren_count = body.count("（") + body.count("(")
    ellipsis_count = body.count("...") + body.count("……")
    
    if dash_count < 1:
        punct_passed = False
        punct_details.append("缺少用于插入或强调的破折号“——”（至少1个）。")
    if q_count < 2:
        punct_passed = False
        punct_details.append(f"设问或疑问标点“？”仅出现 {q_count} 次（至少2个）。")
    if paren_count < 1:
        punct_passed = False
        punct_details.append("缺少用于解释说明的括号“(...)”（至少1个）。")
    if ellipsis_count > 3:
        punct_passed = False
        punct_details.append(f"省略号出现 {ellipsis_count} 次过多（最多3次），AI高频特征。")
        
    if punct_passed:
        punct_details.append("标点符号丰富，破折号、问号、括号穿插自然，省略号未滥用。")
        
    results.append({
        "id": "punctuation_diversity",
        "name": "标点多样性与插入语",
        "passed": punct_passed,
        "details": "；".join(punct_details),
        "violations": [f"破折号: {dash_count}", f"问号: {q_count}", f"括号: {paren_count}", f"省略号: {ellipsis_count}"]
    })
    
    # ----------------------------------------------------
    # ⑧ 结构的“不完美”
    # ----------------------------------------------------
    imperfection_passed = True
    imperfection_keywords = ["扯远了", "回到主题", "收回", "扯淡", "开玩笑", "脑补", "瞎扯", "怀疑我自己"]
    has_imperfection = any(word in body for word in imperfection_keywords)
    
    if not has_imperfection:
        imperfection_passed = False
        
    results.append({
        "id": "imperfection_structure",
        "name": "结构的“不完美”标记",
        "passed": imperfection_passed,
        "details": "全文缺乏体现真人写作痕迹的“补白/反悔/自嘲”口语标记。建议增加如“扯远了，回到正题”、“扯淡”等词。" if not imperfection_passed else "包含真人特有的结构修正或自嘲标记，文章更接地气。",
        "violations": []
    })
    
    # ----------------------------------------------------
    # ⑨ 语气过滤 (Voice Filter)
    # ----------------------------------------------------
    voice_passed = True
    voice_details = []
    voice_violations = []
    
    if voice and ("main" in voice.lower() or "小严" in voice or "北京" in voice):
        # 1. Must contain Beijing oral patterns
        beijing_colloquials = ["这事儿", "说实话", "实话实说", "得嘞", "那什么", "别介"]
        has_colloquial = any(word in body for word in beijing_colloquials)
        if not has_colloquial:
            voice_passed = False
            voice_details.append("未能体现当前账号（小严）特征：缺少“这事儿”、“说实话”等自然的北京口语化语气。")
            
        # 2. Must NOT contain reader-shouting patterns
        shouting_patterns = ["我跟你讲", "我跟你说", "亲爱的读者", "大家快来看"]
        for pattern in shouting_patterns:
            if pattern in body:
                voice_passed = False
                voice_violations.append(f"命中喊话禁用语: “{pattern}”")
                
        if not voice_passed and not voice_violations:
            voice_details.append("建议增加1-2句口语化衔接词。")
    else:
        # Default fallback or no special account filter
        voice_details.append("未指定具体的创作者账号人设或不需要人设口语过滤。")
        
    if voice_passed and voice:
        voice_details.append("已适配当前账号的人设语气口吻，过滤了喊话八股腔。")
        
    results.append({
        "id": "voice_filter",
        "name": "人设语气过滤",
        "passed": voice_passed,
        "details": "；".join(voice_details),
        "violations": voice_violations
    })
    
    # Calculate Overall Score & Suggestion
    passed_count = sum(1 for r in results if r["passed"])
    score = int((passed_count / len(results)) * 100)
    
    if score >= 90:
        suggestion = "优秀！极具真人质感，几乎没有AI味，建议直接发布。"
    elif score >= 70:
        suggestion = "良好。建议针对不达标的项（如微调高频词、调整首段）进行手动润色。"
    else:
        suggestion = "警告！AI味严重，建议触发重新洗稿或按照指示进行深度重组。"
        
    return {
        "score": score,
        "passed_rules_count": passed_count,
        "results": results,
        "suggestion": suggestion
    }
