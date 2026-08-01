from pydantic_ai import Agent
from pydantic import BaseModel
from typing import List
from sqlmodel import Session, select, delete
from database import engine
from models.chunk import Chunk
from models.knowledge import DocumentTopic

class ExtractedTopics(BaseModel):
    topics: List[str]

topic_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=ExtractedTopics,
    retries=3
)

def get_grouped_chunks(document_id: int, group_size: int = 3, max_groups: int = 20) -> List[str]:
    """Get grouped chunks with better context window management"""
    with Session(engine) as session:
        chunks = session.exec(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
            .limit(group_size * max_groups)
        ).all()
        
    grouped = []
    for i in range(0, len(chunks), group_size):
        group = chunks[i:i+group_size]
        combined = " ".join([c.content for c in group])
        grouped.append(combined)
        
    return grouped

def extract_topics(document_id: int, target_topic_count: int = 20) -> List[str]:
    """Extract 15-20 unique topics from document"""
    chunks = get_grouped_chunks(document_id, group_size=3, max_groups=20)
    
    # Use 15 groups for context
    chunks_context = "\n---\n".join(chunks[:15])
    
    prompt = f"""
    From these textbook chunks, extract {target_topic_count} major topics.
    Focus on SUBTOPICS, not just high-level categories.
    
    For example, if document is about "Unix Filters", extract:
    - Unix Filters: Basic Concepts
    - Using Filters in Pipelines
    - Regular Expressions Fundamentals
    - grep: Pattern Searching
    - sed: Stream Editing
    - awk: Text Processing
    
    Requirements:
    1. Extract 15-20 specific, granular topics
    2. NO DUPLICATES
    3. Include both foundational and advanced topics
    4. Cover different subtopics within the main subject
    
    Chunks: {chunks_context}
    
    Return ONLY unique topics as a structured list.
    """
    
    result = topic_agent.run_sync(prompt)
    
    # Deduplicate (case-insensitive)
    seen = set()
    unique_topics = []
    for topic in result.output.topics:
        topic_lower = topic.lower()
        if topic_lower not in seen:
            seen.add(topic_lower)
            unique_topics.append(topic)
            
    # Save to database
    with Session(engine) as session:
        # Clear existing topics for this document (if regenerating)
        session.exec(
            delete(DocumentTopic).where(DocumentTopic.document_id == document_id)
        )
        
        for idx, topic_name in enumerate(unique_topics[:target_topic_count]):
            dt = DocumentTopic(
                document_id=document_id,
                topic_name=topic_name,
                topic_order=idx
            )
            session.add(dt)
        session.commit()
        
    return unique_topics[:target_topic_count]
