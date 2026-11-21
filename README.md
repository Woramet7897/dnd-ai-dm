# D&D AI Dungeon Master Engine 🐉🎲

An offline & cloud-ready, rule-enforced, narrative-rich D&D 5e AI Dungeon Master powered by **Groq Cloud (LPU)**, **Google Gemini Flash**, or **Local Ollama**, built with Streamlit.

## Architecture Highlights
- **100% Rule Integrity**: Python controls all math, dice rolls (`d20`, damage dice), character stats, DC checks, combat rounds, conditions, and leveling.
- **Multi-Engine AI Narrative & Roleplay**: Cloud LLMs (**Groq LPU** / **Google Gemini Flash**) or Local LLM (**Ollama**) generate rich Thai narrative without hallucinating numerical results. Works out-of-the-box via Cloud API keys even without Ollama installed locally.
- **Modular Domain Architecture**: Cleanly separated into focused packages (`ui/`, `state/`, `combat/`, `llm/`, `dungeon/`, `validators/`) with backward-compatible facade modules.
- **Persistent State**: Full save/load system with atomic writes and rolling backups.
- **Offline RAG Memory**: Vector memory (ChromaDB) stores major milestones and minor events for long-term consistency.

## Project Structure
```text
├── app.py                     # Streamlit entrypoint & routing facade
├── ui/                        # 🎨 Streamlit UI views & components
│   ├── styles.py              # Dark Fantasy RPG custom CSS
│   ├── session.py             # Session state, save/load & HUD helpers
│   ├── landing_view.py        # Character creation (27-pt buy) & save loader
│   ├── sidebar.py             # Character vitals & AI Engine settings (Groq/Gemini/Ollama)
│   ├── dialogs.py             # Modal dialogs (Character, Inventory, Quests, Spells, Camp)
│   ├── combat_view.py         # Turn-based combat HUD & actions
│   ├── exploration_view.py    # Compass navigation, BG3 skill checks, & DM chat
│   └── town_view.py           # Town shops & Tavern Notice Board UI
├── state_manager.py           # Facade for character/world state management
├── state/                     # 🛡️ Modular state & D&D 5e mechanics
│   ├── currency.py            # Tri-denomination currency (GP/SP/CP) & pricing
│   ├── persistence.py         # Atomic JSON save/load & rolling backup rotation
│   ├── equipment.py           # AC calculation, equip/unequip, curses, consumables, shops
│   ├── checks.py              # Inspiration, ability checks, death saves, concentration
│   ├── progression.py         # XP, leveling up, short/long rest, downed outcomes
│   ├── magic.py               # Spell learning, spell attack/save, spellcasting
│   ├── cooking.py             # Food spoilage & camp cooking
│   ├── crime.py               # Pickpocketing, guard persuasion, jail, prison break
│   └── companions.py          # Party recruitment, dismissal, approval, camp dialogue
├── combat_manager.py          # Facade for turn-based D&D 5e combat engine
├── combat/                    # ⚔️ Modular combat engine
│   ├── conditions.py          # Combat conditions, surfaces & elemental combos
│   ├── actions.py             # Attack resolution, Shove, & Weapon Actions
│   └── engine.py              # Initiative, AI turns, & round orchestration
├── llm_handler.py             # Facade for LLM routing & prompt orchestration
├── llm/                       # 🧠 Modular LLM providers & prompts
│   ├── providers.py           # Groq Cloud, Google Gemini, & Local Ollama clients
│   ├── prompts.py             # Tiered System Prompt (Tiers 0-4) & token budget
│   └── generator.py           # Narrative generation, extraction & camp dialogue
├── dungeon_manager.py         # Facade for dungeon graph & world exploration
├── dungeon/                   # 🗺️ Modular world & room management
│   ├── navigation.py          # Room movement, time progression, loot & safe room BFS
│   ├── events.py              # Lightweight World Events & interactive objects
│   └── notice_board.py        # Tavern Notice Board quest generator
├── validation.py              # Facade for whitelist sanitization of LLM output
├── validators/                # ✅ Modular LLM output validators
│   ├── entities.py            # Sanitization & generated Monster/NPC/Item/Spell bounds
│   └── extraction.py          # Field-level whitelist validation for turn extraction
├── character_creator.py       # Character creation, classes, races, & stats
├── check_manager.py           # BG3-style interactive skill check detector
├── memory_manager.py          # ChromaDB episodic RAG memory manager
├── rules.py                   # Pure D&D rules, catalogs, & math base layer
├── paths.py                   # Central directory & file path definitions
├── data/catalogs/             # Static D&D catalogs (items, monsters, spells, shops)
└── tests/                     # 27 automated test suites (608 individual checks)
```

## Quick Start
1. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the game:
   ```bash
   streamlit run app.py
   ```
3. **Configure AI Engine (No Local Ollama Required)**:
   - Open the left sidebar **🤖 Dungeon Master AI (Engine & Models)**.
   - Choose **🚀 Groq Cloud** (get a free key at [console.groq.com/keys](https://console.groq.com/keys)) or **⚡ Google Gemini** (get a free key at [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)).
   - Paste your API key and click **💾 บันทึก Key**.
