import json
import re
import ollama
from typing import Dict, Any, Tuple

def generate_dm_response(state: Dict[str, Any], lore: list, history: list, user_input: str) -> Tuple[str, Dict[str, Any]]:
    """
    Generate a response from the DM and parse any state changes.
    Returns a tuple of (narrative_text, state_updates_dict).
    """
    
    # Format Lore
    lore_text = "None"
    if lore:
        lore_text = "\n".join([f"- {l}" for l in lore])
        
    # Format State
    state_text = json.dumps(state, indent=2)
    
    # Format History
    history_text = ""
    for turn in history:
        role = "Player" if turn["role"] == "user" else "DM"
        history_text += f"{role}: {turn['content']}\n"
        
    system_prompt = f"""You are the Dungeon Master (DM) for an immersive text-based RPG.
You are running the game for the player. Be descriptive, engaging, and react to their choices.
You must manage the game state. If the player takes damage, heals, levels up, gains/loses an item, gets a new quest, or changes location, you must output a JSON block to update the state.

CURRENT PLAYER STATE:
{state_text}

RELEVANT LORE / PAST EVENTS:
{lore_text}

INSTRUCTIONS FOR OUTPUT:
You MUST format your response in two parts:
1. The narrative text of what happens next.
2. A JSON block enclosed in ```json and ``` at the very end of your response, detailing state changes. 
If there are no state changes, output an empty JSON object {{}}.

Valid JSON keys for state changes:
- "hp_change": (int) e.g., -10 for damage, 20 for healing.
- "level_change": (int) e.g., 1 for leveling up.
- "add_item": (string) name of the item to add to inventory.
- "remove_item": (string) name of the item to remove.
- "add_quest": (string) new quest description.
- "remove_quest": (string) quest to remove.
- "location": (string) new location if the player moves.

Example Output:
You swing your sword at the goblin, striking it down! However, it manages to scratch you before falling. You search its body and find a rusty key.
```json
{{
  "hp_change": -5,
  "add_item": "Rusty Key"
}}
```
"""

    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # We pass the history as normal conversation
    for turn in history:
        messages.append(turn)
        
    # Add the current user input
    messages.append({"role": "user", "content": user_input})
    
    try:
        response = ollama.chat(model="llama3", messages=messages)
        full_text = response.get("message", {}).get("content", "")
        
        # Parse out the JSON block
        narrative, state_updates = parse_llm_output(full_text)
        return narrative, state_updates
        
    except Exception as e:
        print(f"Error calling Ollama: {e}")
        return "The DM is currently pondering the next move (Error connecting to Ollama).", {}

def parse_llm_output(full_text: str) -> Tuple[str, Dict[str, Any]]:
    """Extracts narrative and JSON state updates from the LLM output."""
    json_pattern = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
    match = json_pattern.search(full_text)
    
    state_updates = {}
    if match:
        json_str = match.group(1)
        try:
            state_updates = json.loads(json_str)
        except json.JSONDecodeError:
            print(f"Warning: Failed to parse JSON from LLM: {json_str}")
        
        # Remove the JSON block from the narrative text
        narrative = json_pattern.sub("", full_text).strip()
    else:
        # If no JSON block is found, the whole text is narrative
        narrative = full_text.strip()
        
    return narrative, state_updates
