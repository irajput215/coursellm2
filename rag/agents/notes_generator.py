from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from sqlmodel import Session, select, delete
from database import engine
from models.chunk import Chunk
from models.knowledge import GeneratedNote
from rag.ingestion.embedder import Embedder
import logging
import asyncio
from enum import Enum

logger = logging.getLogger(__name__)

class Formula(BaseModel):
    name: str = Field(description="Name of the formula/concept")
    expression: str = Field(description="The formula expression or syntax")
    description: str = Field(description="Explanation of what this formula/syntax does")
    example_usage: Optional[str] = Field(default=None, description="Example of using this formula/syntax")

class Example(BaseModel):
    problem: str = Field(description="Problem statement")
    solution: str = Field(description="Solution or code")
    explanation: str = Field(description="Step-by-step explanation")

class NoteSchema(BaseModel):
    topic: str = Field(description="Topic name")
    summary: str = Field(description="2-3 sentence summary", min_length=50)
    key_points: List[str] = Field(description="5-8 key points", min_length=5, max_length=8)
    formulas: List[Formula] = Field(default_factory=list, description="0-5 formulas, syntax examples, or code snippets")
    examples: List[Example] = Field(default_factory=list, description="0-3 practical examples with code if applicable")

class EnhancementType(str, Enum):
    FORMULA = "formula"
    SYNTAX = "syntax"
    CODE = "code"
    EXAMPLE = "example"

def search_enhancement(topic: str, enhancement_type: EnhancementType) -> str:
    """Search for relevant formulas, syntax, or code examples for a topic"""
    from pydantic_ai import Agent
    enhance_agent = Agent("groq:llama-3.3-70b-versatile")
    
    prompt = f"""
    For the topic "{topic}", provide a {enhancement_type.value} example.
    
    If {enhancement_type.value} == "formula", provide a mathematical formula.
    If {enhancement_type.value} == "syntax", provide command syntax.
    If {enhancement_type.value} == "code", provide a code example.
    If {enhancement_type.value} == "example", provide a practical example.
    
    Return ONLY the {enhancement_type.value} content, no explanations.
    """
    
    result = enhance_agent.run_sync(prompt)
    return result.output

notes_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=NoteSchema,
    deps_type=None,
    retries=3
)

@notes_agent.tool_plain
def add_formula_example(topic: str, formula_type: str) -> str:
    """Add a formula or syntax example when missing from source material"""
    return search_enhancement(topic, EnhancementType(formula_type))

def get_chunks_by_topic(document_id: int, topic: str) -> List[str]:
    """Enhanced chunk retrieval with semantic search fallback"""
    with Session(engine) as session:
        # First try exact match
        chunks = session.exec(
            select(Chunk).where(
                Chunk.document_id == document_id,
                Chunk.content.ilike(f"%{topic}%")
            ).limit(10)
        ).all()
        
        # Fallback: get related chunks by vector similarity
        if not chunks or len(chunks) < 3:
            query_embedding = Embedder.get_instance().generate_embeddings([topic])[0]
            chunks = session.exec(
                select(Chunk)
                .where(Chunk.document_id == document_id)
                .order_by(Chunk.embedding.cosine_distance(query_embedding))
                .limit(8)
            ).all()
        
        if not chunks:
            logger.warning(f"No chunks found for topic '{topic}'")
            return []
            
    return [c.content for c in chunks]

class EnhancedNotesAgent:
    """Agent that iteratively enhances notes until completeness"""
    
    def __init__(self):
        self.base_agent = notes_agent
        
    async def generate_with_enhancement(self, topic: str, chunks_context: str, max_iterations: int = 3) -> NoteSchema:
        """Generate notes with iterative enhancement until quality threshold met"""
        current_note = None
        
        for iteration in range(max_iterations):
            if iteration == 0:
                prompt = f"""
                Create comprehensive study notes for: {topic}
                
                Source content: {chunks_context}
                
                IMPORTANT INSTRUCTIONS:
                1. If the source content has NO formulas, syntax, or code examples:
                   - USE YOUR KNOWLEDGE to generate 2-3 relevant formulas, syntax patterns, or code snippets.
                   - For Unix commands, include the exact command syntax.
                   - For programming, include code examples.
                   - For math, include formulas.
                   
                Requirements:
                - Summary: 2-3 sentences, clearly explaining core concept
                - Key points: 5-8 bullet points covering fundamentals to advanced
                - Formulas: 0-5 items (include at least 2 if relevant to topic)
                - Examples: 0-3 practical examples (include at least 1 with code/commands)
                """
            else:
                prompt = f"""
                Improve these notes for {topic} by adding:
                - Missing formulas, syntax, or code examples
                - More practical examples
                - Deeper explanations where needed
                
                Current notes:
                {current_note.model_dump_json() if current_note else 'None'}
                
                Source: {chunks_context}
                """
            
            result = await self.base_agent.run(prompt)
            current_note = result.output
            
            quality_score = await self.assess_quality(current_note, topic)
            
            if quality_score >= 0.8:
                logger.info(f"Note for {topic} reached quality {quality_score}, stopping")
                break
            else:
                logger.info(f"Iteration {iteration+1}: quality {quality_score}, enhancing further")
        
        return current_note
    
    async def assess_quality(self, note: NoteSchema, topic: str) -> float:
        """Assess if note has sufficient formulas/examples"""
        score = 0.0
        
        if note.formulas:
            score += 0.3
        elif "syntax" in topic.lower() or "command" in topic.lower():
            score -= 0.2
            
        if note.examples:
            score += 0.3
            
        if len(note.key_points) >= 6:
            score += 0.2
            
        if len(note.summary) > 100:
            score += 0.2
            
        return min(max(score, 0.0), 1.0)

enhanced_agent = EnhancedNotesAgent()

def generate_note_for_topic(document_id: int, topic: str) -> NoteSchema:
    """Synchronous wrapper for async iterative generation"""
    chunks = get_chunks_by_topic(document_id, topic)
    chunks_context = "\n---\n".join(chunks[:8]) if chunks else f"Generate content about {topic}"
    
    # Run the async loop inside a synchronous function
    note = asyncio.run(enhanced_agent.generate_with_enhancement(topic, chunks_context))
        
    return note

def generate_all_notes(document_id: int, topics: List[str]) -> List[NoteSchema]:
    notes = []
    new_notes = []
    
    # Delete existing notes to make this idempotent
    with Session(engine) as session:
        session.exec(delete(GeneratedNote).where(GeneratedNote.document_id == document_id))
        session.commit()
    
    for idx, topic in enumerate(topics):
        try:
            logger.info(f"Generating note {idx+1}/{len(topics)}: {topic}")
            note_data = generate_note_for_topic(document_id, topic)
            
            gn = GeneratedNote(
                document_id=document_id,
                topic=note_data.topic,
                summary=note_data.summary,
                key_points=note_data.key_points,
                formulas=[f.model_dump() for f in note_data.formulas],
                examples=[e.model_dump() for e in note_data.examples]
            )
            new_notes.append(gn)
            notes.append(note_data)
            
        except Exception as e:
            logger.error(f"Failed on topic {topic}: {e}")
            continue
            
    if new_notes:
        with Session(engine) as session:
            session.add_all(new_notes)
            session.commit()
            logger.info(f"Saved {len(new_notes)} notes to database")
            
    return notes
