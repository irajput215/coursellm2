import asyncio
from rag.agents.notes_generator import generate_note_for_topic
import os
from core.config import settings

# Ensure API key is set for testing
os.environ["GROQ_API_KEY"] = settings.GROQ_API_KEY

def test():
    # Use document_id=1, which was already created by the upload route
    print("Testing Enhanced Notes Generator...")
    # Test topic that likely has no formulas in PDF to trigger the tool
    note = generate_note_for_topic(1, "grep command")
    
    print(f"Topic: {note.topic}")
    print(f"Summary: {note.summary}")
    print(f"Formulas found: {len(note.formulas)}")
    for f in note.formulas:
        print(f"  - {f.name}: {f.expression}")
        print(f"    {f.description}")
    
    print(f"Examples: {len(note.examples)}")
    for e in note.examples:
        print(f"  Problem: {e.problem}")
        print(f"  Solution: {e.solution}")

if __name__ == "__main__":
    test()
