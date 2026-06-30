# ⚕️ Sanjeevani WhatsApp Chatbot

Sanjeevani is an AI-powered WhatsApp pharmacy assistant designed to streamline the process of ordering medicines and managing health-related queries. Built with **FastAPI**, **Groq AI (Llama 3.3/3.1)**, and **MongoDB**, it provides a seamless, conversational experience for users to register, order medicines, and track their deliveries.

---

## 🚀 Key Features

- **Dual-Provider Support**: Integrated with both **Twilio WhatsApp API** and **Meta WhatsApp Cloud API**.
- **AI-Driven Conversations**: Uses high-performance LLMs via **Groq** for Natural Language Understanding (NLU) and conversational replies.
- **Multi-lingual Support**: Onboarding and interaction available in **English**, **Hindi (हिंदी)**, and **Marathi (मराठी)**.
- **Robust Onboarding**: Guided flow to collect user preferences (Language, Name, Gender, Age).
- **Medicine Ordering**: Intuitive ordering system allowing users to specify medicine names and quantities.
- **Address Management**: Users can save multiple addresses (Home, Office, etc.) and select them during checkout.
- **Order Tracking**: Real-time access to recent order statuses.
- **Persistent State**: MongoDB-backed state management ensures conversations continue exactly where they left off.

---

## 🛠️ Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **AI/LLM**: [Groq](https://groq.com/) (Llama-3.3-70b / Llama-3.1-8b)
- **Database**: [MongoDB](https://www.mongodb.com/) with [Motor](https://motor.readthedocs.io/) (Async driver)
- **Containerization**: [Docker](https://www.docker.com/) & [Docker Compose](https://docs.docker.com/compose/)
- **API Clients**: [Httpx](https://www.python-httpx.org/), [Twilio SDK](https://www.twilio.com/docs/libraries/python)

---

## 📁 Project Structure

```text
.
├── app/
│   ├── api/          # Webhook routes for Twilio and Meta
│   ├── core/         # Configuration, Database connection, and Logger
│   ├── models/       # Pydantic models and Enums (State, Schema)
│   ├── services/     # Core logic: AI (NLU/NLG), DB operations, Rule Engine, and Provider logic
│   └── main.py       # Application entry point
├── Dockerfile        # Production Docker configuration
├── docker-compose.yml # Local development setup (App + MongoDB)
├── render.yaml       # Deployment configuration for Render
├── requirements.txt  # Python dependencies
└── .env.example      # Sample environment variables
```

---

## ⚙️ Setup & Installation

### 1. Local Development

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd "Sanjeevani Assistant"
    ```

2.  **Create a virtual environment**:
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables**:
    Copy `.env.example` to `.env` and fill in your credentials.
    ```bash
    cp .env.example .env
    ```

5.  **Run the application**:
    ```bash
    python -m app.main
    ```
    The server will start at `http://localhost:8000`.

### 2. Docker Setup

To run the entire stack (including MongoDB) using Docker:
```bash
docker-compose up --build
```

---

## 🔑 Environment Variables

Required variables in your `.env` file:

| Variable | Description |
| :--- | :--- |
| `MONGODB_URL` | MongoDB connection string (e.g., `mongodb://localhost:27017`) |
| `GROQ_API_KEY` | Your Groq API Key |
| `GROQ_MODEL` | Model to use (e.g., `llama-3.3-70b-versatile`) |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token |
| `TWILIO_WHATSAPP_NUMBER`| Your Twilio WhatsApp Number (e.g., `whatsapp:+14155238886`) |
| `META_ACCESS_TOKEN` | Meta WhatsApp Cloud API Access Token |
| `META_PHONE_NUMBER_ID` | Meta Phone Number ID |
| `META_VERIFY_TOKEN` | Custom token for Meta webhook verification |
| `VERIFY_TOKEN` | Fallback verification token |

---

## 🔗 Webhook Configuration

### Twilio
1.  Go to the [Twilio Console](https://console.twilio.com/).
2.  Navigate to your WhatsApp Sandbox or Production sender settings.
3.  Set the **"When a message comes in"** URL to: `https://your-domain.com/webhook`.
4.  Ensure the method is set to **POST**.

### Meta WhatsApp Cloud API
1.  Go to the [Meta Developers Console](https://developers.facebook.com/).
2.  Navigate to the WhatsApp **Configuration** section.
3.  Set the **Callback URL** to: `https://your-domain.com/webhook/meta`.
4.  Set the **Verify Token** to match your `META_VERIFY_TOKEN` env variable.
5.  Subscribe to `messages` under Webhook Fields.

---

## ☁️ Deployment

### Deploy to Render
This project includes a `render.yaml` for quick deployment.
1.  Push your code to GitHub.
2.  Connect your GitHub account to [Render](https://render.com/).
3.  Select the **Blueprints** menu and choose this repository.
4.  Render will automatically provision the Web Service and prompt for environment variables.

---

## 🛡️ License

This is a **private** repository. All rights reserved © 2026 Sanjeevani AI.
