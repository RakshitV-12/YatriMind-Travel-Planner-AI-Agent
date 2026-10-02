# ✈️ YatriMind — Multi-Agent Travel Planner AI Agent

An open-source AI travel planner that turns natural-language trip requests into structured itineraries with real flights, verified hotels, day-by-day schedules with Google Maps links, budget breakdowns, and an in-app companion chatbot named **COMPASS**.

Powered by **LangGraph**, **LangChain**, **Groq LLMs**, and **FastAPI**, with a boarding-pass themed frontend and automatic inactivity session timeout.

---

## 🌟 Key Features

- **Multi-Agent Workflow (LangGraph)**:
  - **Flight Agent**: Searches real routes via AviationStack with auto-fallback to nearest airport hubs.
  - **Hotel Agent**: Gathers verified hotels, ratings, and amenities via Tavily Search.
  - **Itinerary Agent**: Generates day-by-day morning, afternoon, evening activities and dining spots.
  - **Synthesis Agent**: Computes budget allocation and formats the complete trip plan.
- **COMPASS Travel Companion**: In-app conversational assistant for packing, tips, and itinerary tweaks.
- **Interactive Ticket Dashboard**: Real flight cards, hotel recommendations, day-by-day itinerary, and expense breakdown.
- **Authentication & Inactivity Timeout**:
  - Email/password sign-up/login with lockout protection, plus Google OAuth 2.0.
  - Persistent login across tabs and reloads.
  - 15-minute inactivity session timeout with an interactive 60-second warning countdown modal.

---

## 🛠️ Tech Stack

- **Backend**: Python 3.10+, FastAPI, Uvicorn, LangGraph, LangChain, Groq SDK
- **APIs**: Tavily Search, AviationStack, `airportsdata`, `pycountry`
- **Frontend**: Vanilla JavaScript, HTML5, CSS3
- **Auth**: Google Identity Services, PBKDF2 hashing, session cookies

---

## 📁 Project Structure

```text
.
├── backend/
│   ├── agent.py              # LangGraph travel planner graph
│   ├── ana_chat.py           # COMPASS AI companion chatbot
│   ├── auth.py               # Authentication & session endpoints
│   ├── checkpointer_json.py  # JSON checkpoint state persistence
│   └── main.py               # FastAPI backend & routes
├── frontend/
│   ├── index.html            # Search & trip form
│   ├── login.html            # Boarding-pass login & register page
│   ├── results.html          # Itinerary dashboard & COMPASS chat
│   ├── session.js            # Inactivity session timeout manager
│   ├── app.js                # Search form logic & user session
│   ├── results.js            # Itinerary rendering & COMPASS chat
│   └── style.css             # UI styling & timeout modal
├── tools/
│   ├── flight_tool.py        # AviationStack flight search & nearest hub finder
│   └── tavily_tool.py        # Tavily hotel & attractions search
├── .env.example              # Environment variables template
├── requirements.txt          # Python dependencies
└── README.md
```

---

## ⚙️ Quickstart

### 1. Clone & Setup Virtual Environment

```bash
git clone https://github.com/RakshitV-12/YatriMind-Travel-Planner-AI-Agent.git
cd YatriMind-Travel-Planner-AI-Agent

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file (copy from `.env.example`):

```env
GROQ_API_KEY=your_groq_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here
AVIATIONSTACK_API_KEY=your_aviationstack_api_key_here

# Optional
GROQ_MODEL=qwen/qwen3.8-27b
DEFAULT_ORIGIN_IATA=DEL
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com
```

### 4. Run the Application

```bash
uvicorn backend.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Travel planner search page |
| `GET` | `/login` | Sign in / register page |
| `GET` | `/results` | Itinerary dashboard |
| `POST` | `/api/plan-trip` | Generate complete trip itinerary |
| `POST` | `/api/chat` | Chat with COMPASS companion |
| `POST` | `/api/auth/register` | Create user account |
| `POST` | `/api/auth/login` | User login |
| `POST` | `/api/auth/google` | Google OAuth sign-in |
| `POST` | `/api/auth/logout` | Sign out & clear session |
| `GET` | `/api/auth/me` | Current authenticated user |
| `GET` | `/health` | Health check |

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.