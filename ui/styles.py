"""
ui/styles.py — Custom CSS and Dark Fantasy RPG styling for Streamlit.
Fuses the Obsidian & Antique Champagne Gold aesthetic of 'The Night the Grail Fills'
with the clear visual status bars, scene stage, and transparent choice cards of '1 ใน 65 ล้าน'.
"""

import streamlit as st


def apply_custom_css():
    """Inject Obsidian & Antique Gold Dark Fantasy RPG custom CSS styling (100% offline-safe system font stacks)."""
    st.markdown("""
    <style>
    :root {
        --hgw-bg-deep: #0a0b10;
        --hgw-bg-panel: #11141e;
        --hgw-bg-card: #161a27;
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
        background: radial-gradient(circle at 50% 0%, #161b29 0%, #0a0b10 75%) !important;
        color: var(--hgw-text-main);
    }

    h1, h2, h3 {
        font-family: 'Cinzel', 'Palatino Linotype', 'Book Antiqua', 'Georgia', 'Prompt', serif !important;
        letter-spacing: 0.03em;
        color: #f8fafc;
    }

    /* Tabs Styling — Obsidian & Gold */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: rgba(17, 20, 30, 0.92);
        padding: 6px;
        border-radius: 10px;
        border: 1px solid var(--hgw-gold-dim);
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 7px;
        padding: 8px 18px;
        color: #94a3b8;
        font-weight: 500;
        transition: all 0.2s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #f3d08a;
        background-color: rgba(212, 175, 106, 0.08);
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, rgba(212, 175, 106, 0.24) 0%, rgba(212, 175, 106, 0.08) 100%) !important;
        color: #f3d08a !important;
        font-weight: 600 !important;
        border-bottom: 2px solid var(--hgw-gold) !important;
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, rgba(22, 26, 39, 0.95) 0%, rgba(14, 17, 26, 0.95) 100%);
        border: 1px solid var(--hgw-gold-dim);
        border-radius: 10px;
        padding: 10px 14px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45);
    }

    /* Primary buttons — Antique Gold & Crimson Royale */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #9a6f28 0%, #c69a4b 50%, #d4af6a 100%);
        border: 1px solid #f3d08a;
        color: #0a0b10 !important;
        font-weight: 700;
        border-radius: 8px;
        box-shadow: 0 4px 14px rgba(212, 175, 106, 0.28);
        transition: all 0.2s ease;
    }
    .stButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #b58534 0%, #dbb060 50%, #f3d08a 100%);
        box-shadow: 0 6px 20px rgba(212, 175, 106, 0.45);
        transform: translateY(-1px);
    }

    /* Secondary buttons — Chamfered Obsidian with Gold Hover */
    .stButton > button[kind="secondary"] {
        border-radius: 8px;
        border: 1px solid rgba(212, 175, 106, 0.25);
        background: linear-gradient(180deg, rgba(22, 26, 39, 0.92) 0%, rgba(15, 18, 28, 0.96) 100%);
        color: #e2e8f0;
        font-weight: 500;
        transition: all 0.18s ease;
    }
    .stButton > button[kind="secondary"]:hover {
        border-color: var(--hgw-gold);
        color: #f3d08a;
        background: linear-gradient(180deg, rgba(31, 37, 56, 0.95) 0%, rgba(20, 24, 38, 0.98) 100%);
        box-shadow: 0 0 12px rgba(212, 175, 106, 0.22);
        transform: translateY(-1px);
    }

    /* Chat Message Containers */
    div[data-testid="stChatMessage"] {
        background: linear-gradient(180deg, rgba(19, 23, 35, 0.88) 0%, rgba(13, 16, 25, 0.92) 100%);
        border: 1px solid rgba(212, 175, 106, 0.16);
        border-left: 3px solid var(--hgw-gold);
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 10px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    }

    /* ── 1 in 65 Million × Holy Grail War Top Status Banner ───────────────── */
    .hgw-status-banner {
        background: linear-gradient(180deg, #131724 0%, #0c0e17 100%);
        border: 1px solid var(--hgw-gold);
        border-radius: 12px;
        padding: 14px 18px;
        margin-bottom: 14px;
        box-shadow: 0 8px 28px rgba(0, 0, 0, 0.55), inset 0 0 0 1px rgba(212, 175, 106, 0.15);
    }
    .hgw-banner-top {
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
        padding-bottom: 10px;
        margin-bottom: 12px;
        border-bottom: 1px solid rgba(212, 175, 106, 0.22);
    }
    .hgw-char-title {
        font-family: 'Cinzel', 'Prompt', serif;
        font-size: 1.22rem;
        font-weight: 700;
        color: var(--hgw-gold-bright);
        letter-spacing: 0.03em;
    }
    .hgw-badge-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        background: rgba(212, 175, 106, 0.12);
        border: 1px solid rgba(212, 175, 106, 0.35);
        color: #f3d08a;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.82rem;
        font-weight: 500;
    }
    .hgw-stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 12px;
    }
    .hgw-stat-box {
        background: rgba(10, 11, 16, 0.65);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 8px;
        padding: 8px 11px;
    }
    .hgw-stat-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.8rem;
        color: #cbd5e1;
        margin-bottom: 5px;
    }
    .hgw-stat-val {
        font-weight: 700;
        color: #f8fafc;
    }
    .hgw-bar-track {
        width: 100%;
        height: 7px;
        background: rgba(255, 255, 255, 0.08);
        border-radius: 99px;
        overflow: hidden;
    }
    .hgw-bar-fill {
        height: 100%;
        border-radius: 99px;
        transition: width 0.35s ease;
    }

    /* ── Gold-Framed Scene Stage Banner ───────────────────────────────────── */
    .hgw-scene-stage {
        position: relative;
        background: linear-gradient(180deg, #141927 0%, #0b0d14 100%);
        border: 1px solid var(--hgw-gold);
        border-radius: 12px;
        overflow: hidden;
        margin-bottom: 16px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
    }
    .hgw-scene-visual {
        width: 100%;
        height: 125px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-bottom: 1px solid rgba(212, 175, 106, 0.3);
        position: relative;
    }
    .hgw-scene-body {
        padding: 14px 18px;
    }
    .hgw-scene-header {
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        align-items: center;
        gap: 8px;
        margin-bottom: 6px;
    }
    .hgw-scene-name {
        font-family: 'Cinzel', 'Prompt', serif;
        font-size: 1.35rem;
        font-weight: 700;
        color: var(--hgw-gold-bright);
        margin: 0;
    }
    .hgw-scene-desc {
        color: #cbd5e1;
        font-size: 0.95rem;
        line-height: 1.55;
        margin: 0;
    }

    /* ── Featured Event Card & Stat Delta Pills ───────────────────────────── */
    .hgw-featured-event {
        background: linear-gradient(135deg, rgba(23, 27, 42, 0.96) 0%, rgba(13, 15, 23, 0.98) 100%);
        border: 1px solid var(--hgw-gold);
        border-left: 4px solid var(--hgw-gold-bright);
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 12px;
        box-shadow: 0 6px 22px rgba(0, 0, 0, 0.45);
    }
    .hgw-event-eyebrow {
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--hgw-gold);
        margin-bottom: 6px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .hgw-delta-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px dashed rgba(212, 175, 106, 0.25);
    }
    .hgw-delta-pill {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 9px;
        border-radius: 5px;
        font-size: 0.78rem;
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

    /* ── Transparent Choice Preview Cards (1 in 65 Million × BG3) ─────────── */
    .hgw-choice-preview {
        background: linear-gradient(180deg, rgba(20, 24, 37, 0.94) 0%, rgba(13, 16, 25, 0.97) 100%);
        border: 1px solid rgba(212, 175, 106, 0.35);
        border-top: 2px solid var(--hgw-gold);
        border-radius: 9px;
        padding: 10px 12px;
        margin-bottom: 6px;
        min-height: 76px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    .hgw-choice-top {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 6px;
        margin-bottom: 6px;
    }
    .hgw-choice-num {
        background: rgba(212, 175, 106, 0.2);
        border: 1px solid var(--hgw-gold);
        color: var(--hgw-gold-bright);
        font-family: 'Cinzel', serif;
        font-weight: 700;
        font-size: 0.78rem;
        padding: 1px 7px;
        border-radius: 4px;
    }
    .hgw-choice-odds {
        font-size: 0.75rem;
        font-weight: 600;
        padding: 1px 8px;
        border-radius: 99px;
    }
    .hgw-choice-meta {
        font-size: 0.78rem;
        color: #94a3b8;
        margin-top: 4px;
    }
    </style>
    """, unsafe_allow_html=True)
