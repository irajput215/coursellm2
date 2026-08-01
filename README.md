# CourseLLM

CourseLLM is a full-stack application designed to act as an AI-powered study assistant. It helps students organize their learning materials, query their notes, schedule study plans, and evaluate their understanding through an intelligent conversational interface.

## Repository Structure

This repository is organized as a monorepo containing two main projects:

- **`coursellm/` (Backend):** A robust FastAPI Python application.
- **`coursellm-frontend/` (Frontend):** A modern React TypeScript application built with Vite and Tailwind CSS.

---

## 🛠 Features & Functionalities

### 1. Document Upload & Processing
- **Knowledge Base Ingestion:** Upload PDF or text documents to add them to your personalized course knowledge base.
- **Background Processing:** Documents are processed, chunked, and embedded asynchronously.

### 2. Intelligent Chat (Ask/RAG)
- **Retrieval-Augmented Generation (RAG):** Ask questions about your study materials and receive highly accurate, context-aware answers.
- **Conversational UI:** Seamless chat interface tracking conversation history and providing context for the AI.

### 3. Study Planner & Email Sync
- **Email Integration:** Sync your university/course emails to automatically track deadlines and schedule changes.
- **Daily Briefing:** Dynamically generate a study plan based on upcoming tests and assignments extracted from your communications.

### 4. Evaluation & Progress Tracking
- **Automated Grading:** Test your knowledge and receive automated evaluations from the AI on your answers.
- **Progress Insights:** Track your average scores, improvement metrics, and review past evaluations to identify areas of weakness.

### 5. Secure Authentication
- **OAuth2 Flow:** Complete login and registration system with JWT-based session management.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Node.js 18+ (for frontend)
- PostgreSQL (or SQLite for development)

### Running the Backend (`coursellm/`)
1. Navigate to the backend directory:
   ```bash
   cd coursellm
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the FastAPI development server:
   ```bash
   ./run_dev.sh
   # The API will be accessible at http://localhost:8000
   ```

### Running the Frontend (`coursellm-frontend/`)
1. Navigate to the frontend directory:
   ```bash
   cd coursellm-frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the Vite development server:
   ```bash
   npm run dev
   # The web client will be accessible at http://localhost:5173
   ```

---

## 🎨 Frontend Tech Stack
- **Framework:** React 18 with Vite
- **Language:** TypeScript
- **Styling:** Tailwind CSS (LinkedIn-inspired UI theme)
- **Icons:** Lucide React
- **Routing:** React Router DOM
- **HTTP Client:** Axios (configured for `http://localhost:8000`)

## ⚙️ Backend Tech Stack
- **Framework:** FastAPI
- **Database:** SQLModel / PostgreSQL (with vector support)
- **Migrations:** Alembic
- **AI Integration:** LLM integration for Retrieval-Augmented Generation, Planners, and Evaluators.
