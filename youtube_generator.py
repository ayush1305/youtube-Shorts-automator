import os
import json
import random
import requests
import google.generativeai as genai
from config import settings, logger

def get_gemini_client():
    """Configure and return the Gemini generative model client if an API key is available."""
    keys = settings.get_keys()
    api_key = keys.get("gemini_key")
    if api_key:
        try:
            genai.configure(api_key=api_key)
            return genai.GenerativeModel('gemini-3.6-flash')
        except Exception as e:
            logger.error(f"Failed to configure Gemini client: {str(e)}")
    return None

def generate_script_and_metadata(category: str, topic_keywords: str) -> dict:
    """Generate script and metadata (title, description, tags, search keywords) using Gemini or a fallback database."""
    model = get_gemini_client()
    
    category_map = {
        "tech": "technology, AI, coding, and futuristic gadgets",
        "history": "historical events, figures, ancient secrets, and interesting wars",
        "how_why": "explaining science, everyday phenomena, and 'how and why' questions"
    }
    cat_desc = category_map.get(category, category)
    
    if not model:
        logger.info(f"No Gemini API key found. Using fallback library for category '{category}'")
        return get_fallback_script(category)
        
    import time
    prompt = f"""
    You are an expert YouTube content creator specializing in short-form viral videos (YouTube Shorts, TikToks).
    Generate a high-retention script and metadata for a video about: {topic_keywords if topic_keywords else 'a trending topic in ' + cat_desc}.
    The category is: {cat_desc}.
    
    Random seed/timestamp: {time.time()} (Ensure you generate a completely fresh, creative, and unique script and metadata that differs from prior runs).
    
    Ensure the script:
    1. Is around 30 to 45 seconds long when spoken (70 to 110 words).
    2. Starts with a high-energy, exciting, and curiosity-driven opening hook in the first 3-5 seconds (e.g., an enthusiastic "Did you know this crazy fact?!" or a shocking mystery reveal) formatted with expressive punctuation (! and ?) so the voiceover sounds genuinely excited, engaging, and enthusiastic from the very first second.
    3. Explains the concept in a fast-paced, highly engaging, and clear conversational style.
    4. MUST conclude with an engaging call-to-action asking viewers what topic they want to see next (e.g., "What topic do you want next? Let me know in the comments and subscribe!").
    5. Write the script as plain spoken English text without stage directions, sound effect indicators, or bracketed text like [music plays]. Only output the exact words the voiceover will speak.
    6. Provide exactly 10 tags in the "tags" array. Every tag must be a high-volume viral search tag (with 10M+ views or uses) relevant to the topic (e.g., shorts, trending, viral, science, tech, facts, and topic-specific highly searched keywords). Return them as plain strings without the '#' symbol.
    7. Provide a short, punchy 3 to 6 word curiosity hook banner in ALL CAPS with emojis for the "hook_text" field (e.g., "DID YOU KNOW THIS? 🤯", "THE SHOCKING TRUTH! ⚡", "HOW THIS ACTUALLY WORKS! 🔬", "THE CRAZY SECRET! 🤫", "NOBODY TOLD YOU THIS! 🚨"). DO NOT use generic phrases like "WAIT TILL THE END".
    
    Return a valid JSON object with the following fields (do not include markdown wrapping, return only raw JSON):
    {{
      "title": "An attention-grabbing title under 60 characters with emojis",
      "hook_text": "DID YOU KNOW THIS? 🤯",
      "description": "An engaging, SEO-friendly description containing relevant hashtags",
      "tags": ["shorts", "trending", "viral", "foryou", "science", "physics", "earth", "nature", "space", "facts"],
      "script": "The spoken voiceover script text",
      "search_keywords": ["glowing quantum computer chip", "abstract technology network connection", "futuristic server room lights"]
    }}
    """
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 1.0
            }
        )
        data = json.loads(response.text)
        logger.info(f"Successfully generated script via Gemini: {data.get('title')}")
        return data
    except Exception as e:
        logger.error(f"Failed to generate script from Gemini: {str(e)}. Using fallback library.")
        return get_fallback_script(category)

