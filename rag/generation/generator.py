from pydantic_ai import Agent
from core.config import settings
import os
from models.chunk import Chunk

# Ensure API key is set for PydanticAI
os.environ["GROQ_API_KEY"] = settings.GROQ_API_KEY

class AnswerGenerator:
    def __init__(self):
        # We define a system prompt that enforces strict grounding in the provided context
        system_prompt = (
            "You are an expert AI tutor. Answer the student's question based strictly on the provided context.\n"
            "If the context does not contain enough information to answer the question, state that clearly.\n"
            "Do NOT hallucinate or use outside knowledge."
        )
        self.agent = Agent(
            'groq:llama-3.3-70b-versatile',
            system_prompt=system_prompt,
        )

    async def generate(self, question: str, context_chunks: list[Chunk]) -> str:
        """
        Generates an answer based on the provided context chunks.
        """
        if not context_chunks:
            return "I couldn't find any relevant course material to answer your question."
            
        # Format the context
        formatted_context = "Here is the context retrieved from the course material:\n\n"
        for i, chunk in enumerate(context_chunks):
            formatted_context += f"--- Chunk {i+1} (Source: Document ID {chunk.document_id}, Page {chunk.page}) ---\n"
            formatted_context += f"{chunk.content}\n\n"
            
        # Combine context and question
        prompt = f"{formatted_context}\n\nStudent Question: {question}"
        
        # Run generation
        result = await self.agent.run(prompt)
        return result.output
