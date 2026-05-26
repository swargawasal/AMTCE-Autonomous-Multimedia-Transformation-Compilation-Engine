"""
Influencer Prompt Engineer
============================
Replaces the single generic prompt string in influencer_pipeline.run()
with a dynamically-generated, niche-aware, per-stage prompt system.

Adapted from the Seedance 2.0 Fashion Lookbook + Social Hook skills
(D:/MCP/temp_higgs/skills/) — the full framework is distilled here and
converted from Seedance-specific instructions into SDXL / ControlNet /
Wan2.1 compatible prompt language.

The original Seedance skills encode:
  - 2-second hook frameworks (12+ hook types)
  - Fashion category playbooks (11 categories)
  - Model direction vocabulary (walks, poses, expressions, attitude)
  - Fabric + material showcase prompts
  - Lighting library (9 modes)
  - Location + setting library (12 environments)
  - Music/vibe pairings (motion energy descriptor)

This module converts those into 4 stage-specific prompts for:
  Stage 1 — SDXL + IP-Adapter: Portrait generation
  Stage 3 — ControlNet + IP-Adapter: Pose + style sync
  Stage 4 — Wan2.1 I2V: Motion + animation

Usage
-----
    from Influencer_Modules.prompt_engineer import build_prompts

    prompts = build_prompts(
        niche          = "fashion",        # determines category playbook
        outfit_desc    = "silk saree emerald green",
        setting        = "rooftop",        # selects environment block
        lighting       = "golden_hour",    # selects lighting block
        hook_type      = "fabric_macro",   # selects 2-second hook pattern
        walk_type      = "slow_sway",      # model direction
        attitude       = "grace",
        expression     = "subtle_smile",
        fabric         = "silk",           # selects fabric showcase block
    )

    # Each key is ready to pass directly into the pipeline stage:
    prompts["stage1_portrait"]   → generate_portrait(prompt=...)
    prompts["stage3_sync"]       → sync_pose(prompt=...)
    prompts["stage4_motion"]     → animate(prompt=...)
    prompts["stage4_motion_neg"] → animate(negative_prompt=...)
"""

import logging
import os

logger = logging.getLogger("influencer.prompt_engineer")

# ─────────────────────────────────────────────────────────────────────────────
# HOOK TYPE LIBRARY  (adapted from Seedance Social Hook SKILL.md)
# Mapped to SDXL motion/composition descriptors
# ─────────────────────────────────────────────────────────────────────────────