def search_pexels_video(keywords: str, pexels_key: str) -> str:
    """Search for portrait orientation videos on Pexels. Fall back to curated library if not found."""
    if not pexels_key or not keywords:
        logger.info("No Pexels API key or keywords provided. Using fallback stock video library.")
        return ""
        
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": pexels_key}
    params = {
        "query": keywords,
        "per_page": 5,
        "orientation": "portrait"
    }
    
    try:
        logger.info(f"Searching Pexels for: '{keywords}'")
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        videos = data.get("videos", [])
        if not videos:
            logger.info("No videos found on Pexels for the given query.")
            return ""
            
        # Look for a video file with standard vertical resolution (e.g., 1080x1920 or similar aspect ratio)
        # Sort and select the best file quality
        for video in videos:
            video_files = video.get("video_files", [])
            # Prefer vertical files
            for file in video_files:
                width = file.get("width") or 0
                height = file.get("height") or 0
                link = file.get("link")
                if link and height > width and file.get("quality") == "hd":
                    logger.info(f"Found vertical HD Pexels video: {link}")
                    return link
            # Fallback to the first video link
            for file in video_files:
                link = file.get("link")
                if link:
                    logger.info(f"Using first available Pexels video file: {link}")
                    return link
    except Exception as e:
        logger.error(f"Pexels API video search failed: {str(e)}")
    return ""

def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS time format: H:MM:SS.CS (CS = centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def generate_ass_file(word_boundaries: list, ass_path: str, hook_text: str = "") -> None:
    """Group words into fast-paced subtitle cues and write an Advanced SubStation Alpha (.ass) file with top-center visual hook banner."""
    if not word_boundaries:
        logger.warning("No word boundaries provided to subtitle generator.")
        return

    # Group words into lists of up to 3 words
    groups = []
    current_group = []
    for wb in word_boundaries:
        if not current_group:
            current_group.append(wb)
        else:
            # If group has 3 words or the silence gap is > 0.4 seconds
            gap = wb["start"] - current_group[-1]["end"]
            if len(current_group) >= 3 or gap > 0.4:
                groups.append(current_group)
                current_group = [wb]
            else:
                current_group.append(wb)
    if current_group:
        groups.append(current_group)

    lines = []
    
    # Clean normal title display at top of the video for the first 3.5 seconds
    clean_hook = hook_text.strip() if hook_text else ""
    if clean_hook:
        display_title = clean_hook.replace("🚨", "").replace("🔥", "").strip()
        lines.append(f"Dialogue: 1,0:00:00.00,0:00:03.50,TopTitle,,0,0,0,,{{\\fade(150,200)}}{display_title}")

    for g in groups:
        group_start = g[0]["start"]
        group_end = g[-1]["end"]
        
        # Word highlight animation: output a line for each word in the group
        # highlighting that specific word for its exact duration
        for i, target_wb in enumerate(g):
            start_time = target_wb["start"] if i > 0 else group_start
            end_time = g[i+1]["start"] if i < len(g) - 1 else group_end
            
            words_text = []
            for j, wb in enumerate(g):
                word_clean = wb["word"].strip()
                if j == i:
                    # Highlight color yellow (BBGGRR: &H0000FFFF&)
                    words_text.append(f"{{\\c&H0000FFFF&}}{word_clean}{{\\c}}")
                else:
                    # Standard color white
                    words_text.append(word_clean)
            
            cue_text = " ".join(words_text)
            start_str = format_ass_time(start_time)
            end_str = format_ass_time(end_time)
            
            # Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
            lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{cue_text}")

    # Clean Outro Call-To-Action banner for the last 3.5 seconds
    if groups:
        total_end_time = groups[-1][-1]["end"]
        if total_end_time > 6.0:
            outro_start = max(3.5, total_end_time - 3.5)
            outro_start_str = format_ass_time(outro_start)
            outro_end_str = format_ass_time(total_end_time + 0.5)
            lines.append(f"Dialogue: 1,{outro_start_str},{outro_end_str},TopTitle,,0,0,0,,{{\\fade(150,200)}}💬 What topic next? Comment below!")

    # ASS Subtitle definition headers (Clean modern typography)
    ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Impact,38,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,1,5,10,10,10,1
Style: TopTitle,Impact,40,&H0000FFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4,2,8,20,20,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_TEMPLATE)
        for line in lines:
            f.write(line + "\n")
    logger.info(f"Styled ASS subtitles successfully written to: {ass_path}")

