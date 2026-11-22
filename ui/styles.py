"""
ui/styles.py — Custom CSS and Dark Fantasy CRPG styling for Streamlit.
Designed in a Baldur's Gate 3 × Disco Elysium × Holy Grail War split-screen cockpit aesthetic.
100% offline-safe system font stacks and zero markdown indentation artifacts.
"""

import streamlit as st


def apply_custom_css():
    """Inject Obsidian & Antique Gold Dark Fantasy CRPG custom CSS styling."""
    css = """
<style>
:root {
    --hgw-bg-deep: #090b10;
    --hgw-bg-panel: #111520;
    --hgw-bg-card: #161b29;
    --hgw-gold: #d4af6a;
    --hgw-gold-bright: #f3d08a;
    --hgw-gold-dim: rgba(212, 175, 106, 0.28);
    --hgw-gold-glow: rgba(212, 175, 106, 0.16);
    --hgw-text-main: #f1f5f9;
    --hgw-text-muted: #94a3b8;
    --hgw-green: #4ade80;
    --hgw-red: #f87171;
    --hgw-cyan: #38bdf8;
}

html, body, [class*="css"] {
    font-family: 'Prompt', 'Leelawadee UI', 'Segoe UI', 'Tahoma', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

.stApp {
    background: radial-gradient(circle at 50% 0%, #171d2e 0%, #090b10 75%) !important;
    color: var(--hgw-text-main);
}

/* Maximize usable screen space for split-screen CRPG cockpit */
.block-container {
    padding-top: 2.2rem !important;
    padding-bottom: 1.5rem !important;
    padding-left: 1.8rem !important;
    padding-right: 1.8rem !important;
    max-width: 1600px !important;
}

h1, h2, h3 {
    font-family: 'Cinzel', 'Palatino Linotype', 'Book Antiqua', 'Georgia', 'Prompt', serif !important;
    letter-spacing: 0.02em;
    color: #f8fafc;
}

/* Tabs Styling — Sleek Obsidian & Gold Pill Bar */
.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    background-color: rgba(15, 18, 28, 0.95);
    padding: 5px;
    border-radius: 10px;
    border: 1px solid var(--hgw-gold-dim);
}
.stTabs [data-baseweb="tab"] {
    border-radius: 7px;
    padding: 7px 14px;
    color: #94a3b8;
    font-weight: 500;
    font-size: 0.9rem;
    transition: all 0.18s ease;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #f3d08a;
    background-color: rgba(212, 175, 106, 0.1);
}
.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, rgba(212, 175, 106, 0.25) 0%, rgba(212, 175, 106, 0.08) 100%) !important;
    color: #f3d08a !important;
    font-weight: 600 !important;
    border-bottom: 2px solid var(--hgw-gold) !important;
}

/* Primary buttons — Antique Gold Royale */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #9a6f28 0%, #c69a4b 50%, #d4af6a 100%);
    border: 1px solid #f3d08a;
    color: #090b10 !important;
    font-weight: 700;
    border-radius: 8px;
    box-shadow: 0 3px 12px rgba(212, 175, 106, 0.25);
    transition: all 0.18s ease;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #b58534 0%, #dbb060 50%, #f3d08a 100%);
    box-shadow: 0 5px 18px rgba(212, 175, 106, 0.42);
    transform: translateY(-1px);
}

/* Secondary buttons — Multi-line support, never truncate choice text */
.stButton > button[kind="secondary"] {
    border-radius: 8px;
    border: 1px solid rgba(212, 175, 106, 0.28);
    background: linear-gradient(180deg, rgba(22, 27, 42, 0.94) 0%, rgba(14, 17, 27, 0.98) 100%);
    color: #e2e8f0;
    font-weight: 500;
    height: auto !important;
    min-height: 2.5rem;
    padding: 8px 14px !important;
    white-space: normal !important;
    word-wrap: break-word !important;
    text-align: left !important;
    line-height: 1.45 !important;
    transition: all 0.18s ease;
}
.stButton > button[kind="secondary"]:hover {
    border-color: var(--hgw-gold);
    color: #f3d08a;
    background: linear-gradient(180deg, rgba(31, 38, 58, 0.96) 0%, rgba(20, 25, 39, 0.99) 100%);
    box-shadow: 0 0 12px rgba(212, 175, 106, 0.22);
}

/* Chat Message Containers */
div[data-testid="stChatMessage"] {
    background: linear-gradient(180deg, rgba(19, 23, 35, 0.92) 0%, rgba(12, 15, 24, 0.96) 100%);
    border: 1px solid rgba(212, 175, 106, 0.18);
    border-left: 3px solid var(--hgw-gold);
    border-radius: 10px;
    padding: 12px 16px;
    margin-bottom: 10px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
    line-height: 1.65;
}

/* Compact Top CRPG HUD Bar */
.crpg-hud-bar {
    background: linear-gradient(180deg, #141927 0%, #0c0f18 100%);
    border: 1px solid var(--hgw-gold);
    border-radius: 11px;
    padding: 10px 16px;
    margin-bottom: 8px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
}
.crpg-hud-top {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
}
.crpg-hero-name {
    font-family: 'Cinzel', 'Prompt', serif;
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--hgw-gold-bright);
}
.crpg-hero-sub {
    color: #94a3b8;
    font-size: 0.86rem;
    margin-left: 6px;
}
.crpg-vitals-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
}
.crpg-vital-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: rgba(9, 11, 16, 0.78);
    border: 1px solid rgba(255, 255, 255, 0.1);
    padding: 4px 10px;
    border-radius: 7px;
    font-size: 0.83rem;
    font-weight: 600;
}
.hgw-badge-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: rgba(212, 175, 106, 0.13);
    border: 1px solid rgba(212, 175, 106, 0.38);
    color: #f3d08a;
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 500;
}

/* Scene Stage Card (Left Column Header) */
.crpg-scene-card {
    background: linear-gradient(180deg, #141927 0%, #0b0e17 100%);
    border: 1px solid var(--hgw-gold);
    border-radius: 11px;
    overflow: hidden;
    margin-bottom: 10px;
    box-shadow: 0 6px 22px rgba(0, 0, 0, 0.5);
}
.crpg-scene-art {
    width: 100%;
    height: 78px;
    border-bottom: 1px solid rgba(212, 175, 106, 0.28);
    position: relative;
}
.crpg-scene-content {
    padding: 10px 14px;
}
.crpg-scene-title-row {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
}
.crpg-scene-title {
    font-family: 'Cinzel', 'Prompt', serif;
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--hgw-gold-bright);
    margin: 0;
}
.crpg-scene-desc {
    color: #cbd5e1;
    font-size: 0.88rem;
    line-height: 1.5;
    margin: 0;
}

/* Full-Width Stacked Choice Cards (Right Column) */
.crpg-choice-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: rgba(212, 175, 106, 0.08);
    border: 1px solid rgba(212, 175, 106, 0.28);
    border-left: 3px solid var(--hgw-gold);
    border-radius: 7px 7px 0 0;
    padding: 5px 11px;
    margin-top: 6px;
    margin-bottom: -4px;
    font-size: 0.78rem;
}
.crpg-choice-cat {
    font-weight: 700;
    color: var(--hgw-gold-bright);
}
.crpg-choice-meta {
    color: #94a3b8;
    font-size: 0.76rem;
}
.crpg-choice-odds {
    padding: 1px 8px;
    border-radius: 99px;
    font-size: 0.74rem;
    font-weight: 700;
}

/* Stat Delta Pills */
.hgw-delta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
    padding-top: 6px;
    border-top: 1px dashed rgba(212, 175, 106, 0.24);
}
.hgw-delta-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 5px;
    font-size: 0.76rem;
    font-weight: 600;
}
.hgw-delta-pos {
    background: rgba(74, 222, 128, 0.14);
    border: 1px solid rgba(74, 222, 128, 0.4);
    color: #4ade80;
}
.hgw-delta-neg {
    background: rgba(248, 113, 113, 0.14);
    border: 1px solid rgba(248, 113, 113, 0.4);
    color: #f87171;
}
.hgw-delta-neu {
    background: rgba(212, 175, 106, 0.14);
    border: 1px solid rgba(212, 175, 106, 0.4);
    color: #f3d08a;
}

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0e111a 0%, #090b10 100%) !important;
    border-right: 1px solid rgba(212, 175, 106, 0.22);
}
</style>
"""
    st.markdown(css, unsafe_allow_html=True)