HOOK_TYPES = {
    # Visual shock hooks
    "fabric_macro": (
        "extreme macro close-up of fabric texture in first frame, "
        "slowly pulling back to reveal full outfit, "
        "ultra-detailed material weave visible, high cinematic quality"
    ),
    "power_walk": (
        "model walking directly toward camera with confident power-stride, "
        "shoulders back, purposeful movement, dominates frame, "
        "slight camera tilt tracking motion"
    ),
    "outfit_reveal": (
        "dramatic outfit reveal from behind sheer curtain or door, "
        "full silhouette emerging, camera holds on reveal moment, "
        "high-contrast backlighting defining silhouette"
    ),
    "slow_mo_wind": (
        "slow-motion hair and fabric caught in gentle wind, "
        "dramatic fabric billow, sensual graceful movement, "
        "fashion-film energy, golden ratio composition"
    ),
    "mirror_reveal": (
        "elegant mirror reflection reveal, model's reflection visible "
        "before full body seen, sophisticated editorial composition, "
        "turn or tilt reveals outfit, geometric symmetry"
    ),
    "color_match_env": (
        "model outfit matches or complements background in monochromatic harmony, "
        "color-coordinated environment frames the look, "
        "minimalist composition with intentional palette"
    ),
    "accessory_zoom": (
        "opening tight zoom on signature accessory or shoes, "
        "then pulls back to reveal full outfit, "
        "macro detail shot transitioning to full body frame"
    ),
    "spin_reveal": (
        "model rotating slowly, 270-degree reveal of outfit from all angles, "
        "camera captures full spin, showing train or back detail, "
        "dynamic rotational movement"
    ),
    "rapid_cuts": (
        "energetic rapid cuts showing multiple outfit angles, "
        "0.3-second each cut, dizzying variety showcasing full look, "
        "kinetic fast-paced editorial energy"
    ),
    "silhouette_shadow": (
        "dramatic silhouette against backlit background, "
        "shadow or outline emphasized, mysterious and contemporary, "
        "high-contrast backlight defining body shape"
    ),
    # Default fallback
    "default": (
        "confident model posing in stylish outfit, "
        "professional fashion photography composition, "
        "scroll-stopping first frame with clear visual impact"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# FASHION CATEGORY PLAYBOOK  (from Fashion Lookbook SKILL.md)
# ─────────────────────────────────────────────────────────────────────────────

FASHION_CATEGORIES = {
    "ethnic_traditional": {
        "style_keywords": (
            "traditional indian ethnic wear, intricate embroidery, "
            "rich jewel tones, heritage craftsmanship, cultural elegance"
        ),
        "model_direction": "poise, grace, understated confidence, slow deliberate movements",
        "lighting":        "golden hour or soft studio light, warm and rich tones",
        "atmosphere":      "aspirational, cultural luxury, timeless sophistication",
    },
    "saree": {
        "style_keywords": (
            "silk saree, pleated drape, pallu flowing over shoulder, "
            "blouse with detailed work, traditional nine-yard elegance"
        ),
        "model_direction": "graceful walk with pallu held, traditional stance, poised confidence",
        "lighting":        "golden hour warm light, soft diffused natural light",
        "atmosphere":      "timeless indian fashion, cultural pride, feminine grace",
    },
    "lehenga": {
        "style_keywords": (
            "lehenga choli, flared skirt with embroidery, dupatta draped elegantly, "
            "bridal or festive aesthetic, rich embellishments"
        ),
        "model_direction": "slow spin to show flare, graceful dupatta hold, festive confidence",
        "lighting":        "warm golden light, studio flash on embellishments",
        "atmosphere":      "celebration, festivity, bridal grandeur",
    },
    "luxury_fashion": {
        "style_keywords": (
            "haute couture, designer wear, dramatic silhouette, "
            "flowing fabric, jewel tones, editorial lighting, intricate detailing"
        ),
        "model_direction": "grace, poise, understated confidence, slow deliberate movements",
        "lighting":        "golden hour or studio flash, dramatic shadows highlight drape",
        "atmosphere":      "exclusive, rare, aspirational, editorial luxury",
    },
    "streetwear": {
        "style_keywords": (
            "bold colors, graphic prints, oversized fit, sneaker focus, "
            "attitude-driven, street energy, quick cuts, confident swagger"
        ),
        "model_direction": "attitude, swagger, dynamic walk, strong gaze, personality-forward",
        "lighting":        "natural daylight, urban neon, high-contrast shadows",
        "atmosphere":      "contemporary, rebellious, youth energy, street credibility",
    },
    "casual_everyday": {
        "style_keywords": (
            "comfortable fit, effortless style, accessible, relatable, "
            "casual grace, genuine movement, wearability, real-life styling"
        ),
        "model_direction": "natural, relaxed, approachable, genuine personality",
        "lighting":        "natural daylight, warm and inviting",
        "atmosphere":      "relatable, authentic, everyday aspirational",
    },
    "formal_business": {
        "style_keywords": (
            "structured silhouette, tailored fit, professional polish, "
            "minimalist elegance, office-ready, neutral tones or power colors"
        ),
        "model_direction": "poised confidence, powerful walk, professional attitude, strong eye contact",
        "lighting":        "studio lighting or bright office environments",
        "atmosphere":      "authority, professional ambition, sophisticated",
    },
    "evening_wear": {
        "style_keywords": (
            "glamorous silhouette, dramatic color, statement piece, "
            "sequins or embellishment, evening elegance, party-ready"
        ),
        "model_direction": "glamorous, confident, celebratory, dramatic pose, runway attitude",
        "lighting":        "dramatic studio light, moody ambient, or neon party energy",
        "atmosphere":      "celebration, glamour, aspirational nightlife",
    },
    "fashion": {  # generic fallback
        "style_keywords": (
            "stylish contemporary outfit, on-trend fashion, "
            "confident aesthetic, editorial quality"
        ),
        "model_direction": "confident, engaged, fashion-forward attitude",
        "lighting":        "flattering professional lighting",
        "atmosphere":      "modern, aspirational, scroll-stopping",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# LIGHTING LIBRARY  (from Fashion Lookbook SKILL.md)
# ─────────────────────────────────────────────────────────────────────────────

LIGHTING_PRESETS = {
    "golden_hour": (
        "golden hour lighting, warm flattering light, soft shadows creating dimension, "
        "warm orange and amber tones, universally beautiful, romantic quality"
    ),
    "studio_editorial": (
        "crisp studio flash, directional key light, clean editorial shadows, "
        "professional high-fashion aesthetic, bright background separation"
    ),
    "soft_diffused": (
        "soft diffused window light, gentle directional illumination, "
        "no harsh shadows, intimate flattering quality, flattering on all skin tones"
    ),
    "natural_daylight": (
        "bright natural daylight, clear energetic light, "
        "high-contrast shadows define silhouette, colors pop with clarity"
    ),
    "neon_night": (
        "neon signs casting colored light, urban nighttime setting, "
        "high-contrast artificial light, edgy contemporary mood, color-shifted shadows"
    ),
    "dramatic_backlight": (
        "dramatic backlighting creating silhouette, moody artistic quality, "
        "silhouette of garment emphasized, high-fashion aesthetic"
    ),
    "warm_indoor": (
        "warm indoor practical lighting, cozy intimate atmosphere, "
        "flattering soft shadows, lifestyle aesthetic quality"
    ),
    "default": (
        "professional flattering fashion photography lighting, "
        "even illumination, clear outfit visibility"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# LOCATION / SETTING LIBRARY  (from Fashion Lookbook SKILL.md)
# ─────────────────────────────────────────────────────────────────────────────

LOCATION_PRESETS = {
    "studio_white": (
        "pure white studio backdrop, clean professional environment, "
        "no environmental distractions, high-fashion minimalist aesthetic"
    ),
    "urban_street": (
        "urban street setting with brick walls, concrete sidewalk, "
        "authentic city environment, streetwear context, gritty energy"
    ),
    "nature_garden": (
        "lush garden setting, green foliage background, natural flowers framing, "
        "romantic organic aesthetic, peaceful natural environment"
    ),
    "rooftop": (
        "rooftop with city skyline backdrop, golden hour light, "
        "sophisticated urban aspirational aesthetic, glass and steel cityscape"
    ),
    "luxury_interior": (
        "luxury hotel lobby, marble floors, crystal lighting, elegant furnishings, "
        "glamorous aspirational interior environment"
    ),
    "beach": (
        "sandy beach with ocean waves, golden sand and blue water, "
        "tropical summer aesthetic, golden hour coastal light"
    ),
    "market_bazaar": (
        "colorful traditional market with vibrant textiles and goods, "
        "cultural eclectic aesthetic, authentic diverse environment"
    ),
    "industrial": (
        "industrial warehouse, concrete walls, metal beams, "
        "moody natural light through windows, edgy contemporary vibe"
    ),
    "home_interior": (
        "cozy home interior setting, warm tones and furniture, "
        "intimate relatable domestic aesthetic, approachable lifestyle"
    ),
    "default": (
        "elegant contemporary setting appropriate for fashion content, "
        "clean background that complements the outfit"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# FABRIC SHOWCASE LIBRARY  (from Fashion Lookbook SKILL.md)
# ─────────────────────────────────────────────────────────────────────────────

FABRIC_DESCRIPTORS = {
    "silk": (
        "silk fabric catching light and shimmering with each movement, "
        "gentle ripples and folds creating subtle light reflections, "
        "lustrous smooth surface, drapes gracefully"
    ),
    "silk_saree": (
        "Banarasi or Kanjeevaram silk with rich zari border, "
        "fabric catches light creating luminous sheen, "
        "pallu drapes with natural weight, intricate woven patterns visible"
    ),
    "chiffon": (
        "chiffon flowing and floating around the body, "
        "translucent quality allowing light through, "
        "layers draping softly without stiffness, ethereal fabric movement"
    ),
    "denim": (
        "denim showing natural wrinkles and creases as model moves, "
        "sturdy reliable texture, matte structured surface, "
        "natural fade and worn details visible"
    ),
    "leather": (
        "leather moving with the body, creasing naturally at joints, "
        "glossy sheen catching light as model walks, "
        "defined shadows on structured surface"
    ),
    "sequin": (
        "sequin dress throwing light and sparkling with every movement, "
        "cascading light effects on rotation, "
        "high-impact sparkle and shine, light-catching embellishments"
    ),
    "velvet": (
        "velvet showing color depth and subtle sheen as model moves, "
        "nap direction creating shifting highlights, "
        "ultra-luxurious deep surface with light play"
    ),
    "lace": (
        "lace overlay showing intricate pattern details, "
        "delicate translucent sections creating visual interest, "
        "romantic feminine aesthetic, pattern casting subtle shadows"
    ),
    "cotton_casual": (
        "soft cotton fabric moving naturally with body, "
        "comfortable relaxed drape, matte finish, "
        "breathable everyday textile quality"
    ),
    "embroidered": (
        "rich embroidery catching light, intricate thread work visible, "
        "textured surface with dimensional stitching, "
        "traditional craftsmanship in each detail"
    ),
    "default": (
        "fabric moving naturally with body, "
        "texture clearly visible, high-quality material appearance"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# MODEL DIRECTION VOCABULARY  (from Fashion Lookbook SKILL.md)
# ─────────────────────────────────────────────────────────────────────────────

WALK_TYPES = {
    "power_walk":    "confident direct stride toward camera, shoulders back, purposeful",
    "slow_sway":     "gentle side-to-side movement, fluid relaxed sensual sway",
    "catwalk":       "deliberate heel-to-toe runway placement, poised dramatic stride",
    "casual_amble":  "relaxed natural gait, approachable everyday movement",
    "graceful_walk": "elegant measured steps, fluid movement with poise and restraint",
    "spin":          "full 270-360 degree rotation, showcasing outfit from all angles",
    "default":       "natural confident movement",
}

ATTITUDE_TYPES = {
    "confidence":     "assured powerful commanding presence",
    "grace":          "elegant refined poised understated sophistication",
    "playfulness":    "joyful light fun approachable energy",
    "attitude":       "bold rebellious unapologetic edge",
    "approachable":   "relatable friendly warm accessible",
    "sensuality":     "graceful fluid alluring subtly captivating",
    "default":        "confident and engaging",
}

EXPRESSION_TYPES = {
    "strong_gaze":    "direct eye contact with camera, intense confident expression",
    "subtle_smile":   "gentle warm approachable smile, friendly and relatable",
    "blank_slate":    "high-fashion editorial expression, lets outfit speak",
    "smoldering":     "sensual confident editorial look, attitude-forward",
    "genuine_laugh":  "authentic joy, playful relatable expression",
    "default":        "natural confident engagement with camera",
}

# ─────────────────────────────────────────────────────────────────────────────
# MOTION ENERGY TIERS (for Wan2.1 animation prompt)
# Maps to optical-flow tiers in motion_animator.py
# ─────────────────────────────────────────────────────────────────────────────

MOTION_ENERGY_PROMPTS = {
    "static":   (
        "subject standing still, gentle breeze causing subtle fabric movement, "
        "minimal body sway, meditative stillness, slight hair movement"
    ),
    "subtle":   (
        "slow graceful movement, swaying hair, gentle body sway, "
        "fabric rippling with slow walk, measured elegant motion"
    ),
    "moderate": (
        "walking at confident pace, natural hand gestures, "
        "fabric flowing with movement, dynamic but controlled motion"
    ),
    "dynamic":  (
        "energetic confident movement, expressive gestures, "
        "fabric catching air, spin or turn with full body engagement, "
        "high energy fashion-film motion"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Negative prompt (shared across all stages)
# ─────────────────────────────────────────────────────────────────────────────

_UNIVERSAL_NEG = (
    "blurry, out of focus, noise, grain, watermark, text overlay, logo, "
    "tiktok watermark, instagram username, deformed face, bad anatomy, "
    "bad proportions, extra limbs, disfigured, mutation, low quality, "
    "cartoon, anime, painting, illustration, ugly, worst quality, "
    "lowres, bad lighting, overexposed, underexposed"
)

_PORTRAIT_NEG = _UNIVERSAL_NEG + (
    ", multiple people, crowd, duplicate faces, accessory distortion, "
    "asymmetric eyes, cross-eyed, teeth issues, hair artifacts"
)

_SYNC_NEG = _UNIVERSAL_NEG + (
    ", same scene as reference, exact copy of background, "
    "identical environment to source, pixel-for-pixel match, "
    "watermarked clothing, brand logos visible"
)

_MOTION_NEG = (
    "blurry, watermark, low quality, static, frozen, no motion, "
    "distorted face, bad anatomy, flickering, temporal inconsistency, "
    "jitter, unstable background"
)

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_seedance_prompt(
    niche:          str = "fashion",
    outfit_desc:    str = "",
    setting:        str = "default",
    lighting:       str = "golden_hour",
    hook_type:      str = "fabric_macro",
    walk_type:      str = "slow_sway",
    attitude:       str = "grace",
    expression:     str = "subtle_smile",
    fabric:         str = "default",
    motion_energy:  str = "moderate",
    num_images:     int = 1,
    num_videos:     int = 0,
    num_audio:      int = 0,
    duration:       int = 10,
) -> str:
    """
    Constructs a highly structured Jimeng Seedance 2.0 multi-segment storyboard prompt.
    Implements reference mapping (@Image1, @Video1, @Audio1), input limit compliance,
    2-second hook patterns, camera vocabulary, and multi-segment timeline control.
    """
    # 1. Input Constraints & Reference Mapping
    ref_parts = []
    
    # Restrict to technical limits (Images <= 9, Videos <= 3, Audio <= 3)
    safe_images = min(max(0, num_images), 9)
    safe_videos = min(max(0, num_videos), 3)
    safe_audio = min(max(0, num_audio), 3)
    
    if safe_images >= 1:
        ref_parts.append(f"@Image1 as the first frame, @Image1's character as the subject, wearing the outfit from @Image1")
    if safe_videos >= 1:
        ref_parts.append(f"reference @Video1's camera movement and action choreography")
    if safe_audio >= 1:
        ref_parts.append(f"BGM and sound effects reference @Audio1")
        
    ref_str = ", ".join(ref_parts)

    # 2. Extract presets and style blocks
    cat = FASHION_CATEGORIES.get(niche) or FASHION_CATEGORIES.get("fashion")
    style_kw = outfit_desc.strip(" ,") or cat['style_keywords']
    fab_desc = FABRIC_DESCRIPTORS.get(fabric, FABRIC_DESCRIPTORS["default"])
    light_desc = LIGHTING_PRESETS.get(lighting, LIGHTING_PRESETS["default"])
    loc_desc = LOCATION_PRESETS.get(setting, LOCATION_PRESETS["default"])
    walk_desc = WALK_TYPES.get(walk_type, WALK_TYPES["default"])
    att_desc = ATTITUDE_TYPES.get(attitude, ATTITUDE_TYPES["default"])
    expr_desc = EXPRESSION_TYPES.get(expression, EXPRESSION_TYPES["default"])
    hook_desc = HOOK_TYPES.get(hook_type, HOOK_TYPES["default"])

    # 3. Time-Segmented Storyboard Generation (0-2s, 2-5s, 5-10s, 10-15s)
    storyboard = []
    
    # 0-2s: The algorithmic retention gate hook
    storyboard.append(
        f"0-2s: [Attention Gate Hook: {hook_desc}. Instantly stop scrolling using high contrast, satisfying micro-animation, and pattern interrupts. Camera does a rapid movement or macro zoom.]"
    )
    
    # 2-5s: Development & Showcase
    storyboard.append(
        f"2-5s: [Garment showcase. Subject ambling in {walk_desc} displaying the {style_kw} ({fab_desc}). Environment context is {loc_desc}. Model exhibits {att_desc} with a {expr_desc}. Illuminated by {light_desc}. Smooth camera pan.]"
    )
    
    # 5-10s: Climax / Loop / CTA
    storyboard.append(
        f"5-10s: [Climax cinematic showcase. Slow push-in and orbit tracking shot around the subject. Bold confidence pose, flawless clothing fold details, professional editorial lighting, ending on high visual completion. High aesthetic quality.]"
    )

    if duration > 10:
        storyboard.append(
            f"10-15s: [Perfect loop resolution. Slowly pull away from the model as they look confidently at the camera, smooth lighting fade out, presenting clean space for branding overlays.]"
        )

    storyboard_str = "\n".join(storyboard)

    # 4. Synthesize final prompt string
    prompt = f"""[System: Jimeng Seedance 2.0 Multimodal Prompt Engine]

[References Setup]
{ref_str}

[Subject & Material Config]
Subject: {cat['atmosphere']} woman
Garment: {style_kw}
Fabric detail: {fab_desc}

[Timing & Storyboard Control]
{storyboard_str}

[Camera & Production Directives]
Camera movement: Push in, slow orbit, tracking follow shots. Cinematic fashion film, photorealistic 8k texture, soft natural lighting on face, crisp detail focus.
Sound design: Sync visual cuts to rhythm beats.
Negative: blurry, out of focus, noise, grain, watermark, text overlay, logo, bad anatomy, deformed face, lowres, duplicates, frozen, temporal inconsistency"""

    return prompt.strip()


def build_prompts(
    niche:          str = "fashion",
    outfit_desc:    str = "",
    setting:        str = "default",
    lighting:       str = "golden_hour",
    hook_type:      str = "fabric_macro",
    walk_type:      str = "slow_sway",
    attitude:       str = "grace",
    expression:     str = "subtle_smile",
    fabric:         str = "default",
    motion_energy:  str = "moderate",
    extra_tags:     str = "",
    num_images:     int = 1,
    num_videos:     int = 0,
    num_audio:      int = 0,
    duration:       int = 10,
) -> dict:
    """
    Build distinct, optimised prompts for local pipeline stages (SDXL/ControlNet/Wan2.1)
    and the native Jimeng Seedance 2.0 model on Higgsfield.
    """

    # Resolve lookups (with fallback)
    cat       = FASHION_CATEGORIES.get(niche) or FASHION_CATEGORIES.get("fashion")
    hook      = HOOK_TYPES.get(hook_type,    HOOK_TYPES["default"])
    light     = LIGHTING_PRESETS.get(lighting, LIGHTING_PRESETS["default"])
    location  = LOCATION_PRESETS.get(setting,  LOCATION_PRESETS["default"])
    fab       = FABRIC_DESCRIPTORS.get(fabric,  FABRIC_DESCRIPTORS["default"])
    walk      = WALK_TYPES.get(walk_type,    WALK_TYPES["default"])
    att       = ATTITUDE_TYPES.get(attitude,  ATTITUDE_TYPES["default"])
    expr      = EXPRESSION_TYPES.get(expression, EXPRESSION_TYPES["default"])
    motion    = MOTION_ENERGY_PROMPTS.get(motion_energy, MOTION_ENERGY_PROMPTS["moderate"])

    outfit    = outfit_desc.strip(" ,")
    extra     = (", " + extra_tags.strip(" ,")) if extra_tags.strip() else ""

    # Stage 1  —  Portrait (SDXL + IP-Adapter Plus Face)
    stage1 = (
        f"photorealistic portrait of a {cat['atmosphere']} woman, "
        f"{outfit or cat['style_keywords']}, "
        f"{cat['model_direction']}, "
        f"{expr}, {att}, "
        f"{light}, "
        f"professional fashion photography, "
        f"sharp focus RAW photo, "
        f"8k ultra-detailed skin texture, natural lighting on face, "
        f"instagram influencer aesthetic, scroll-stopping visual quality"
        f"{extra}"
    )

    # Stage 3  —  Pose & Style Sync (ControlNet OpenPose + IP-Adapter)
    stage3 = (
        f"photorealistic fashion photograph, "
        f"{cat['atmosphere']} woman, "
        f"{outfit or cat['style_keywords']}, "
        f"inspired by the pose composition, similar fashion aesthetic, "
        f"{walk}, {att}, {expr}, "
        f"{light}, "
        f"{location}, "
        f"{fab}, "
        f"{hook}, "
        f"original unique background scene different from source, "
        f"high-end fashion editorial quality, "
        f"8k professional photography, "
        f"natural seamless body proportions"
        f"{extra}"
    )

    # Stage 4  —  Motion Animation (Wan2.1 I2V)
    stage4 = (
        f"{cat['atmosphere']} woman in {outfit or cat['style_keywords']}, "
        f"{motion}, "
        f"{fab}, "
        f"{walk}, "
        f"cinematic fashion film aesthetic, "
        f"smooth natural movement, "
        f"professional model energy, "
        f"ultra-realistic, high production value"
        f"{extra}"
    )

    # Build the Jimeng Seedance 2.0 structured prompt
    seedance = build_seedance_prompt(
        niche=niche,
        outfit_desc=outfit_desc,
        setting=setting,
        lighting=lighting,
        hook_type=hook_type,
        walk_type=walk_type,
        attitude=attitude,
        expression=expression,
        fabric=fabric,
        motion_energy=motion_energy,
        num_images=num_images,
        num_videos=num_videos,
        num_audio=num_audio,
        duration=duration,
    )

    logger.info(
        f"📝  Prompts built | niche={niche} | hook={hook_type} | "
        f"setting={setting} | lighting={lighting} | fabric={fabric} | "
        f"motion={motion_energy} | Seedance 2.0 active"
    )

    return {
        "stage1_portrait":   stage1,
        "stage3_sync":       stage3,
        "stage4_motion":     stage4,
        "seedance_prompt":   seedance,
        "stage4_motion_neg": _MOTION_NEG,
        "portrait_neg":      _PORTRAIT_NEG,
        "sync_neg":          _SYNC_NEG,
        "meta": {
            "niche":         niche,
            "hook_type":     hook_type,
            "setting":       setting,
            "lighting":      lighting,
            "fabric":        fabric,
            "motion_energy": motion_energy,
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Gemini-powered Auto-Prompt Builder
# When a user sends a raw concept ("indian bride", "streetwear girl"),
# Gemini analyses it and fills all parameters automatically.
# Falls back to defaults if Gemini unavailable.
# ─────────────────────────────────────────────────────────────────────────────

_GEMINI_SYSTEM = """You are a fashion video prompt engineer specializing in AI influencer content.
Given a concept description, extract these parameters as a JSON object:
{
  "niche":         one of [ethnic_traditional, saree, lehenga, luxury_fashion, streetwear, casual_everyday, formal_business, evening_wear, fashion],
  "outfit_desc":   short specific outfit description (max 15 words),
  "setting":       one of [studio_white, urban_street, nature_garden, rooftop, luxury_interior, beach, market_bazaar, industrial, home_interior, default],
  "lighting":      one of [golden_hour, studio_editorial, soft_diffused, natural_daylight, neon_night, dramatic_backlight, warm_indoor, default],
  "hook_type":     one of [fabric_macro, power_walk, outfit_reveal, slow_mo_wind, mirror_reveal, color_match_env, accessory_zoom, spin_reveal, rapid_cuts, silhouette_shadow, default],
  "walk_type":     one of [power_walk, slow_sway, catwalk, casual_amble, graceful_walk, spin, default],
  "attitude":      one of [confidence, grace, playfulness, attitude, approachable, sensuality, default],
  "expression":    one of [strong_gaze, subtle_smile, blank_slate, smoldering, genuine_laugh, default],
  "fabric":        one of [silk, silk_saree, chiffon, denim, leather, sequin, velvet, lace, cotton_casual, embroidered, default],
  "motion_energy": one of [static, subtle, moderate, dynamic],
  "extra_tags":    any other relevant style words
}
Return ONLY valid JSON, no explanation."""


def auto_build_prompts(concept: str, gemini_api_key: str = None) -> dict:
    """
    Use Gemini to auto-detect all prompt parameters from a free-form concept string.

    Parameters
    ----------
    concept        : Free text, e.g. "beautiful indian bride in red lehenga at sunset"
    gemini_api_key : Optional Gemini API key. Falls back to GEMINI_API_KEY env var.

    Returns
    -------
    Same dict as build_prompts() plus "auto_params" key showing what Gemini chose.
    """
    import json

    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")

    if not api_key:
        logger.warning("⚠️  No GEMINI_API_KEY — falling back to default prompts.")
        return build_prompts()

    try:
                        
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            f"{_GEMINI_SYSTEM}\n\nConcept: {concept}"
        )
        raw = response.text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.split("\n")
                if not line.strip().startswith("```")
            )

        params = json.loads(raw)
        logger.info(f"🤖  Gemini auto-params: {params}")

        result = build_prompts(**{
            k: v for k, v in params.items()
            if k in [
                "niche", "outfit_desc", "setting", "lighting",
                "hook_type", "walk_type", "attitude", "expression",
                "fabric", "motion_energy", "extra_tags"
            ]
        })
        result["auto_params"] = params
        return result

    except Exception as e:
        logger.warning(f"⚠️  Gemini prompt build failed ({e}) — using defaults.")
        return build_prompts()