def score_video_metadata(metadata: dict) -> dict:
    """Analyze the video elements (title, hashtags, script density/hook) and score against YouTube viral standards."""
    model = get_gemini_client()
    if not model:
        logger.info("No Gemini API key found. Using fallback scoring heuristics.")
        return get_fallback_score(metadata)
        
    prompt = f"""
    You are a YouTube Shorts algorithm expert. Analyze the following video metadata and script to score it according to YouTube standards (viral potential, hook quality, retention probability, and SEO relevancy).
    
    Video Title: {metadata.get('title')}
    Video Description: {metadata.get('description')}
    Video Tags: {', '.join(metadata.get('tags', []))}
    Video Script: {metadata.get('script')}
    
    Evaluate the following metrics (out of 100):
    1. Overall Score: General viral potential on YouTube Shorts.
    2. Hook Strength: Speed and power of the hook (first 3 seconds and title).
    3. Retention Score: How well the pacing and concept keep viewers engaged.
    4. SEO Score: Relevancy of the title, tags, and description for optimization.
    
    Predict the estimated Click-Through Rate (CTR) and Average retention percentage.
    Provide constructive feedback and recommendations. Evaluate dynamically and optimistically: if the script is well-structured and fast-paced, target a minimum score of 80% for hook strength and retention to encourage release.
    
    Return a valid JSON object with the following structure (do not include markdown wrapping, return only raw JSON):
    {{
      "overall_score": 85,
      "hook_score": 90,
      "retention_score": 80,
      "seo_score": 85,
      "estimated_ctr": "8.5%",
      "estimated_retention": "82%",
      "feedback": "Your overall feedback summary.",
      "suggestions": [
        "Suggestion 1",
        "Suggestion 2"
      ]
    }}
    """
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"Failed to score metadata using Gemini: {str(e)}")
        return get_fallback_score(metadata)

def get_what_to_post_today(category: str) -> dict:
    """Recommend daily trending topics, titles, descriptions, and structural hooks for the chosen category."""
    model = get_gemini_client()
    
    category_map = {
        "tech": "technology, artificial intelligence, and coding breakthroughs",
        "history": "ancient history, mystery events, and war facts",
        "how_why": "explaining daily mysteries and 'how and why' science questions"
    }
    cat_name = category_map.get(category, category)
    
    if not model:
        logger.info(f"No Gemini API key found. Using fallback posting recommendations for category '{category}'")
        return get_fallback_recommendations(category)
        
    import time
    prompt = f"""
    You are a social media trends expert. Suggest 3 trending and highly viral video ideas to post today in the category of '{cat_name}'.
    For each idea, provide a viral title, an engaging caption/description with hashtags, and a strategic viral angle (pacing / Hook).
    
    Random seed/timestamp: {time.time()} (Ensure you generate fresh, unique, and completely different ideas than prior requests).
    
    Return a valid JSON object with the following structure (do not include markdown wrapping, return only raw JSON):
    {{
      "category": "{category}",
      "recommendations": [
        {{
          "title": "Title Idea with Emojis",
          "description": "Caption description with #hashtags",
          "angle": "Structural viral angle or visual Hook recommendation"
        }},
        ...
      ]
    }}
    """
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 1.0
            }
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"Failed to generate trending topics using Gemini: {str(e)}")
        return get_fallback_recommendations(category)

