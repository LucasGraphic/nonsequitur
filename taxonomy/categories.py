# taxonomy/categories.py -- Category detection for Article Agent
# Slugs match PayloadCMS category slugs on lucasgraphic.com

CATEGORIES = {
    # -- DATA section ------------------------------------------------------
    "games":         ["steam game", "xbox game", "playstation game", "nintendo game",
                      "gaming", "esports", "early access game", "game patch",
                      "game update", "game review", "subnautica", "doom game",
                      "fortnite", "minecraft", "elden ring", "game release"],
    "ai-data":       ["large language model", "llm", "gpt-", "claude ai", "gemini ai",
                      "stable diffusion", "comfyui", "flux model", "midjourney",
                      "chatgpt", "neural network", "deepseek", "ollama",
                      "hugging face", "machine learning", "data science",
                      "ai model", "qwen", "artificial intelligence"],
    "hardware":      ["cpu benchmark", "gpu benchmark", "processor review",
                      "motherboard", "ram ddr", "ssd nvme", "nvidia rtx", "rtx ",
                      "amd radeon", "intel core", "arm chip", "semiconductor",
                      "overclocking", "raspberry pi", "arduino"],
    "software":      ["unreal engine", "unity engine", "godot engine",
                      "programming", "developer", "devops", "linux distro",
                      "docker", "kubernetes", "framework", "python script",
                      "javascript", "typescript", "rust lang", "golang",
                      "open source", "software update", "app release",
                      "game engine"],
    "security":      ["cybersecurity", "hacking", "vulnerability", "exploit",
                      "malware", "ransomware", "phishing", "zero day",
                      "CVE-", "penetration testing", "data breach", "cyberattack"],
    "entertainment": ["netflix series", "disney plus", "hbo series",
                      "movie review", "film review", "streaming show",
                      "music album", "concert tour", "anime series", "manga"],
    # -- PORTFOLIO section -------------------------------------------------
    "drone":           ["drone photography", "dji mavic", "dji mini",
                        "aerial photo", "fpv drone", "uav flight"],
    "portrait-studio": ["portrait studio", "studio portrait", "studio lighting",
                        "strobe light", "softbox"],
    "macro":           ["macro photography", "macro lens", "close-up photo",
                        "insect photo", "flower macro"],
    "portrait-outdoor": ["outdoor portrait", "natural light portrait",
                         "environmental portrait"],
    "product":         ["product photography", "commercial photography",
                        "product shoot"],
    "travel":          ["travel photography", "travel photo", "travel landscape"],
    "photography":     ["photography tips", "camera review", "lens review",
                        "lightroom", "photoshop editing", "exposure settings",
                        "aperture", "shutter speed"],
    "3d-exterior":     ["3d exterior", "architectural visualization",
                        "exterior render", "archviz"],
    "3d-interior":     ["3d interior", "interior render", "interior visualization"],
    "3d":              ["blender tutorial", "blender render", "unreal engine 5",
                        "maya 3d", "cinema4d", "3d modeling", "pbr texture",
                        "3d rendering", "cgi art"],
    "ai":              ["ai art", "ai generated image", "stable diffusion art",
                        "midjourney art", "dall-e", "comfyui workflow",
                        "flux image generation"],
    "other":           [],
}


def detect_category(topic: str) -> str:
    """Detect category from topic keywords. More specific categories checked first."""
    tl = topic.lower()

    priority_order = [
        "drone", "portrait-studio", "macro", "portrait-outdoor", "product",
        "travel", "3d-exterior", "3d-interior",
        "software", "hardware", "security", "games", "ai-data", "entertainment",
        "photography", "3d", "ai", "other",
    ]

    for category in priority_order:
        keywords = CATEGORIES.get(category, [])
        for kw in keywords:
            if kw.lower() in tl:
                return category
    return "other"
