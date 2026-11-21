"""
ui/styles.py — Custom CSS and Dark Fantasy RPG styling for Streamlit.
"""

import streamlit as st


def apply_custom_css():
    """Inject modern Dark Fantasy RPG custom CSS styling."""
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;800&family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    h1, h2, h3 {
        font-family: 'Cinzel', serif !important;
        letter-spacing: 0.02em;
    }

    /* Tabs Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: rgba(19, 27, 46, 0.7);
        padding: 6px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 18px;
        color: #94a3b8;
        font-weight: 500;
        transition: all 0.2s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #f1f5f9;
        background-color: rgba(255, 255, 255, 0.04);
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, rgba(225, 29, 72, 0.25) 0%, rgba(225, 29, 72, 0.1) 100%) !important;
        color: #ffffff !important;
        font-weight: 600 !important;
        border-bottom: 2px solid #e11d48 !important;
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background: rgba(19, 27, 46, 0.85);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 10px;
        padding: 10px 14px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }

    /* Primary buttons */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #b91c1c 0%, #e11d48 100%);
        border: 1px solid rgba(255, 255, 255, 0.18);
        color: #ffffff;
        font-weight: 600;
        border-radius: 8px;
        box-shadow: 0 4px 14px rgba(225, 29, 72, 0.35);
        transition: all 0.2s ease;
    }
    .stButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #dc2626 0%, #f43f5e 100%);
        box-shadow: 0 6px 18px rgba(225, 29, 72, 0.5);
        transform: translateY(-1px);
    }

    /* Secondary buttons */
    .stButton > button[kind="secondary"] {
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background-color: rgba(19, 27, 46, 0.75);
        transition: all 0.15s ease;
    }
    .stButton > button[kind="secondary"]:hover {
        border-color: rgba(225, 29, 72, 0.45);
        background-color: rgba(30, 41, 59, 0.9);
    }

    /* Chat Message Containers */
    div[data-testid="stChatMessage"] {
        background-color: rgba(19, 27, 46, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    </style>
    """, unsafe_allow_html=True)