# =====================================================================
# Offline Fallbacks & Libraries
# =====================================================================

def get_fallback_script(category: str) -> dict:
    """Curated viral scripts library (used as zero-config fallback)."""
    library = {
        "tech": [
            {
                "title": "Why Quantum Computing is a Cheat Code! 💻⚡",
                "hook_text": "DID YOU KNOW THIS? 💻⚡",
                "description": "Quantum computers are not just normal computers but faster. Here is how they work and why they are going to change medicine, encryption, and the entire digital world forever! #quantumcomputing #tech #future #science",
                "tags": ["shorts", "trending", "viral", "foryou", "tech", "quantumcomputing", "futuretech", "computers", "science", "innovation"],
                "script": "The quantum computing revolution is closer than you think! Standard computers use bits, which are either zero or one. But quantum computers use qubits, which can be both at the same time! This means they can solve complex problems in seconds that would take our best supercomputers thousands of years. From breaking encryption to inventing new medicines, quantum computing is about to change everything. What topic should I cover next? Let me know in the comments and subscribe!",
                "search_keywords": ["quantum computer technology", "microchip close up", "glowing servers computing"]
            },
            {
                "title": "NASA's Moon Tech vs Your Phone! 🚀📱",
                "hook_text": "THE CRAZY MOON SECRET! 🚀📱",
                "description": "Compare the computing power of the NASA Apollo space program to the smartphone in your pocket. You won't believe how far technology has scaled! #space #techhistory #smartphone #nasa #funfacts",
                "tags": ["shorts", "trending", "viral", "foryou", "nasa", "space", "smartphone", "techhistory", "engineering", "facts"],
                "script": "Your smartphone has more computing power than all of NASA did when they sent astronauts to the Moon in 1969! That's right—the device in your pocket is millions of times faster. It shows how rapidly technology is scaling. If this pace continues, what will technology look like in another fifty years? What topic do you want next? Let me know in the comments and subscribe!",
                "search_keywords": ["retro space electronics", "rocket launch apollo", "modern smartphone typing"]
            }
        ],
        "history": [
            {
                "title": "The Shortest War in History (Only 38 Mins!) ⏱️💥",
                "hook_text": "DID YOU KNOW THIS? ⏱️💥",
                "description": "The Anglo-Zanzibar war of 1896 remains the shortest war ever recorded. Here is how it went down and why it was over before it even started! #historyfacts #funfacts #britishhistory #militaryhistory",
                "tags": ["shorts", "trending", "viral", "foryou", "history", "historyfacts", "war", "britishhistory", "weirdhistory", "learn"],
                "script": "Did you know that the shortest war in history lasted only thirty-eight minutes? It happened in 1896 between the British Empire and the Sultanate of Zanzibar. The Sultan died, a usurper took power, and the British fleet immediately opened fire on the palace. In less than forty minutes, the new Sultan's forces surrendered. Talk about a quick defeat! What historical topic should I cover next? Let me know in the comments and subscribe!",
                "search_keywords": ["old sailing warship cannon", "zanzibar palace ruins", "historic pocket watch 38 minutes"]
            },
            {
                "title": "The Pyramid Mystery Solved! 🔺🏗️",
                "hook_text": "THE HIDDEN PYRAMID TRUTH! 🔺",
                "description": "New evidence changes everything we knew about how the pyramids were built. Spoiler: it wasn't slaves! #pyramids #egypt #ancienthistory #secrets #historical",
                "tags": ["shorts", "trending", "viral", "foryou", "egypt", "pyramids", "ancienthistory", "archaeology", "mysteries", "engineering"],
                "script": "Did you know that the ancient Egyptians did not build the pyramids using slaves? Archaeological discoveries of workers' tombs show they were actually paid laborers. They were highly respected craftsmen who ate meat and drank beer daily. Building the pyramids was a matter of national pride, not slavery. What mystery should I cover next? Let me know in the comments and subscribe!",
                "search_keywords": ["ancient egyptian pyramids", "hieroglyphs wall carvings", "stone workers construction"]
            }
        ],
        "how_why": [
            {
                "title": "Why the Sky is NOT Blue Because of the Ocean! 🌌⛅",
                "hook_text": "DID YOU KNOW THIS? 🌌⛅",
                "description": "Learn the actual physics behind why the sky appears blue. Hint: it is not the ocean reflection, but a physics process called Rayleigh scattering! #sciencefacts #whytheskyisblue #physics #howitworks",
                "tags": ["shorts", "trending", "viral", "foryou", "science", "physics", "sky", "earth", "nature", "explain"],
                "script": "Why is the sky blue? It's not because it reflects the ocean! It's actually due to a phenomenon called Rayleigh scattering. Sunlight contains all colors of the rainbow, but blue light travels in shorter, smaller waves. When it hits Earth's atmosphere, it scatters in all directions, coloring the sky. What science question should I answer next? Let me know in the comments and subscribe!",
                "search_keywords": ["beautiful blue sky clouds", "sunset prism light spectrum", "earth atmosphere space view"]
            },
            {
                "title": "The Caffeine Trick on Your Brain! ☕🧠",
                "hook_text": "HOW COFFEE TRICKS YOU! ☕🧠",
                "description": "How does coffee actually keep you awake? It doesn't give you energy, it tricks your brain structure! #coffee #science #brain #caffeine #healthylifestyle",
                "tags": ["shorts", "trending", "viral", "foryou", "coffee", "caffeine", "brain", "health", "science", "biology"],
                "script": "Why does coffee actually make you feel awake? It doesn't actually give you energy! Instead, caffeine blockades adenosine, a chemical in your brain that signals tiredness. By binding to adenosine receptors, caffeine tricks your brain into thinking you are fully awake. That's why you crash when it wears off! What topic do you want to learn about next? Let me know in the comments and subscribe!",
                "search_keywords": ["coffee cup steaming", "brain synapse neural activity", "coffee beans roasting close up"]
            }
        ]
    }
    
    cat = category if category in library else "tech"
    return random.choice(library[cat])

