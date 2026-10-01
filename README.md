# D&D AI Dungeon Master Engine 🐉🎲

An offline, rule-enforced, narrative-rich D&D AI Dungeon Master powered by local LLMs (Ollama) and Streamlit.

## Architecture Highlights
- **100% Rule Integrity**: Python controls all math, dice rolls (d20, damage dice), character stats, DC checks, combat rounds, conditions, and leveling.
- **Narrative & Roleplay**: Local LLM (Llama 3 or Typhoon 2.1 Thai) generates contextual narrative without hallucinating numerical results.
- **Persistent State**: Full save/load system with atomic writes and rolling backups.
- **Offline RAG Memory**: Vector memory (ChromaDB) stores major milestones and minor events for long-term consistency.

## Project Structure
```text
├── app.py                     # Streamlit frontend & game loop entrypoint
├── character_creator.py       # Character creation, classes, races, & stats
├── combat_manager.py          # Turn-based D&D 5e combat engine
├── dungeon_manager.py         # Room navigation, dungeon graph, & exploration
├── llm_handler.py             # Tiered prompt construction & Ollama interaction
├── memory_manager.py          # ChromaDB episodic RAG memory manager
├── paths.py                   # Central directory & file path definitions
├── state_manager.py           # Character/world persistence, inventory, & XP
├── validation.py              # Whitelist sanitization of LLM extraction output
├── data/
│   └── catalogs/              # Static D&D catalogs (items, monsters, spells, shops)
├── docs/                      # Specification documents, roadmaps, & design guides
└── saves/                     # Player and world save files
```

## Quick Start
1. Ensure Ollama is installed and running (`ollama serve`).
2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the game:
   - **Default (English / Llama 3)**:
     ```bash
     streamlit run app.py
     ```
   - **Thai Mode (Typhoon 2.1)**:
     ```bash
     $env:OLLAMA_MODEL="scb10x/llama3.1-typhoon2-8b-instruct"
     streamlit run app.py
     ```
