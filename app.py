import streamlit as st
import state_manager
import memory_manager
import llm_handler

# Must be the first Streamlit command
st.set_page_config(page_title="AI Dungeon Master", page_icon="🐉", layout="wide")

def init_session_state():
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    
    if "short_term_memory" not in st.session_state:
        st.session_state.short_term_memory = []
        
    if "player_state" not in st.session_state:
        st.session_state.player_state = state_manager.load_state()

def render_sidebar():
    st.sidebar.title("Player Character")
    state = st.session_state.player_state
    
    if not state:
        st.sidebar.warning("No player state found. Please check player_state.json.")
        return

    st.sidebar.markdown(f"**Name:** {state.get('name', 'Unknown')}")
    st.sidebar.markdown(f"**Class:** {state.get('class_name', 'Unknown')}")
    st.sidebar.markdown(f"**Level:** {state.get('level', 1)}")
    
    # HP Bar simulation
    hp = state.get('hp', 100)
    st.sidebar.progress(min(hp / 100.0, 1.0), text=f"HP: {hp}")
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("Location")
    st.sidebar.write(state.get('location', 'Unknown'))
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("Inventory")
    for item in state.get('inventory', []):
        st.sidebar.write(f"- {item}")
        
    st.sidebar.markdown("---")
    st.sidebar.subheader("Active Quests")
    for quest in state.get('active_quests', []):
        st.sidebar.write(f"- {quest}")

def manage_memory():
    """Handles the sliding window and summarization of short term memory."""
    if len(st.session_state.short_term_memory) > 6:
        # Extract the oldest 2 turns (one user, one DM usually, but just take first 2)
        oldest_turns = st.session_state.short_term_memory[:2]
        
        with st.spinner("DM is taking notes (Summarizing memory)..."):
            memory_manager.summarize_and_store(oldest_turns)
            
        # Remove them from short term memory
        st.session_state.short_term_memory = st.session_state.short_term_memory[2:]

def main():
    init_session_state()
    render_sidebar()
    
    st.title("AI Dungeon Master 🐉")
    
    # Display chat history (all of it for UI purposes)
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            
    # Chat Input
    if user_input := st.chat_input("What do you do?"):
        # Display user message
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        st.session_state.short_term_memory.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)
            
        # Fetch relevant lore
        lore = memory_manager.get_relevant_lore(user_input, top_k=3)
        
        # Display DM thinking
        with st.chat_message("assistant"):
            with st.spinner("The DM is pondering..."):
                narrative, state_updates = llm_handler.generate_dm_response(
                    state=st.session_state.player_state,
                    lore=lore,
                    history=st.session_state.short_term_memory[:-1], # pass all but the current input
                    user_input=user_input
                )
                
            st.markdown(narrative)
            
            # Update state if necessary
            if state_updates:
                st.session_state.player_state = state_manager.apply_state_updates(state_updates)
                st.success(f"State Updated: {state_updates}")
                st.rerun() # Rerun to update sidebar immediately
                
        # Save assistant message to histories
        st.session_state.chat_history.append({"role": "assistant", "content": narrative})
        st.session_state.short_term_memory.append({"role": "assistant", "content": narrative})
        
        # Check memory capacity
        manage_memory()

if __name__ == "__main__":
    main()