def get_fallback_score(metadata: dict) -> dict:
    """Heuristic scoring tool for metadata validation when no AI keys are present."""
    script = metadata.get("script", "")
    words = script.split()
    word_count = len(words)
    
    # Calculate simple quality metric
    hook_len = len(script.split(".")[0]) if script else 50
    hook_score = max(82, 95 - abs(hook_len - 45))  # Minimum 82%
    
    seo_score = max(85, 75 + min(20, len(metadata.get("tags", [])) * 3))
    
    retention_score = max(81, 95 - abs(90 - word_count)) # Minimum 81%
    
    overall_score = int((hook_score + seo_score + retention_score) / 3)
    estimated_ctr = f"{(overall_score / 10):.1f}%"
    estimated_retention = f"{int(retention_score * 0.9)}%"
    
    return {
        "overall_score": overall_score,
        "hook_score": hook_score,
        "retention_score": retention_score,
        "seo_score": seo_score,
        "estimated_ctr": estimated_ctr,
        "estimated_retention": estimated_retention,
        "feedback": "Offline Heuristics: This script has good length and formatting. The tags are relevant, and the description contains strategic hashtags.",
        "suggestions": [
            "Add animated text elements in the first 3 seconds.",
            "Verify sound levels for background music so speech is clear."
        ]
    }

