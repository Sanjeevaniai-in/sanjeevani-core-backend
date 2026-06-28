<div align="center">

<img src="https://img.shields.io/badge/Sanjeevani-Backend-10B981?style=for-the-badge&logo=fastapi&logoColor=white" alt="Sanjeevani Backend"/>

# 🏥 Sanjeevani Backend

### Next-Generation AI-Powered Pharmacy Operations Platform

[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?style=flat-square&logo=mongodb&logoColor=white)](https://mongodb.com)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docker.com)
[![Render](https://img.shields.io/badge/Deploy-Render-46E3B7?style=flat-square&logo=render&logoColor=white)](https://render.com)
[![License](https://img.shields.io/badge/License-Private-red?style=flat-square)](.)

> **Sanjeevani** is an intelligent pharmacy OS that combines AI agents, real-time order management, prescription safety validation, and patient refill intelligence — all in one backend monorepo.

</div>

---

## 📦 Repository Structure

This monorepo contains **three independent microservices**, each deployable separately:

```
Sanjeevani Backend/
├── 📂 Sanjeevani System/        # Core API — Orders, AI Agents, Dashboard
├── 📂 Sanjeevani Auth/          # Authentication — JWT + Google OAuth
└── 📂 Sanjeevani Assistant/     # WhatsApp Chatbot — NLP Order Taking
```

---

## 🧠 Services Overview

### 1. `Sanjeevani System` — Core API · Port `8000`

The brain of the platform. Handles all pharmacy operations via a **4-Agent AI Pipeline**.

| Module | Description |
|--------|-------------|
| `orders.py` | Order creation, tracking, status updates (manual + AI-extracted) |
| `chat.py` | AI chatbot endpoint — LLM extracts medicine + quantity from natural language |
| `products.py` | Inventory search, medicine master database, stock management |
| `customers.py` | Patient profiles, purchase history, contact info |
| `dashboard.py` | Real-time analytics, revenue, order counts |
| `alerts.py` | Refill alerts, low-stock notifications |
| `recommendations.py` | AI-powered medicine recommendations based on patient history |
| `agent_routes.py` | Direct access to the 4-Agent orchestration pipeline |

**4-Agent AI Pipeline (`agent_orchestrator.py`):**
```
User Request
    │
    ▼
Agent 1 → Inventory Check    (Is it in stock at this pharmacy?)
    │
    ▼
Agent 2 → Safety Validation  (Does it need Rx? Is it habit-forming?)
    │
    ▼
Agent 3 → Refill Nudge       (Should we remind about chronic medicines?)
    │
    ▼
Agent 4 → Action Summary     (Final response + order_status)
```

---

### 2. `Sanjeevani Auth` — Auth Service · Port `8001`

Handles all identity and access management.

| Feature | Details |
|---------|---------|
| **Google OAuth 2.0** | Sign in with Google for pharmacy owners |
| **JWT Tokens** | HS256 signed, 24h expiry, 30s clock-skew leeway |
| **Merchant Seeding** | `seed_pharmacies.py` — bootstrap pharmacy accounts |
| **Admin Routes** | Pharmacy management, user listing |

---

### 3. `Sanjeevani Assistant` — WhatsApp Bot

AI-powered WhatsApp chatbot for customer order taking.

| Feature | Details |
|---------|---------|
| **NLU Engine** | Extracts medicine name + quantity from conversational text |
| **State Manager** | Multi-turn conversation state per customer |
| **Pharmacy Routing** | Routes orders to the correct pharmacy by customer location |
| **Meta WhatsApp API** | Direct integration with WhatsApp Business API |

---

## 🚀 Quick Start

### Prerequisites

- Python **3.10+**
- MongoDB Atlas URI (or local MongoDB)
- Groq API Key (for AI features)

### 1. Clone & Setup

```bash
git clone https://github.com/Sanjeevaniai-in/sanjeevani-core-backend.git
cd sanjeevani-core-backend
```

### 2. Setup Sanjeevani System (Core API)

```bash
cd "Sanjeevani System"

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Copy env template and fill in your values
cp .env.example .env            # (create this — see Environment Variables below)

# Run development server
python -m uvicorn app.main:app --reload --port 8000
```

### 3. Setup Sanjeevani Auth

```bash
cd "Sanjeevani Auth"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Configure .env (see below)
python -m uvicorn app.main:app --reload --port 8001
```

### 4. Verify Everything is Running

```bash
# Core API health check
curl http://localhost:8000/health

# Auth service health check  
curl http://localhost:8001/health

# Interactive API docs
open http://localhost:8000/api/v1/docs
```

---

## ⚙️ Environment Variables

Create a `.env` file inside each service folder. **Never commit real `.env` files.**

### `Sanjeevani System/.env`

```env
# ── Database ──────────────────────────────────────────────────
MONGO_URI=mongodb+srv://<user>:<pass>@cluster.mongodb.net/
DB_NAME=sanjeevani_rx_db

# Optional: Supabase/Postgres (alternative to MongoDB)
SUPABASE_DB_URL=
POSTGRES_DSN=

# ── Auth ──────────────────────────────────────────────────────
JWT_SECRET=your-super-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# ── AI / LLM ──────────────────────────────────────────────────
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
GROQ_MODEL=llama-3.1-8b-instant
ANTHROPIC_API_KEY=                    # Optional

# ── Deployment ────────────────────────────────────────────────
ENV=development                       # development | staging | production
SERVER_URL=http://localhost:8000      # Public URL (use ngrok in dev)
FRONTEND_URL=http://localhost:5173

# ── Rate Limiting ─────────────────────────────────────────────
RATE_LIMIT_PER_MINUTE=60

# ── Pharmacy ──────────────────────────────────────────────────
DEFAULT_PHARMACY_ID=                  # Default merchant_id for single-store mode
```

### `Sanjeevani Auth/.env`

```env
MONGO_URI=mongodb+srv://<user>:<pass>@cluster.mongodb.net/
DB_NAME=sanjeevani_auth_db
JWT_SECRET=your-super-secret-key-here   # Must match System service
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
FRONTEND_URL=http://localhost:5173
```

---

## 📡 API Reference

Base URL: `http://localhost:8000/api/v1`

### 🛒 Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/orders/manual` | Create a manual order from app/chatbot |
| `GET` | `/orders` | List all orders (paginated, filterable) |
| `GET` | `/orders/{id}` | Get single order by ID |
| `PATCH` | `/orders/{id}/status` | Update order status |
| `GET` | `/orders/tracking/{id}` | Live order tracking |

### 💬 AI Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/chat` | Send message → AI extracts medicines + intent |
| `POST` | `/chat/upload-prescription` | Upload prescription image → OCR extraction |

### 🏪 Products / Inventory

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/products` | Search medicine inventory |
| `GET` | `/products/{id}` | Medicine detail + stock level |
| `POST` | `/products` | Add new product to inventory |
| `PATCH` | `/products/{id}/stock` | Update stock quantity |

### 👥 Customers / Patients

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/customers` | List all patients (paginated) |
| `GET` | `/customers/{id}` | Patient profile + order history |

### 📊 Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/dashboard/overview` | Revenue, orders, patients summary |
| `GET` | `/dashboard/analytics` | Detailed charts data |

### 🤖 AI Agents

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/agents/process-order` | Run full 4-Agent pipeline on an order |

### 🔔 Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/alerts` | Refill alerts, low-stock warnings |

**Full interactive docs:** `http://localhost:8000/api/v1/docs`

---

## 🔒 Authentication

All protected routes require a Bearer JWT token:

```bash
curl -H "Authorization: Bearer <your-jwt-token>" \
     http://localhost:8000/api/v1/orders
```

Tokens are issued by the **Sanjeevani Auth** service on Google OAuth login.

---

## 🐳 Docker Deployment

Each service includes a `Dockerfile` and `docker-compose.yml`.

```bash
# Run Sanjeevani System with Docker
cd "Sanjeevani System"
docker compose up --build

# Run Sanjeevani Auth with Docker
cd "Sanjeevani Auth"
docker compose up --build
```

---

## ☁️ Deploy to Render

Each service has a `render.yaml` for one-click deployment.

1. Push this repo to GitHub ✅ (already done)
2. Go to [render.com](https://render.com) → **New Web Service**
3. Connect the repo, select the service folder as root
4. Add environment variables from the table above
5. Deploy 🚀

**Render start commands:**
```bash
# Sanjeevani System
uvicorn app.main:app --host 0.0.0.0 --port $PORT

# Sanjeevani Auth
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

---

## 🧪 Running Tests

```bash
cd "Sanjeevani System"

# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_order_routing.py -v
```

Test files:

| File | Coverage |
|------|----------|
| `test_api.py` | General API endpoint smoke tests |
| `test_order_routing.py` | Order creation + routing logic |
| `test_data_loader.py` | Medicine data ingestion pipeline |
| `test_predictions.py` | Refill prediction accuracy |
| `test_sms.py` | SMS webhook handling |
| `test_voice.py` | Voice webhook handling |

---

## 📁 Project Architecture

```
Sanjeevani System/
├── app/
│   ├── api/                    # Route handlers (FastAPI routers)
│   │   ├── orders.py           # Order management
│   │   ├── chat.py             # AI chatbot + prescription OCR
│   │   ├── products.py         # Inventory management
│   │   ├── customers.py        # Patient management
│   │   ├── dashboard.py        # Analytics
│   │   ├── alerts.py           # Notifications
│   │   ├── recommendations.py  # AI recommendations
│   │   └── agent_routes.py     # 4-Agent pipeline API
│   ├── modules/                # Business logic
│   │   ├── agent_orchestrator.py   # 4-Agent AI pipeline
│   │   ├── safety_validation.py    # Rx & habit-forming checks
│   │   ├── inventory_intelligence.py
│   │   ├── refill_prediction.py    # Chronic med refill nudges
│   │   ├── recommendation_engine.py
│   │   ├── patient_context.py
│   │   └── dashboard_analytics.py
│   ├── database/               # DB layer
│   │   ├── mongo_client.py     # MongoDB + retry logic
│   │   ├── pg_document_db.py   # Postgres/Supabase adapter
│   │   └── models.py           # Pydantic schemas
│   ├── utils/
│   │   ├── security.py         # JWT verify + create
│   │   ├── ocr_service.py      # Prescription image parsing
│   │   └── logger.py           # Structured logging
│   ├── config.py               # Settings (pydantic-settings)
│   └── main.py                 # FastAPI app factory
├── scripts/                    # One-off utility scripts
├── tests/                      # pytest test suite
├── static/                     # Served HTML pages
├── Dockerfile
├── render.yaml
└── requirements.txt
```

---

## 🛡️ Security Features

| Feature | Implementation |
|---------|---------------|
| **JWT Auth** | HS256, 24h expiry, `exp` claim enforced, 30s clock-skew leeway |
| **Rate Limiting** | SlowAPI — 60 req/min per IP (configurable) |
| **CORS** | Configurable origins via `CORS_ORIGINS` env var |
| **Request Tracing** | `X-Request-ID` header on every response |
| **Rx Lock** | Orders with prescription medicines locked to `PENDING_RX` until OCR validates |
| **Secret Scanning** | `.env` excluded from git; no hardcoded credentials |
| **DB Retry** | MongoDB auto-retries 3× with 1s→2s→4s backoff on Atlas timeouts |

---

## 🤝 Contributing

We love contributions! Whether you're a student, a friend, or an open-source contributor, we'd love your help.

👉 **[Please read our full Contributing Guide (CONTRIBUTING.md) here!](./CONTRIBUTING.md)** 👈

It contains everything you need to know about:
- What this project is and how it helps the world 🌍
- What kind of solutions we accept (Bug fixes, UI improvements, etc.) 💡
- A step-by-step beginner's guide to making your first Pull Request! 🛠️

> ⚠️ **Never commit `.env` files or real API keys.** Use `.env.example` as a template.

---

## 📄 License

This is a **private** repository. All rights reserved © 2026 Sanjeevani AI.

---

<div align="center">

Built with ❤️ for better patient outcomes

**[System API Docs](http://localhost:8000/api/v1/docs)** · **[Auth API Docs](http://localhost:8001/api/v1/docs)** · **[GitHub](https://github.com/Sanjeevaniai-in/sanjeevani-core-backend)**

</div>
