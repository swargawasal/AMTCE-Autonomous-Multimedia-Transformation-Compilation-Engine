# Vanguard Registry & Project Memory

This document acts as the high-level behavioral guide for the Vanguard Agentic Suite. ALL agents (Director, Sensor, Formatter) must reference this file before executing a mission.

## ✅ Winning Styles (Verified Positive Signals)
- **Cinematic Slow Zoom**: 1.2x zoom over 3s + emotional hook = 15% increase in retention.
- **Micro-Captions**: Single-word bursts on center-screen for high-energy niches (Fitness, Car).
- **Golden Hour Color**: Warm LUTs at 0.7 intensity for Fashion/Lifestyle.

## ❌ Failed Patterns (Avoid at all Costs)
- **Generic Captions**: "Product Link in Bio" leads to 40% bounce rate. Use "Shop the Look" instead.
- **Fast Cuts on Sad Clips**: Destroys emotional arc. Keep buildup > 1.5s for emotional content.
- **Over-Saturation**: Excessive color grading leads to "AI-Slop" flags on Instagram.

## 📜 Role-Specific Rules
### 👗 Fashion & Lifestyle
- **Rule**: Minimum 3 clips. Hook must be the highest quality visual.
- **Style**: Minimalistic captions, "Luxury" aesthetic, consistent 9:16 portrait.

### 🚗 Car & Automotive
- **Rule**: High information density (6+ segments).
- **Style**: Beat-match cuts, exhaust audio peaking at -3dB.

## 🛠️ Vanguard Working Agreements
- **Agreement 1**: Always sampling vision every 2s to save tokens.
- **Agreement 2**: Never ignore FFmpeg codec errors; classify as "simple" if it's a path issue.
- **Agreement 3**: Max 2 retries for self-healing before accepting the best result.