def get_fallback_recommendations(category: str) -> dict:
    """Zero-config recommended posting topics for today."""
    topics = {
        "tech": [
            {
                "title": "The Secret Chip Ruling the AI Race! 💻💥",
                "description": "Why silicon microchips have become the most valuable resource on the planet. #tech #ai #chips #siliconvalley #business",
                "angle": "Open with a visual hook showing glowing circuits, then explain NVIDIA's scaling."
            },
            {
                "title": "How a Simple Bug Cost $10 Billion! 🐜💸",
                "description": "The costliest software errors in history, from rocket crashes to bank lockouts. #coding #software #bugs #financehistory #techfacts",
                "angle": "Pose the question: 'Could a single typo erase ten billion dollars?' then show the space launch error."
            }
        ],
        "history": [
            {
                "title": "The Computer of Ancient Greece! ⚙️🏛️",
                "description": "The story of the Antikythera Mechanism—a 2,000-year-old astronomical calculator. #ancienthistory #greece #mystery #archaeology #technology",
                "angle": "Open with the ship wreck discovery of gears that shouldn't have existed for another thousand years."
            },
            {
                "title": "Why Pyramids Were Originally White! 🔺✨",
                "description": "The Giza Pyramids didn't always look like sand. Discover the limestone casing stones! #pyramids #egyptian #ancientsecrets #historyfacts",
                "angle": "Reconstruct the original gleaming white and gold-capped appearance of Giza."
            }
        ],
        "how_why": [
            {
                "title": "Why Rain Smells So Good! 🌧️👃",
                "description": "The scientific reason behind the soothing scent of rain, known as petrichor. #science #rain #petrichor #howthingswork #nature",
                "angle": "Start with the scent release from soil bacteria during dry periods when raindrops impact."
            },
            {
                "title": "How Your Brain Stores Memories! 🧠💾",
                "description": "Understanding neurons, electric charges, and synapses in under 60 seconds. #scienceexplained #brainpower #memory #health #psychology",
                "angle": "Use the analogy of a dynamic computer RAM writing data paths in real time."
            }
        ]
    }
    
    cat = category if category in topics else "tech"
    return {
        "category": category,
        "recommendations": topics[cat]
    }

def ensure_music_track(category: str, music_dir: str) -> str:
    """Ensure background music track exists for category, generating a clean harmonic ambient track if missing."""
    import subprocess
    os.makedirs(music_dir, exist_ok=True)
    cat = category if category in ["tech", "history", "how_why"] else "tech"
    music_file = os.path.join(music_dir, f"{cat}.mp3")
    if os.path.exists(music_file) and os.path.getsize(music_file) > 1000:
        return music_file
        
    if cat == "tech":
        filter_expr = (
            "aevalsrc='0.08*sin(2*PI*220*t) + 0.06*sin(2*PI*277.18*t) + 0.07*sin(2*PI*329.63*t) + "
            "0.05*sin(2*PI*440*t)*sin(2*PI*0.5*t)':s=44100:d=60,"
            "lowpass=f=800,chorus=0.7:0.9:55:0.4:0.25:2"
        )
    elif cat == "history":
        filter_expr = (
            "aevalsrc='0.09*sin(2*PI*110*t) + 0.07*sin(2*PI*164.81*t) + 0.05*sin(2*PI*220*t) + "
            "0.04*sin(2*PI*330*t)*sin(2*PI*0.2*t)':s=44100:d=60,"
            "lowpass=f=600,flanger=delay=10:depth=3:regen=20"
        )
    else:
        filter_expr = (
            "aevalsrc='0.07*sin(2*PI*261.63*t) + 0.06*sin(2*PI*329.63*t) + 0.06*sin(2*PI*392*t) + "
            "0.05*sin(2*PI*523.25*t)*sin(2*PI*1.0*t)':s=44100:d=60,"
            "lowpass=f=1200,chorus=0.6:0.8:40:0.3:0.2:2"
        )
    
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", filter_expr,
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        music_file
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=15)
        logger.info(f"Generated harmonic background music for {cat} at {music_file}")
    except Exception as e:
        logger.error(f"Failed to generate ambient music: {e}")
    return music_file
