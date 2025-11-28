import base64
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

# =========================
# BASIC CONFIG
# =========================

st.set_page_config(
    page_title="SNOW LIWA",
    page_icon="❄️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =========================
# SETTINGS
# =========================

BASE_DIR = Path(__file__).resolve().parent
BACKGROUND_IMAGE_PATH = BASE_DIR / "assets" / "snow_liwa_bg.jpg"
HERO_IMAGE_PATH = BACKGROUND_IMAGE_PATH
DATA_DIR = BASE_DIR / "data"
BOOKINGS_FILE = DATA_DIR / "bookings.xlsx"
UI_CONFIG_FILE = DATA_DIR / "ui_config.json"

TICKET_PRICE = 175  # AED per ticket

# Ziina API config
ZIINA_API_BASE = "https://api-v2.ziina.com/api"

# Read Ziina config (bypass secrets for now)
ZIINA_ACCESS_TOKEN = "FAKE_ACCESS_TOKEN"
APP_BASE_URL = "https://snow-liwa.streamlit.app"
ZIINA_TEST_MODE = True

PAGES = {
    "Welcome": "welcome",
    "Who we are": "who",
    "Experience": "experience",
    "Contact": "contact",
    "Dashboard (Admin)": "dashboard",
}

ADMIN_PASSWORD = "snowadmin123"  # Legacy; login removed
DEFAULT_ADMIN_PIN = "1234"  # UI settings PIN fallback

# =========================
# DATA HELPERS
# =========================


def ensure_data_file():
    DATA_DIR.mkdir(exist_ok=True)
    if not BOOKINGS_FILE.is_file():
        df = pd.DataFrame(
            columns=[
                "booking_id",
                "created_at",
                "name",
                "phone",
                "tickets",
                "ticket_price",
                "total_amount",
                "status",  # pending / paid / cancelled
                "payment_intent_id",  # from Ziina
                "payment_status",  # requires_payment_instrument / completed / failed...
                "redirect_url",  # Ziina hosted page
                "notes",
            ]
        )
        df.to_excel(BOOKINGS_FILE, index=False)


def load_bookings():
    ensure_data_file()
    return pd.read_excel(BOOKINGS_FILE)


def save_bookings(df: pd.DataFrame):
    df.to_excel(BOOKINGS_FILE, index=False)


def get_next_booking_id(df: pd.DataFrame) -> str:
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"SL-{today}-"
    todays = df[df["booking_id"].astype(str).str.startswith(prefix)]
    if todays.empty:
        seq = 1
    else:
        last = todays["booking_id"].iloc[-1]
        try:
            seq = int(str(last).split("-")[-1]) + 1
        except Exception:
            seq = len(todays) + 1
    return prefix + f"{seq:03d}"


# =========================
# ZIINA API HELPERS
# =========================


def has_ziina_configured() -> bool:
    return bool(ZIINA_ACCESS_TOKEN) and ZIINA_ACCESS_TOKEN != "PUT_YOUR_ZIINA_ACCESS_TOKEN_IN_SECRETS"


def create_payment_intent(amount_aed: float, booking_id: str, customer_name: str) -> dict | None:
    """Create Payment Intent via Ziina API and return JSON."""
    if not has_ziina_configured():
        st.error("Ziina API token not configured. Add it to .streamlit/secrets.toml under [ziina].")
        return None

    amount_fils = int(round(amount_aed * 100))  # Ziina expects amount in fils (cents equivalent)

    url = f"{ZIINA_API_BASE}/payment_intent"
    headers = {
        "Authorization": f"Bearer {ZIINA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    # From Ziina docs: using {PAYMENT_INTENT_ID} in URLs
    base_return = APP_BASE_URL.rstrip("/")
    success_url = f"{base_return}/?result=success&pi_id={{PAYMENT_INTENT_ID}}"
    cancel_url = f"{base_return}/?result=cancel&pi_id={{PAYMENT_INTENT_ID}}"
    failure_url = f"{base_return}/?result=failure&pi_id={{PAYMENT_INTENT_ID}}"

    payload = {
        "amount": amount_fils,
        "currency_code": "AED",
        "message": f"Snow Liwa booking {booking_id} - {customer_name}",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "failure_url": failure_url,
        "test": ZIINA_TEST_MODE,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
    except requests.RequestException as e:
        st.error(f"Error talking to Ziina API: {e}")
        return None

    if resp.status_code >= 400:
        st.error(f"Ziina API error ({resp.status_code}): {resp.text}")
        return None

    return resp.json()


def get_payment_intent(pi_id: str) -> dict | None:
    """Fetch payment intent from Ziina."""
    if not has_ziina_configured():
        st.error("Ziina API token not configured. Add it to .streamlit/secrets.toml under [ziina].")
        return None

    url = f"{ZIINA_API_BASE}/payment_intent/{pi_id}"
    headers = {"Authorization": f"Bearer {ZIINA_ACCESS_TOKEN}"}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        st.error(f"Error talking to Ziina API: {e}")
        return None

    if resp.status_code >= 400:
        st.error(f"Ziina API error ({resp.status_code}): {resp.text}")
        return None

    return resp.json()


def sync_payments_from_ziina(df: pd.DataFrame) -> pd.DataFrame:
    """Loop pending bookings and update payment status from Ziina."""
    if not has_ziina_configured():
        st.error("Ziina API not configured.")
        return df

    updated = False
    for idx, row in df.iterrows():
        pi_id = str(row.get("payment_intent_id") or "").strip()
        if not pi_id:
            continue

        pi = get_payment_intent(pi_id)
        if not pi:
            continue

        status = pi.get("status")
        if not status:
            continue

        df.at[idx, "payment_status"] = status

        if status == "completed":
            df.at[idx, "status"] = "paid"
            updated = True
        elif status in ("failed", "canceled"):
            df.at[idx, "status"] = "cancelled"
            updated = True

    if updated:
        save_bookings(df)
    return df


# =========================
# UI HELPERS
# =========================


def encode_image_base64(image_path: Path) -> str | None:
    if not image_path.is_file():
        return None
    try:
        return base64.b64encode(image_path.read_bytes()).decode()
    except Exception:
        return None


def ensure_ui_config():
    DATA_DIR.mkdir(exist_ok=True)
    if UI_CONFIG_FILE.is_file():
        return
    default_config = {
        "admin_pin": DEFAULT_ADMIN_PIN,
        "background": {
            "mode": "gradient",  # solid | gradient | image
            "solid_color": "#f4f8ff",
            "gradient_start": "#f4f8ff",
            "gradient_end": "#eaf2fd",
            "overlay_color": "#ffffff",
            "overlay_opacity": 0.35,
            "image_name": "snow_liwa_bg.jpg",
            "fixed": False,
        },
        "hero": {
            "image_name": "snow_liwa_bg.jpg",
            "height": "medium",  # small | medium | large
        },
        "theme": {
            "primary": "#123764",
            "secondary": "#0d2a4f",
            "card_bg": "#fdfdff",
            "radius": "medium",  # small | medium | large
            "shadow": "soft",  # none | soft | strong
            "mode": "light",  # light | dark
        },
        "typography": {
            "heading_font": "Poppins, sans-serif",
            "body_font": "Inter, sans-serif",
            "arabic_font": "Tajawal, sans-serif",
            "scale": "default",  # small | default | large
            "direction": "ltr",  # ltr | rtl
            "language_mode": "bilingual",  # bilingual | english | arabic
        },
        "content": {
            "nav": {
                "name": "NAME",
                "about": "ABOUT",
                "activities": "ACTIVITIES",
                "invites": "INVYS",
                "contact": "CONTACT",
            },
            "cards": {
                "activities_title": "ACTIVITIES",
                "activities_desc": "Snow play, warm drinks, chocolate fountain, and winter vibes for friends & family.",
                "events_title": "EVENTS",
                "events_desc": "Group bookings, private sessions, and curated winter moments at our secret Liwa spot.",
                "contact_title": "CONTACT",
                "contact_desc": "Reach us on WhatsApp or Instagram snowliwa. We'll share the exact location after booking.",
            },
            "booking": {
                "heading": "??? Book your ticket",
                "price_text": f"Entrance ticket: {TICKET_PRICE} AED per person.",
                "pay_button": "Proceed to payment with Ziina",
                "success_message": "? Booking created!",
            },
        },
        "icons": {
            "enabled": True,
            "size": "medium",  # small | medium | large
            "animation": "float",  # float | none
        },
        "form": {
            "show_notes": True,
            "label_position": "top",  # top | placeholder
            "button_width": "auto",  # auto | full
            "show_ticket_icon": True,
            "labels": {
                "name": "Name / ????? ??????",
                "phone": "Phone / ??? ?????? (??????)",
                "tickets": "Number of tickets / ??? ???????",
                "notes": "Notes (optional) / ??????? ????????",
            },
        },
    }
    UI_CONFIG_FILE.write_text(json.dumps(default_config, indent=2))


def load_ui_config() -> dict:
    ensure_ui_config()
    try:
        return json.loads(UI_CONFIG_FILE.read_text())
    except Exception:
        return {}


def save_ui_config(config: dict):
    try:
        UI_CONFIG_FILE.write_text(json.dumps(config, indent=2))
    except Exception:
        st.warning("Could not save UI settings.")


def _font_scale(scale: str) -> float:
    return {"small": 0.9, "default": 1.0, "large": 1.1}.get(scale, 1.0)


def _radius_value(radius: str) -> str:
    return {"small": "10px", "medium": "18px", "large": "28px"}.get(radius, "18px")


def _shadow_value(shadow: str) -> str:
    if shadow == "none":
        return "none"
    if shadow == "strong":
        return "0 20px 50px rgba(8, 46, 102, 0.20)"
    return "0 14px 34px rgba(8, 46, 102, 0.10)"


def _hero_height(height: str) -> str:
    return {"small": "360px", "medium": "480px", "large": "620px"}.get(height, "480px")


def get_assets_images():
    if not (BASE_DIR / "assets").is_dir():
        return []
    return sorted([p.name for p in (BASE_DIR / "assets").iterdir() if p.is_file()])


def resolve_asset(image_name: str) -> Path | None:
    if not image_name:
        return None
    candidate = BASE_DIR / "assets" / image_name
    return candidate if candidate.is_file() else None


def set_background(config: dict):
    bg = config.get("background", {})
    mode = bg.get("mode", "gradient")
    overlay_color = bg.get("overlay_color", "#ffffff")
    overlay_opacity = bg.get("overlay_opacity", 0.35)
    fixed = "fixed" if bg.get("fixed") else "scroll"

    if mode == "solid":
        base = bg.get("solid_color", "#f4f8ff")
        background_css = f"background:{base};"
    elif mode == "image":
        image_path = resolve_asset(bg.get("image_name", "")) or BACKGROUND_IMAGE_PATH
        if image_path and image_path.is_file():
            background_css = (
                f"background: url('{image_path.as_posix()}') center/cover no-repeat;"
            )
        else:
            background_css = "background: linear-gradient(180deg, #f4f8ff 0%, #eaf2fd 100%);"
    else:
        start = bg.get("gradient_start", "#f4f8ff")
        end = bg.get("gradient_end", "#eaf2fd")
        background_css = f"background: linear-gradient(180deg, {start} 0%, {end} 100%);"

    css = f"""
    <style>
    .stApp {{
        {background_css}
        background-attachment: {fixed};
        position: relative;
    }}
    .stApp:before {{
        content: '';
        position: fixed;
        inset: 0;
        background: {overlay_color};
        opacity: {overlay_opacity};
        pointer-events: none;
        z-index: 0;
    }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def inject_base_css(config: dict):
    theme = config.get("theme", {})
    typo = config.get("typography", {})
    icons_cfg = config.get("icons", {})
    form_cfg = config.get("form", {})
    scale = _font_scale(typo.get("scale", "default"))
    radius = _radius_value(theme.get("radius", "medium"))
    shadow = _shadow_value(theme.get("shadow", "soft"))
    hero_height = _hero_height(config.get("hero", {}).get("height", "medium"))
    mode = theme.get("mode", "light")
    card_bg = theme.get("card_bg", "#ffffff")

    primary = theme.get("primary", "#123764")
    secondary = theme.get("secondary", "#0d2a4f")

    sticker_size = {"small": "2.2rem", "medium": "3rem", "large": "3.6rem"}.get(
        icons_cfg.get("size", "medium"), "3rem"
    )
    sticker_animation = (
        "animation: floaty 6s ease-in-out infinite;"
        if icons_cfg.get("animation", "float") == "float"
        else ""
    )

    direction = typo.get("direction", "ltr")
    label_position = form_cfg.get("label_position", "top")
    button_width = "100%" if form_cfg.get("button_width") == "full" else "auto"

    text_color = "#0d2a4f" if mode == "light" else "#e9f1ff"
    sub_text = "#4f6077" if mode == "light" else "#d4def0"
    page_bg = (
        "linear-gradient(180deg, #0d1b2f 0%, #0b243f 100%)"
        if mode == "dark"
        else None
    )

    css = f"""
    <style>
    :root {{
        --primary-color: {primary};
        --secondary-color: {secondary};
        --card-bg: {card_bg};
        --radius: {radius};
        --shadow: {shadow};
        --text-color: {text_color};
        --text-sub: {sub_text};
        --heading-font: {typo.get("heading_font", "Poppins, sans-serif")};
        --body-font: {typo.get("body_font", "Inter, sans-serif")};
        --arabic-font: {typo.get("arabic_font", "Tajawal, sans-serif")};
        --scale: {scale};
    }}
    .stApp {{
        color: var(--text-color);
        direction: {direction};
        {"background:" + page_bg + ";" if page_bg else ""}
    }}
    .page-container {{
        max-width: 1180px;
        margin: 0 auto;
        padding: 0.8rem 0.75rem 1.6rem;
        position: relative;
        z-index: 1;
    }}
    .page-card {{
        max-width: 1180px;
        width: 100%;
        background: transparent;
        box-shadow: none;
        padding: 0;
    }}
    @media (max-width: 800px) {{
        .page-card {{ padding: 0; }}
    }}
    .hero-card {{
        position: relative;
        border-radius: 30px;
        overflow: hidden;
        min-height: {hero_height};
        background-size: cover;
        background-position: center;
        box-shadow: 0 18px 48px rgba(14, 59, 110, 0.26);
        isolation: isolate;
    }}
    .sticker {{
        position: absolute;
        z-index: 3;
        font-size: {sticker_size};
        opacity: 0.9;
        filter: drop-shadow(0 6px 12px rgba(0,0,0,0.18));
        pointer-events: none;
        {sticker_animation}
    }}
    @keyframes floaty {{
        0% {{ transform: translateY(0px); }}
        50% {{ transform: translateY(-8px); }}
        100% {{ transform: translateY(0px); }}
    }}
    .sticker.kid {{ top: 62%; left: 12%; }}
    .sticker.snowman {{ top: 24%; right: 14%; }}
    .sticker.deer {{ bottom: 12%; right: 30%; }}
    .sticker.mitten {{ top: 12%; left: 8%; }}
    .hero-layer {{
        position: absolute;
        inset: 0;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.0) 0%, rgba(220, 235, 255, 0.45) 100%);
        z-index: 1;
    }}
    .hero-content {{
        position: relative;
        z-index: 2;
        width: 100%;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        gap: 1.5rem;
        padding: 2.8rem 2rem;
        color: var(--text-color);
        text-align: center;
        font-family: var(--heading-font);
        transform: scale(var(--scale));
    }}
    .hero-nav {{
        display: flex;
        gap: 1.8rem;
        letter-spacing: 0.18em;
        font-size: 0.9rem;
        text-transform: uppercase;
        color: var(--text-color);
    }}
    .hero-title {{
        font-size: 3.6rem;
        line-height: 1.05;
        letter-spacing: 0.18em;
        font-weight: 800;
        color: var(--text-color);
        text-shadow: 0 10px 24px rgba(0, 0, 0, 0.14);
    }}
    .hero-tags {{
        display: flex;
        gap: 1rem;
        flex-wrap: wrap;
        justify-content: center;
    }}
    .hero-pill {{
        background: rgba(255, 255, 255, 0.92);
        color: var(--secondary-color);
        padding: 0.6rem 1.4rem;
        border-radius: 999px;
        font-weight: 700;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.16);
        letter-spacing: 0.08em;
    }}
    .info-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1rem;
        margin: 1.1rem 0 1.0rem 0;
    }}
    .info-card {{
        background: var(--card-bg);
        border: 1px solid #e1e8f4;
        border-radius: var(--radius);
        padding: 1.1rem 1.25rem;
        box-shadow: var(--shadow);
    }}
    .info-card h3 {{
        margin: 0 0 0.4rem 0;
        font-size: 1.1rem;
        letter-spacing: 0.08em;
        color: var(--secondary-color);
        font-family: var(--heading-font);
    }}
    .info-card p {{
        margin: 0;
        color: var(--text-sub);
        line-height: 1.5;
        font-family: var(--body-font);
        transform: scale(var(--scale));
    }}
    .section-card {{
        background: #ffffff;
        border: 1px solid #e3ecf8;
        border-radius: var(--radius);
        padding: 1.4rem 1.4rem 1.2rem 1.4rem;
        box-shadow: var(--shadow);
        margin-top: 1rem;
    }}
    .snow-title {{
        text-align: center;
        font-size: 3rem;
        font-weight: 700;
        letter-spacing: 0.30em;
        margin-bottom: 0.4rem;
        font-family: var(--heading-font);
    }}
    .subheading {{
        text-align: center;
        font-size: 0.95rem;
        opacity: 0.8;
        margin-bottom: 2rem;
        font-family: var(--body-font);
    }}
    .arabic {{
        direction: rtl;
        text-align: right;
        font-size: 1rem;
        line-height: 1.8;
        font-family: var(--arabic-font);
    }}
    .english {{
        direction: ltr;
        text-align: left;
        font-size: 0.98rem;
        line-height: 1.7;
        font-family: var(--body-font);
    }}
    .dual-column {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 2.25rem;
    }}
    @media (max-width: 800px) {{
        .dual-column {{ grid-template-columns: 1fr; }}
        .hero-card {{ min-height: 360px; }}
        .hero-title {{ font-size: 2.6rem; }}
        .hero-nav {{ gap: 0.7rem; font-size: 0.78rem; }}
        .hero-content {{ padding: 2rem 1.2rem; gap: 1rem; }}
    }}
    .ticket-price {{
        font-size: 1.2rem;
        font-weight: 700;
        margin-top: 1rem;
    }}
    .stButton>button {{
        border-radius: 999px;
        padding: 0.7rem 1.6rem;
        font-weight: 600;
        letter-spacing: 0.08em;
        background: var(--primary-color);
        border: 0;
        color: #ffffff;
        width: {button_width};
        box-shadow: var(--shadow);
    }}
    .center-btn {{
        display: flex;
        justify-content: center;
        margin-top: 0.5rem;
        margin-bottom: 0.5rem;
    }}
    .footer-note {{
        text-align: center;
        font-size: 0.8rem;
        opacity: 0.75;
        margin-top: 1.5rem;
    }}
    .settings-fab {{
        position: fixed;
        top: 16px;
        right: 16px;
        z-index: 999;
    }}
    .settings-fab button {{
        border-radius: 999px;
        padding: 0.45rem 0.9rem;
        background: var(--secondary-color);
        color: #fff;
        border: none;
        box-shadow: 0 10px 24px rgba(0,0,0,0.16);
    }}
    .form-label-inline label {{
        display: {"none" if label_position == "placeholder" else "block"};
    }}
    </style>
    """
    st.markdown(
        css,
        unsafe_allow_html=True,
    )


def init_state():
    if "page" not in st.session_state:
        st.session_state.page = "welcome"


def page_nav():
    pass


def get_query_params() -> dict:
    """Handle query params in both new and old Streamlit."""
    try:
        qp = st.query_params
        if hasattr(qp, "to_dict"):
            return qp.to_dict()
        return dict(qp)
    except Exception:
        try:
            return st.experimental_get_query_params()
        except Exception:
            return {}


def _normalize_query_value(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def render_settings_panel(config: dict) -> dict:
    if "ui_settings_open" not in st.session_state:
        st.session_state.ui_settings_open = False
    if "ui_settings_authed" not in st.session_state:
        st.session_state.ui_settings_authed = False

    with st.container():
        cols = st.columns([0.85, 0.15])
        with cols[1]:
            if st.button("⚙️", key="open_settings_btn", help="Open settings", use_container_width=True):
                st.session_state.ui_settings_open = not st.session_state.ui_settings_open

    if not st.session_state.ui_settings_open:
        return config

    if not st.session_state.ui_settings_authed:
        pin = st.text_input("Enter admin PIN to access settings", type="password")
        if st.button("Unlock settings"):
            if pin == str(config.get("admin_pin", DEFAULT_ADMIN_PIN)):
                st.session_state.ui_settings_authed = True
                st.success("Settings unlocked.")
            else:
                st.error("Incorrect PIN.")
        return config

    st.markdown("### Settings (UI only)")
    with st.form("ui_settings_form"):
        st.caption("Changes affect CSS, text, and images only. Booking logic, data storage, and Ziina integration stay untouched.")

        bg = config.get("background", {})
        hero_cfg = config.get("hero", {})
        theme = config.get("theme", {})
        typo = config.get("typography", {})
        content = config.get("content", {})
        icons_cfg = config.get("icons", {})
        form_cfg = config.get("form", {})

        st.subheader("Background")
        bg_mode = st.selectbox("Background type", ["solid", "gradient", "image"], index=["solid", "gradient", "image"].index(bg.get("mode", "gradient")))
        solid_color = st.color_picker("Solid color", bg.get("solid_color", "#f4f8ff"))
        gradient_start = st.color_picker("Gradient start", bg.get("gradient_start", "#f4f8ff"))
        gradient_end = st.color_picker("Gradient end", bg.get("gradient_end", "#eaf2fd"))
        assets = get_assets_images()
        hero_image_choice = st.selectbox("Background image from assets", options=[""] + assets, index=([""] + assets).index(bg.get("image_name", "")) if bg.get("image_name", "") in ([""] + assets) else 0)
        overlay_color = st.color_picker("Overlay color", bg.get("overlay_color", "#ffffff"))
        overlay_opacity = st.slider("Overlay opacity", 0.0, 0.9, float(bg.get("overlay_opacity", 0.35)))
        bg_fixed = st.checkbox("Fixed background", value=bg.get("fixed", False))

        st.subheader("Hero")
        hero_image = st.selectbox("Hero image (optional)", options=[""] + assets, index=([""] + assets).index(hero_cfg.get("image_name", "")) if hero_cfg.get("image_name", "") in ([""] + assets) else 0)
        hero_height = st.selectbox("Hero height", options=["small", "medium", "large"], index=["small", "medium", "large"].index(hero_cfg.get("height", "medium")))

        st.subheader("Theme")
        primary = st.color_picker("Primary color", theme.get("primary", "#123764"))
        secondary = st.color_picker("Secondary color", theme.get("secondary", "#0d2a4f"))
        card_bg = st.color_picker("Card background", theme.get("card_bg", "#fdfdff"))
        radius = st.selectbox("Radius", ["small", "medium", "large"], index=["small", "medium", "large"].index(theme.get("radius", "medium")))
        shadow = st.selectbox("Shadow", ["none", "soft", "strong"], index=["none", "soft", "strong"].index(theme.get("shadow", "soft")))
        theme_mode = st.selectbox("Mode", ["light", "dark"], index=["light", "dark"].index(theme.get("mode", "light")))

        st.subheader("Typography & Language")
        heading_font = st.text_input("Heading font", typo.get("heading_font", "Poppins, sans-serif"))
        body_font = st.text_input("Body font", typo.get("body_font", "Inter, sans-serif"))
        arabic_font = st.text_input("Arabic font", typo.get("arabic_font", "Tajawal, sans-serif"))
        scale = st.selectbox("Font scale", ["small", "default", "large"], index=["small", "default", "large"].index(typo.get("scale", "default")))
        direction = st.selectbox("Direction", ["ltr", "rtl"], index=["ltr", "rtl"].index(typo.get("direction", "ltr")))
        language_mode = st.selectbox("Language display", ["bilingual", "english", "arabic"], index=["bilingual", "english", "arabic"].index(typo.get("language_mode", "bilingual")))

        st.subheader("Content")
        nav_cfg = content.get("nav", {})
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            nav_name = st.text_input("Nav: Name", nav_cfg.get("name", "NAME"))
        with c2:
            nav_about = st.text_input("Nav: About", nav_cfg.get("about", "ABOUT"))
        with c3:
            nav_activities = st.text_input("Nav: Activities", nav_cfg.get("activities", "ACTIVITIES"))
        with c4:
            nav_invites = st.text_input("Nav: Invites", nav_cfg.get("invites", "INVYS"))
        with c5:
            nav_contact = st.text_input("Nav: Contact", nav_cfg.get("contact", "CONTACT"))

        cards_cfg = content.get("cards", {})
        activities_title = st.text_input("Card ACTIVITIES title", cards_cfg.get("activities_title", "ACTIVITIES"))
        activities_desc = st.text_area("Card ACTIVITIES description", cards_cfg.get("activities_desc", ""), height=60)
        events_title = st.text_input("Card EVENTS title", cards_cfg.get("events_title", "EVENTS"))
        events_desc = st.text_area("Card EVENTS description", cards_cfg.get("events_desc", ""), height=60)
        contact_title = st.text_input("Card CONTACT title", cards_cfg.get("contact_title", "CONTACT"))
        contact_desc = st.text_area("Card CONTACT description", cards_cfg.get("contact_desc", ""), height=60)

        booking_cfg = content.get("booking", {})
        booking_heading = st.text_input("Booking heading", booking_cfg.get("heading", "??? Book your ticket"))
        booking_price_text = st.text_input("Booking price text (display only)", booking_cfg.get("price_text", f"Entrance ticket: {TICKET_PRICE} AED per person."))
        pay_button = st.text_input("Pay button text", booking_cfg.get("pay_button", "Proceed to payment with Ziina"))
        success_message = st.text_input("Success message", booking_cfg.get("success_message", "? Booking created!"))

        st.subheader("Hero icons")
        icons_enabled = st.checkbox("Show emojis", value=icons_cfg.get("enabled", True))
        icons_size = st.selectbox("Icon size", ["small", "medium", "large"], index=["small", "medium", "large"].index(icons_cfg.get("size", "medium")))
        icons_anim = st.selectbox("Icon animation", ["float", "none"], index=["float", "none"].index(icons_cfg.get("animation", "float")))

        st.subheader("Form (UI only)")
        show_notes = st.checkbox("Show notes field", value=form_cfg.get("show_notes", True))
        label_position = st.selectbox("Label position", ["top", "placeholder"], index=["top", "placeholder"].index(form_cfg.get("label_position", "top")))
        button_width = st.selectbox("Button width", ["auto", "full"], index=["auto", "full"].index(form_cfg.get("button_width", "auto")))
        show_ticket_icon = st.checkbox("Show ticket icon on button", value=form_cfg.get("show_ticket_icon", True))
        labels_cfg = form_cfg.get("labels", {})
        name_label = st.text_input("Label: Name", labels_cfg.get("name", "Name / ????? ??????"))
        phone_label = st.text_input("Label: Phone", labels_cfg.get("phone", "Phone / ??? ?????? (??????)"))
        tickets_label = st.text_input("Label: Tickets", labels_cfg.get("tickets", "Number of tickets / ??? ???????"))
        notes_label = st.text_input("Label: Notes", labels_cfg.get("notes", "Notes (optional) / ??????? ????????"))

        submitted = st.form_submit_button("Save UI settings")
        if submitted:
            new_config = config.copy()
            new_config["background"] = {
                "mode": bg_mode,
                "solid_color": solid_color,
                "gradient_start": gradient_start,
                "gradient_end": gradient_end,
                "overlay_color": overlay_color,
                "overlay_opacity": overlay_opacity,
                "image_name": hero_image_choice,
                "fixed": bg_fixed,
            }
            new_config["hero"] = {
                "image_name": hero_image,
                "height": hero_height,
            }
            new_config["theme"] = {
                "primary": primary,
                "secondary": secondary,
                "card_bg": card_bg,
                "radius": radius,
                "shadow": shadow,
                "mode": theme_mode,
            }
            new_config["typography"] = {
                "heading_font": heading_font,
                "body_font": body_font,
                "arabic_font": arabic_font,
                "scale": scale,
                "direction": direction,
                "language_mode": language_mode,
            }
            new_config["content"] = {
                "nav": {
                    "name": nav_name,
                    "about": nav_about,
                    "activities": nav_activities,
                    "invites": nav_invites,
                    "contact": nav_contact,
                },
                "cards": {
                    "activities_title": activities_title,
                    "activities_desc": activities_desc,
                    "events_title": events_title,
                    "events_desc": events_desc,
                    "contact_title": contact_title,
                    "contact_desc": contact_desc,
                },
                "booking": {
                    "heading": booking_heading,
                    "price_text": booking_price_text,
                    "pay_button": pay_button,
                    "success_message": success_message,
                },
            }
            new_config["icons"] = {
                "enabled": icons_enabled,
                "size": icons_size,
                "animation": icons_anim,
            }
            new_config["form"] = {
                "show_notes": show_notes,
                "label_position": label_position,
                "button_width": button_width,
                "show_ticket_icon": show_ticket_icon,
                "labels": {
                    "name": name_label,
                    "phone": phone_label,
                    "tickets": tickets_label,
                    "notes": notes_label,
                },
            }
            save_ui_config(new_config)
            st.session_state.ui_settings_open = False
            st.success("Settings saved. Reloading...")
            st.experimental_rerun()

    return config


# =========================
# PAGE CONTENT
# =========================


def render_welcome(config: dict):
    hero_cfg = config.get("hero", {})
    icons_cfg = config.get("icons", {})
    content_cfg = config.get("content", {})
    form_cfg = config.get("form", {})
    booking_cfg = content_cfg.get("booking", {})

    hero_image = resolve_asset(hero_cfg.get("image_name", "")) or HERO_IMAGE_PATH or BACKGROUND_IMAGE_PATH
    hero_style = (
        f"background-image: url('{hero_image.as_posix()}');"
        if hero_image and hero_image.is_file()
        else "background: linear-gradient(180deg, #dfeffd 0%, #c8d9f0 100%);"
    )

    nav_cfg = content_cfg.get("nav", {})
    cards_cfg = content_cfg.get("cards", {})

    icons_html = ""
    if icons_cfg.get("enabled", True):
        icons_html = """
            <div class="sticker kid">????</div>
            <div class="sticker snowman">??</div>
            <div class="sticker deer">??</div>
            <div class="sticker mitten">??</div>
        """

    st.markdown(
        f"""
        <div class="hero-card" style="{hero_style}">
            <div class="hero-layer"></div>
            {icons_html}
            <div class="hero-content">
                <div class="hero-nav">
                    <span>{nav_cfg.get("name", "NAME")}</span>
                    <span>{nav_cfg.get("about", "ABOUT")}</span>
                    <span>{nav_cfg.get("activities", "ACTIVITIES")}</span>
                    <span>{nav_cfg.get("invites", "INVYS")}</span>
                    <span>{nav_cfg.get("contact", "CONTACT")}</span>
                </div>
                <div class="hero-title">SNOW<br>LIWA</div>
                <div class="hero-tags">
                    <span class="hero-pill">ICE SKATING</span>
                    <span class="hero-pill">SLADDING</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="info-grid">
            <div class="info-card">
                <h3>{cards_cfg.get("activities_title", "ACTIVITIES")}</h3>
                <p>{cards_cfg.get("activities_desc", "")}</p>
            </div>
            <div class="info-card">
                <h3>{cards_cfg.get("events_title", "EVENTS")}</h3>
                <p>{cards_cfg.get("events_desc", "")}</p>
            </div>
            <div class="info-card">
                <h3>{cards_cfg.get("contact_title", "CONTACT")}</h3>
                <p>{cards_cfg.get("contact_desc", "")}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown(f"### {booking_cfg.get('heading', '??? Book your ticket')}")
    st.write(booking_cfg.get("price_text", f"Entrance ticket: **{TICKET_PRICE} AED** per person."))

    labels_cfg = form_cfg.get("labels", {})
    label_mode = form_cfg.get("label_position", "top")
    label_visibility = "visible" if label_mode == "top" else "collapsed"

    with st.form("booking_form"):
        name = st.text_input(
            labels_cfg.get("name", "Name"),
            label_visibility=label_visibility,
            placeholder=labels_cfg.get("name", "Name") if label_mode == "placeholder" else None,
        )
        phone = st.text_input(
            labels_cfg.get("phone", "Phone"),
            label_visibility=label_visibility,
            placeholder=labels_cfg.get("phone", "Phone") if label_mode == "placeholder" else None,
        )
        tickets = st.number_input(
            labels_cfg.get("tickets", "Number of tickets"),
            1,
            20,
            1,
        )
        notes = None
        if form_cfg.get("show_notes", True):
            notes = st.text_area(
                labels_cfg.get("notes", "Notes"),
                height=70,
                label_visibility=label_visibility,
                placeholder=labels_cfg.get("notes", "Notes") if label_mode == "placeholder" else None,
            )
        btn_label = booking_cfg.get("pay_button", "Proceed to payment with Ziina")
        if form_cfg.get("show_ticket_icon", True):
            btn_label = "🎟️ " + btn_label
        submitted = st.form_submit_button(btn_label)

    st.markdown("</div>", unsafe_allow_html=True)

    if submitted:
        if not name or not phone:
            st.error("? ?????? ????? ????? ???? ??????.")
            return

        df = load_bookings()
        booking_id = get_next_booking_id(df)
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_amount = tickets * TICKET_PRICE

        # 1) Create Payment Intent
        pi_json = create_payment_intent(total_amount, booking_id, name)

        if pi_json:
            payment_intent_id = pi_json.get("id", "")
            redirect_url = pi_json.get("redirect_url", "")
            payment_status = pi_json.get("status", "requires_payment_instrument")
        else:
            payment_intent_id = ""
            redirect_url = ""
            payment_status = "error"

        # 2) Save booking (logic untouched)
        new_row = {
            "booking_id": booking_id,
            "created_at": created_at,
            "name": name,
            "phone": phone,
            "tickets": int(tickets),
            "ticket_price": TICKET_PRICE,
            "total_amount": float(total_amount),
            "status": "pending",
            "payment_intent_id": payment_intent_id,
            "payment_status": payment_status,
            "redirect_url": redirect_url,
            "notes": notes,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_bookings(df)

        st.success(
            f"{booking_cfg.get('success_message', '? Booking created!')}\n\n"
            f"**Booking ID:** {booking_id}\n\n"
            f"Total amount: **{total_amount} AED** for {tickets} ticket(s)."
        )

        if redirect_url:
            st.info(
                "1?? ???? ??? ?? ????? ??????? ???? ???? Ziina.\n"
                "2?? ???? ?????.\n"
                "3?? ??? ?????? ???? ?????? ???????? ????? ??????? ?? SNOW LIWA.\n"
                "4?? ????? ????? ???? ??? ???????? ?? ??? ????? ??????? ??????? ??????? ?????? ????? ??"
            )
            st.markdown('<div class="center-btn">', unsafe_allow_html=True)
            st.link_button("Pay with Ziina", redirect_url, use_container_width=False)
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.error(
                "?? ????? ?? ????? ???? ????? ?? Ziina. ????? ????? "
                "?????? ??????? ???? ?????? ????? ????? ??????."
            )

        st.markdown(
            '<div class="footer-note">*???? ?????? ????? ?????? ???? ???????? Webhooks ?? ????? ?????*</div>',
            unsafe_allow_html=True,
        )

def render_who_we_are(config: dict):
    language_mode = config.get("typography", {}).get("language_mode", "bilingual")
    st.markdown('<div class="snow-title">SNOW LIWA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subheading">من نحن ؟ · Who are we</div>',
        unsafe_allow_html=True,
    )

    ar_text = """
مشروع شبابي إماراتي من قلب منطقة الظفرة ، 

يقدم تجربة شتوية فريدة تجمع بين أجواء ليوا الساحرة ولمسات من البساطة والجمال . 

يهدف المشروع إلى خلق مساحة ترفيهية ودية للعائلات والشباب تجمع بين الديكور الشتوي الفخم والضيافة الراقية من مشروب الشوكولاتة الساخنة الي نافورة الشوكولاتة والفراولة الطازجة نحن نعمل على تطوير باستمرار بدعم من الجهات المحلية وروح الشباب الإماراتي الطموح .
"""

    en_title = "? Who are we"
    en_text = """
Emirati youth project from the heart of Al Dhafra region,

It offers a unique winter experience that combines the charming atmosphere of Liwa
with touches of simplicity and beauty.

The project aims to create a friendly entertainment space for families and young people
that combines luxurious winter decoration and high-end hospitality from hot chocolate
drink to the fresh chocolate and strawberry fountain. We are constantly developing
with the support of local authorities and the spirit of ambitious Emirati youth.
"""

    st.markdown('<div class="dual-column">', unsafe_allow_html=True)
    if language_mode != "english":
        st.markdown(
            f'<div class="arabic"><strong>من نحن ؟</strong><br><br>{ar_text}</div>',
            unsafe_allow_html=True,
        )
    if language_mode != "arabic":
        st.markdown(
            f'<div class="english"><strong>{en_title}</strong><br><br>{en_text}</div>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_experience(config: dict):
    st.markdown('<div class="snow-title">SNOW LIWA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subheading">Snow Experience · تجربة الثلج</div>',
        unsafe_allow_html=True,
    )

    ar_block_1 = """
تجربة الثلج ❄️ 

في مبادرةٍ فريدةٍ تمنح الزوّار أجواءً ثلجية ممتعة وتجربةً استثنائية لا تُنسى، يمكنكم الاستمتاع بمشاهدة تساقط الثلج، وتجربة مشروب الشوكولاتة الساخنة، مع ضيافةٍ راقية تشمل الفراولة ونافورة الشوكولاتة.

تذكرة الدخول فقط بـ 175 درهمًا 
"""

    en_block_1 = """
In a unique initiative that gives visitors a pleasant snowy
atmosphere and an exceptional and unforgettable experience,
you can enjoy watching the snowfall, and try a hot chocolate
drink, with high-end hospitality including strawberries and a
chocolate fountain.

The entrance ticket is only AED 175
"""

    ar_block_2 = """
SNOW Liwa

بعد الدفع عن طريق تصوير الباركود تواصلو معانا واستلمو تذكرتكم ولوكيشن موقعنا السري 🫣
"""

    en_block_2 = """
SNOW Liwa

After paying by photographing the barcode, contact us and receive
your ticket and the location of our secret website 🫣
"""

    language_mode = config.get("typography", {}).get("language_mode", "bilingual")
    st.markdown('<div class="dual-column">', unsafe_allow_html=True)
    if language_mode != "english":
        st.markdown(
            f'<div class="arabic">{ar_block_1}<br><br>{ar_block_2}</div>',
            unsafe_allow_html=True,
        )
    if language_mode != "arabic":
        st.markdown(
            f'<div class="english">{en_block_1}<br><br>{en_block_2}</div>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        f'<div class="ticket-price">🎟️ Entrance Ticket: <strong>{TICKET_PRICE} AED</strong> per person</div>',
        unsafe_allow_html=True,
    )


def render_contact(config: dict):
    st.markdown('<div class="snow-title">SNOW LIWA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subheading">Contact · تواصل معنا</div>',
        unsafe_allow_html=True,
    )

    st.markdown("### 📞 Contact Us / تواصل معنا")

    ar_contact = """
**رقم الهاتف**

050 113 8781

للتواصل عبر الواتساب فقط أو من خلال حسابنا في الإنستغرام:
**snowliwa**
"""

    en_contact = """
**Phone**

050 113 8781

To contact WhatsApp only or on our Instagram account:

**snowliwa**
"""

    language_mode = config.get("typography", {}).get("language_mode", "bilingual")
    st.markdown('<div class="dual-column">', unsafe_allow_html=True)
    if language_mode != "english":
        st.markdown(f'<div class="arabic">{ar_contact}</div>', unsafe_allow_html=True)
    if language_mode != "arabic":
        st.markdown(f'<div class="english">{en_contact}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.write("You can later add direct WhatsApp links or Instagram buttons here.")


def render_dashboard():
    st.markdown('<div class="snow-title">SNOW LIWA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subheading">Dashboard · لوحة التحكم</div>',
        unsafe_allow_html=True,
    )

    df = load_bookings()
    if df.empty:
        st.info("No bookings yet.")
        return

    # Sync from Ziina
    if st.button("🔄 Sync payment status from Ziina"):
        with st.spinner("Syncing with Ziina..."):
            df = sync_payments_from_ziina(df)
        st.success("Sync completed.")

    # KPIs
    total_bookings = len(df)
    total_tickets = df["tickets"].sum()
    total_amount = df["total_amount"].sum()
    total_paid = df[df["status"] == "paid"]["total_amount"].sum()
    total_pending = df[df["status"] == "pending"]["total_amount"].sum()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total bookings", int(total_bookings))
    c2.metric("Total tickets", int(total_tickets))
    c3.metric("Total amount (AED)", f"{total_amount:,.0f}")
    c4.metric("Paid (AED)", f"{total_paid:,.0f}")
    c5.metric("Pending (AED)", f"{total_pending:,.0f}")

    st.markdown("### Update booking status manually")
    booking_ids = df["booking_id"].tolist()
    selected_id = st.selectbox("Select booking", booking_ids)
    new_status = st.selectbox("New status", ["pending", "paid", "cancelled"])
    if st.button("Save status"):
        df.loc[df["booking_id"] == selected_id, "status"] = new_status
        save_bookings(df)
        st.success(f"Updated {selected_id} to status: {new_status}")

    st.markdown("### Last 25 bookings")
    st.dataframe(
        df.sort_values("created_at", ascending=False).head(25),
        use_container_width=True,
    )


def render_payment_result(result: str, pi_id: str):
    """Page shown when user returns from Ziina with pi_id in URL."""
    st.markdown('<div class="snow-title">SNOW LIWA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subheading">Payment result · نتيجة الدفع</div>',
        unsafe_allow_html=True,
    )

    st.write(f"**Payment Intent ID:** `{pi_id}`")

    df = load_bookings()
    row = df[df["payment_intent_id"].astype(str) == str(pi_id)]
    booking_id = row["booking_id"].iloc[0] if not row.empty else None
    if booking_id:
        st.write(f"**Booking ID:** `{booking_id}`")

    pi_status = None
    if pi_id:
        pi = get_payment_intent(pi_id)
        if pi:
            pi_status = pi.get("status")
            if not row.empty:
                idx = row.index[0]
                df.at[idx, "payment_status"] = pi_status
                if pi_status == "completed":
                    df.at[idx, "status"] = "paid"
                elif pi_status in ("failed", "canceled"):
                    df.at[idx, "status"] = "cancelled"
                save_bookings(df)

    final_status = pi_status or result

    if final_status == "completed":
        st.success(
            "✅ تم الدفع بنجاح!\n\n"
            "شكرًا لاختياركم **SNOW LIWA** ❄️\n\n"
            "تواصلوا معنا عبر الواتساب مع رقم الحجز لاستلام التذكرة ولوكيشن الموقع."
        )
    elif final_status in ("pending", "requires_payment_instrument", "requires_user_action"):
        st.info(
            "ℹ️ عملية الدفع قيد المعالجة أو لم تكتمل بعد.\n\n"
            "لو تأكدت أن المبلغ تم خصمه، أرسل لنا رقم الحجز لنراجع الحالة."
        )
    elif final_status in ("failed", "canceled"):
        st.error(
            "❌ لم تتم عملية الدفع أو تم إلغاؤها.\n\n"
            "يمكنك إعادة المحاولة من صفحة الحجز أو التواصل معنا للمساعدة."
        )
    else:
        st.warning(
            "تعذر التأكد من حالة الدفع.\n\n"
            "يرجى التواصل معنا على الواتساب مع رقم الحجز للتحقق من العملية."
        )

    st.markdown("---")
    st.markdown(
        "📱 للتواصل: واتساب أو إنستغرام **snowliwa** مع ذكر رقم الحجز.",
    )

    st.markdown('<div class="center-btn">', unsafe_allow_html=True)
    st.link_button("Back to SNOW LIWA home", APP_BASE_URL, use_container_width=False)
    st.markdown("</div>", unsafe_allow_html=True)


# =========================
# MAIN APP
# =========================


def main():
    init_state()
    ensure_data_file()
    ensure_ui_config()
    ui_config = load_ui_config()
    set_background(ui_config)
    inject_base_css(ui_config)
    ui_config = render_settings_panel(ui_config)

    query = get_query_params()
    result_param = _normalize_query_value(query.get("result")) if query else None
    pi_id_param = _normalize_query_value(query.get("pi_id")) if query else None

    # If coming back from Ziina with pi_id -> show payment result
    if result_param and pi_id_param:
        st.markdown(
            '<div class="page-container"><div class="page-card">',
            unsafe_allow_html=True,
        )
        render_payment_result(result_param, pi_id_param)
        st.markdown("</div></div>", unsafe_allow_html=True)
        return

    # Normal navigation
    st.markdown(
        '<div class="page-container"><div class="page-card">',
        unsafe_allow_html=True,
    )

    render_welcome(ui_config)
    st.markdown("<hr>", unsafe_allow_html=True)
    render_who_we_are(ui_config)
    st.markdown("<hr>", unsafe_allow_html=True)
    render_experience(ui_config)
    st.markdown("<hr>", unsafe_allow_html=True)
    render_contact(ui_config)
    st.markdown("<hr>", unsafe_allow_html=True)
    # Dashboard is now a separate page (dashboard.py)

    st.markdown("</div></div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
