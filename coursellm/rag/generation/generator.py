import time

from pydantic_ai import Agent
from core.config import settings
import os
from models.chunk import Chunk
from observability.tracing.langfuse_client import log_pipeline_event, trace_span

os.environ["GROQ_API_KEY"] = settings.GROQ_API_KEY

class AnswerGenerator:
    def __init__(self):
        system_prompt = (
            "You are an expert AI teaching assistant for a university course.\n"
            "Your goal is to help students understand the material deeply, clearly, and accurately based strictly on the provided context.\n\n"
            "Follow these guidelines for every response:\n"
            "1. **Tone & Persona**: Be encouraging, academic, and extremely clear. Treat the student with respect and patience.\n"
            "2. **Structuring Answers**: Break complex topics into digestible bullet points or numbered lists. Use Markdown formatting (bolding, code blocks, italics) to make the text highly readable.\n"
            "3. **Synthesis over Repetition**: Don't just copy-paste the text. Synthesize the information as a real teacher would. If the context contains examples or code snippets, include them to illustrate your points.\n"
            "4. **Handling Ambiguity**: If a student asks a vague question, provide the most likely relevant information from the context and ask if they need clarification on a specific sub-topic.\n"
            "5. **Strict Context Adherence**: You must NOT hallucinate or bring in outside knowledge. If the provided context does not contain the answer, politely state: 'I couldn't find any relevant course material to answer your question' and suggest related topics that are in the text if possible."
        )
        self.agent = Agent(
            'groq:llama-3.3-70b-versatile',
            system_prompt=system_prompt,
        )
        self.model_name = "groq:llama-3.3-70b-versatile"

    async def generate(self, question: str, context_chunks: list[Chunk]) -> str:
        if not context_chunks:
            return "I couldn't find any relevant course material to answer your question."

        start = time.perf_counter()
        with trace_span(
            "generate",
            as_type="generation",
            input={"question": question, "chunk_count": len(context_chunks)},
            metadata={"model": self.model_name},
        ) as span:
            formatted_context = "Here is the context retrieved from the course material:\n\n"
            for i, chunk in enumerate(context_chunks):
                formatted_context += f"--- Chunk {i+1} (Source: Document ID {chunk.document_id}, Page {chunk.page}) ---\n"
                formatted_context += f"{chunk.content}\n\n"
                
            prompt = f"{formatted_context}\n\nStudent Question: {question}"
            
            result = await self.agent.run(prompt)
            answer = result.output

            usage = getattr(result, "usage", None)
            usage_details = None
            if usage is not None:
                usage_details = {
                    "input": getattr(usage, "input_tokens", None) or getattr(usage, "request_tokens", None),
                    "output": getattr(usage, "output_tokens", None) or getattr(usage, "response_tokens", None),
                }
                usage_details = {k: v for k, v in usage_details.items() if v is not None}

            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            update_kwargs: dict = {
                "output": {"answer_preview": answer[:500]},
                "metadata": {"latency_ms": str(latency_ms), "model": self.model_name},
                "model": self.model_name,
            }
            if usage_details:
                update_kwargs["usage_details"] = usage_details
            span.update(**update_kwargs)

            log_pipeline_event(
                "generate",
                question=question,
                chunk_ids=[chunk.id for chunk in context_chunks],
                model=self.model_name,
                latency_ms=latency_ms,
                usage=usage_details,
            )

        return answer
