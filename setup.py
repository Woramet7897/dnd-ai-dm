import subprocess
import sys
import shutil

def check_ollama_installed():
    """Check if ollama is installed and available in the system PATH."""
    print("Checking for Ollama installation...")
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        print("Error: Ollama is not installed or not found in the system PATH.")
        print("Please download and install Ollama from https://ollama.com/ before running this setup.")
        sys.exit(1)
    print("Ollama is installed.")

def pull_llama3_model():
    """Run `ollama pull llama3` to fetch the required model."""
    print("Pulling llama3 model (this may take a while depending on your internet speed)...")
    try:
        # We use subprocess.run to stream the output to the console
        result = subprocess.run(["ollama", "pull", "llama3"], check=True)
        print("Successfully pulled llama3.")
    except subprocess.CalledProcessError as e:
        print(f"Error pulling llama3 model: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: Could not execute 'ollama' command.")
        sys.exit(1)

def install_requirements():
    """Install Python packages from requirements.txt."""
    print("Installing requirements from requirements.txt...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
        print("Requirements installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error installing requirements: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("Starting Offline RAG RPG Setup...")
    check_ollama_installed()
    pull_llama3_model()
    install_requirements()
    print("\nSetup complete! You can now run the game with: streamlit run app.py")
