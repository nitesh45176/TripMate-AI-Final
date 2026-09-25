# ✈️ TripMate AI — Multi-Agent Travel Planner

**TripMate AI** is an advanced, production-ready multi-agent travel planning system built with **FastAPI**, **LangGraph**, **Groq (GPT-OSS 120B)**, **PostgreSQL**, and **Model Context Protocol (MCP)** integrations for live aviation, weather, and hotel search.

Featuring an **autonomous supervisor router**, **safety guardrails**, **token-optimized LLM calls**, and a **Human-in-the-Loop (HITL) approval gate**, TripMate AI generates comprehensive, budget-aware, multi-day travel itineraries complete with interactive UI sub-tabs and PDF export capabilities.

---

## 🌟 Key Features

- **🤖 Supervisor Routing Engine**: Dynamically routes travel requests to specialized agents (*Flight*, *Hotel*, *Weather*, *Budget*, *Itinerary*) based on user intent.
- **🛡️ Input Guardrail Agent**: Detects and blocks off-topic or unsafe queries before invoking specialized agents.
- **🔌 Model Context Protocol (MCP) Integration**:
  - **AviationStack MCP**: Retrieves airport codes, route options, and airline lists.
  - **Tavily MCP**: Executes real-time web searches for top accommodations, neighborhood advice, and local attractions.
  - **OpenWeather MCP**: Fetches live weather conditions and 5-day forecasts for destination cities.
- **💰 Budget Feasibility Analyst**: Evaluates pricing risk factors, category breakdowns, and money-saving advice.
- **👤 Human-in-the-Loop (HITL) Gate**: Intercepts draft itineraries using LangGraph `interrupt()`, allowing travelers to approve or request revisions (*Lower Hotel Budget*, *Faster Flight*, *More Free Time*) before generating the final verified response.
- **⚡ Token Rate-Limit Resilience**: Includes prompt context truncation and exponential backoff retry handling for Groq's 8,000 TPM limit.
- **🎨 Glassmorphism Interactive UI**: Features dual input modes (Natural Prompt vs Guided Form Builder), visual agent execution cards, sub-tabs for flights/hotels/weather/budget, markdown rendering, and 1-click **PDF Download**.

---

## 🏗️ System Architecture

```mermaid
flowgraph TD
    User([User Request]) --> Guardrail{Input Guardrail}
    Guardrail -->|Blocked| BlockedResponse[Return Safety Explanation]
    Guardrail -->|Passed| Supervisor[Supervisor Agent]
    
    Supervisor --> FlightAgent[✈️ Flight Agent / AviationStack MCP]
    Supervisor --> HotelAgent[🏨 Hotel Agent / Tavily MCP]
    Supervisor --> WeatherAgent[🌦️ Weather Agent / OpenWeather MCP]
    Supervisor --> BudgetAgent[💰 Budget Analyst Agent]
    
    FlightAgent --> ItineraryAgent[🗓️ Itinerary Specialist Agent]
    HotelAgent --> ItineraryAgent
    WeatherAgent --> ItineraryAgent
    BudgetAgent --> ItineraryAgent
    
    ItineraryAgent --> HITL{👤 Human-in-the-Loop Review}
    HITL -->|Revise Feedback| FinalAgent[✨ Final Response Agent]
    HITL -->|Approved| FinalAgent
    FinalAgent --> Response([Final Travel Plan + PDF Export])
```

---

## 📁 Project Structure

```text
TripMate-AI-Final/
├── app.py                      # FastAPI web server & API endpoints
├── backend.py                  # LangGraph StateGraph workflow, nodes, & checkpointer
├── mcp_client.py               # MCP client setup (Tavily, AviationStack, Weather)
├── weather_mcp_server.py       # Custom FastMCP weather server (OpenWeather API)
├── static/
│   ├── style.css               # Glassmorphism UI styling & print layout
│   └── script.js               # Frontend state management, tab switching & API handling
├── templates/
│   └── index.html              # Main single-page application template
├── Dockerfile                  # Container build instructions
├── .dockerignore               # Files excluded from Docker builds
├── .gitignore                  # Git untracked patterns (.env, __pycache__)
├── .env.example                # Template for environment variables
└── requirements.txt            # Python dependencies
```

