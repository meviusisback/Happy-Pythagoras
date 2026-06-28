import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # Request configurations
    TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
    USER_AGENT = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    
    # Crawler parameters
    MAX_DEPTH = int(os.getenv("MAX_CRAWL_DEPTH", "2"))
    MAX_PAGES = int(os.getenv("MAX_CRAWL_PAGES", "12"))
    
    # Search API configurations
    # Defaults to DuckDuckGo search (free). Can be configured for SerpAPI or Google Custom Search.
    SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "duckduckgo").lower()
    SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    GOOGLE_CX = os.getenv("GOOGLE_CX", "")

    # AI enhancement settings (set by sidebar at runtime)
    AI_ENABLED = False
    AI_PROVIDER = ""
    AI_MODEL = ""

    # Default request headers
    @classmethod
    def get_headers(cls):
        return {
            "User-Agent": cls.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.google.com/",
        }

    SENDER_COMPANY = os.getenv("SENDER_COMPANY", "")
