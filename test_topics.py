import asyncio
from rag.agents.topic_extractor import extract_topics
import os
from core.config import settings

os.environ["GROQ_API_KEY"] = settings.GROQ_API_KEY

def test():
    print("Testing Topic Extractor...")
    try:
        # Use document_id=1, which was already created by the upload route
        topics = extract_topics(1, target_topic_count=10)
        print(f"Extracted {len(topics)} topics:")
        for t in topics:
            print(f"- {t}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
