import base64
import os

backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
project_root = os.path.dirname(backend_dir)
LOGO_PATH = os.path.join(project_root, "web", "public", "logo.jpg")

BRAND_NAME = "Agriculture is life Co., Ltd."
BRAND_TAGLINE = "Fresh Produce Export & Operations"
CURRENCY_CODE = "THB"
CURRENCY_SYMBOL = "฿"


def get_logo_base64() -> str:
    """Returns base64 encoded string of the company logo."""
    if os.path.exists(LOGO_PATH):
        try:
            with open(LOGO_PATH, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            pass
    return ""
