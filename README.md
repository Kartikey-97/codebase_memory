# Codebase Memory

Codebase Memory is a proactive developer intelligence agent for the Google Cloud Rapid Agent Hackathon (MongoDB Track). 

Unlike traditional "chat with your code" tools that only answer questions when asked, Codebase Memory continuously tracks your codebase to surface structural anomalies, document decay, and architectural risks in real-time, while providing an AI agent capable of deep relationship mapping.

## Features
- **Intelligent Ingestion:** Parses source code into abstract syntax trees (AST) and generates embedding vectors for every code chunk using Voyage AI via MongoDB Atlas Vector Search.
- **Proactive Insights Feed:** Automatically detects issues like stale documentation, over-coupled components, and missing error handling without needing a prompt.
- **Force-Directed Codebase Map:** A dynamic D3.js visual graph showing file dependencies, imports, and ownership.
- **Agentic Chat:** A Gemini 2.5 Pro powered assistant that reasons over MongoDB Atlas Data utilizing Model Context Protocol (MCP) tool integration.

## Architecture & Tech Stack
- **Frontend:** React + Vite, styled with custom CSS for a premium dark mode aesthetic. Hosted on Firebase Hosting.
- **Backend:** FastAPI (Python 3.11). Hosted on Google Cloud Run.
- **Database:** MongoDB Atlas with Vector Search.
- **AI/ML:** Google Cloud Vertex AI (Gemini 2.5 Pro / Gemini 2.0 Flash) and Voyage AI for embeddings.
- **Integration:** MCP-compatible bridge that exposes MongoDB MCP-style tool calls and adds Voyage AI embedding support to securely execute database queries.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `APP_ENV` | Environment (`development` or `production`) |
| `MONGODB_URI` | Connection string for MongoDB Atlas |
| `MONGODB_DB_NAME` | Target database name (e.g. `codebase_memory`) |
| `MONGODB_MCP_URL` | URL of the MongoDB MCP server sidecar |
| `VERTEX_AI_PROJECT` | GCP Project ID for Vertex AI |
| `VERTEX_AI_LOCATION` | GCP Region for Vertex AI (e.g., `us-central1`) |
| `VERTEX_AI_MODEL_CHAT` | Gemini model for conversational agent |
| `VOYAGE_API_KEY` | API key for Voyage AI embeddings |
| `GITHUB_TOKEN` | (Optional) Token to ingest private repositories |

## How it was Built
The project was constructed over a multi-session pair programming marathon. We began by establishing the custom design tokens to ensure a premium UI. The backend is modeled using FastAPI. To qualify for the MongoDB track, we use an MCP-compatible bridge that exposes MongoDB MCP-style tool calls while adding native Voyage AI embedding support. This ensures the agent and backend strictly interact with MongoDB through an MCP-style interface. Vertex AI streams are used to pipe SSE updates in real-time to the React frontend.

## Deployment Instructions

Ensure the GCP CLI and Firebase CLI are installed and authenticated.

1. **Configure Secret Manager**: Create the following secrets in Google Cloud Secret Manager: `MONGODB_URI`, `MONGODB_MCP_URL`, `VOYAGE_API_KEY`, `GITHUB_TOKEN`.
2. **Deploy via script**:
   ```bash
   ./deploy.sh
   ```
   This script builds the React SPA, pushes to Firebase Hosting, then builds the Docker image and deploys the backend to Cloud Run.

## License
MIT License. See [LICENSE](LICENSE) for details.