---

## ⚙️ Prerequisites & Setup

### 1. Requirements
- **Python 3.10+**
- **PostgreSQL Database** (Render PostgreSQL, Supabase, Neon, or local PostgreSQL)
- API Keys:
  - [Groq API Key](https://console.groq.com/)
  - [Tavily Search API Key](https://tavily.com/)
  - [AviationStack API Key](https://aviationstack.com/)
  - [OpenWeather API Key](https://openweathermap.org/)
  - *(Optional)* [LangSmith API Key](https://smith.langchain.com/) for telemetry

### 2. Installation

Clone the repository:
```bash
git clone https://github.com/nitesh45176/TripMate-AI-Final.git
cd TripMate-AI-Final
```

Create and activate a virtual environment:
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

### 3. Environment Variables Setup

Create a `.env` file in the root directory (refer to `.env.example`):

```env
GROQ_API_KEY="your_groq_api_key"
AVIATIONSTACK_API_KEY="your_aviationstack_api_key"
DEFAULT_ORIGIN_IATA="DAC"
TAVILY_API_KEY="your_tavily_api_key"
OPENWEATHER_API_KEY="your_openweather_api_key"

# PostgreSQL Connection String (Render / Supabase / Local)
DATABASE_URL="postgresql://user:password@hostname:5432/dbname?sslmode=require"

# LangSmith Telemetry (Optional)
LANGSMITH_TRACING="true"
LANGSMITH_ENDPOINT="https://api.smith.langchain.com"
LANGSMITH_API_KEY="your_langsmith_api_key"
LANGSMITH_PROJECT="TripMate-AI"
```

---

## 🚀 Running Locally

Start the FastAPI development server:
```bash
python app.py
```
*Or using uvicorn directly:*
```bash
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser and navigate to:
```text
http://127.0.0.1:8000
```

---

## 📦 Deployment Guide

### Option 1: Deploying on Render (Recommended)

1. **Push your code to GitHub**:
   Ensure your latest code is pushed to your repository (`main` branch).

2. **Create a PostgreSQL Database on Render**:
   - Log into [Render Dashboard](https://dashboard.render.com/).
   - Click **New +** $\rightarrow$ **PostgreSQL**.
   - Name your database (e.g. `tripmate-db`) and click **Create Database**.
   - Copy the **External Database URL** (e.g. `postgresql://user:password@dpg-xxx.oregon-postgres.render.com/dbname`).

3. **Deploy as a Web Service on Render**:
   - In Render Dashboard, click **New +** $\rightarrow$ **Web Service**.
   - Connect your GitHub repository (`TripMate-AI-Final`).
   - Configure the service:
     - **Name**: `tripmate-ai`
     - **Environment**: `Python 3`
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - Scroll down to **Environment Variables** and add:
     - `GROQ_API_KEY`
     - `AVIATIONSTACK_API_KEY`
     - `TAVILY_API_KEY`
     - `OPENWEATHER_API_KEY`
     - `DATABASE_URL` *(Paste the Render PostgreSQL URL)*
     - `DEFAULT_ORIGIN_IATA` = `DAC`
   - Click **Create Web Service**.

---

### Option 2: Deployment via Docker

1. **Build the Docker image**:
   ```bash
   docker build -t tripmate-ai .
   ```

2. **Run the Container**:
   ```bash
   docker run -d \
     -p 8000:8000 \
     --env-file .env \
     --name tripmate-app \
     tripmate-ai
   ```

3. **Access Application**:
   Navigate to `http://localhost:8000`.

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for details.

---

## 👥 Credits & Acknowledgments

- **LangGraph & LangChain**: For multi-agent state graph management and Human-in-the-Loop interrupts.
- **Groq**: High-speed LLM inference engine (`openai/gpt-oss-120b`).
- **FastMCP**: Model Context Protocol integration framework.
