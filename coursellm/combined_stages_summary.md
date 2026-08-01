# CourseLLM: Architecture & Stages Overview

Welcome to the **CourseLLM** project! This document elegantly summarizes the architectural progression and development stages detailed in the combined documentation. The project evolves from a foundational RAG (Retrieval-Augmented Generation) backend into a sophisticated, multi-agent educational platform.

---

## 🏗️ Phase 1: Production RAG Foundation

The first phase focuses on building a robust, production-ready backend for ingesting course documents and answering student queries.

* **Stage 1: Multi-Tenant Backend & Database Migration**
  * Transitioned from SQLite/SQLAlchemy to **PostgreSQL & SQLModel** for asynchronous, scalable data handling.
  * Implemented the **Repository Pattern** to cleanly separate database logic from API routes.
* **Stage 2: Document Ingestion Pipeline**
  * Created the pipeline to upload PDFs, chunk text, generate vector embeddings, and store them securely using **pgvector**.
* **Stage 3: Semantic Retrieval & Generation**
  * Built the core RAG logic: fetching the most relevant document chunks based on semantic similarity (Cosine Distance) and generating LLM responses.
* **Stage 5: Hybrid Retrieval**
  * Upgraded the retrieval system to combine vector search (semantic) with keyword search (BM25 or similar) for higher accuracy.

---

## 🤖 Phase 2: Multi-Agent System

With the RAG foundation in place, the system introduces specialized AI agents to handle complex, domain-specific tasks.

* **Stage A: Tutor Agent** 
  * A specialized agent for interactive, context-aware student tutoring.
* **Stage B: Quiz Generator Agent**
  * Automates the creation of quizzes and practice tests based on ingested course material to test student comprehension.
* **Stage C: Topic Extraction & Evaluation**
  * Introduces an **Evaluation Agent** to analyze student answers, extract topics of weakness, and provide structured feedback.

---

## 📅 Phase 3: Course-Aware Study Planner (MCP)

This phase turns the application from a reactive Q&A bot into a proactive virtual teaching assistant.

* **Stage D: Planner Agent & MCP Integration**
  * **Model Context Protocol (MCP)**: Implements an `EmailService` and `CalendarService` to simulate fetching a student's inbox.
  * The Planner Agent parses emails to extract assignments, exams, and deadlines, automatically adding them to a calendar DB.
  * It then generates personalized **Study Plans** and daily briefings to help students manage their workload.

---

## 📊 Phase 4: Observability & Analytics

* **Stage E: Pipeline Observability (Langfuse)**
  * Integrates **Langfuse** to trace LLM calls, monitor token usage, track latency, and debug complex multi-agent workflows.
  * Ensures the system remains reliable and performant as it scales.

---

> [!TIP]
> **Why this progression?** 
> Many developers fail by jumping straight into complex frameworks like LangChain or LangGraph without a solid foundation. By building a robust PostgreSQL/pgvector backend first (Stages 1-5) before adding agentic routing (Stages A-D), **CourseLLM** guarantees scalability, observability, and clean architecture.
