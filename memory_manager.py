import os
import uuid
import chromadb
from sentence_transformers import SentenceTransformer
import ollama

# Initialize ChromaDB client (persistent)
DB_PATH = "./db"
client = chromadb.PersistentClient(path=DB_PATH)

# Get or create a collection for lore
collection = client.get_or_create_collection(name="rpg_lore")

# Initialize Sentence Transformer for embeddings
# It will download the model to the default HuggingFace cache directory if not present
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

def get_relevant_lore(query: str, top_k: int = 3) -> list:
    """Retrieve the top_k most relevant lore entries based on the user's query."""
    if collection.count() == 0:
        return []

    # Generate embedding for the query
    query_embedding = embedding_model.encode(query).tolist()
    
    # Query ChromaDB
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count())
    )
    
    # Extract the documents (lore facts)
    documents = results.get("documents", [[]])[0]
    return documents

def summarize_and_store(turns: list):
    """
    Take the oldest turns from short-term memory, summarize them using Ollama,
    and store the result as a lore entry in ChromaDB.
    `turns` should be a list of dictionaries, e.g. [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    """
    if not turns:
        return

    # Prepare the text to summarize
    text_to_summarize = ""
    for turn in turns:
        role = "Player" if turn["role"] == "user" else "DM"
        text_to_summarize += f"{role}: {turn['content']}\n"
    
    prompt = f"""
    You are an expert summarizer for a fantasy RPG. 
    Summarize the following conversation into a concise lore fact or key event that should be remembered. 
    Focus on what happened, decisions made, or items found. Do not include unnecessary dialogue.
    Keep it to 1-3 sentences.
    
    Conversation:
    {text_to_summarize}
    
    Summary:
    """
    
    try:
        # Call Ollama to summarize
        response = ollama.generate(model="llama3", prompt=prompt)
        summary = response.get("response", "").strip()
        
        if summary:
            # Generate embedding
            summary_embedding = embedding_model.encode(summary).tolist()
            
            # Store in ChromaDB with a unique ID
            doc_id = str(uuid.uuid4())
            collection.add(
                ids=[doc_id],
                embeddings=[summary_embedding],
                documents=[summary]
            )
            print(f"Lore stored: {summary}")
    except Exception as e:
        print(f"Error during summarization or storing: {e}")
