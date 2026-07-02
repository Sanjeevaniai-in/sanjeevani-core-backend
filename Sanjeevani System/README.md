# 💊 Sanjeevani System — Backend API

Welcome to the **Backend System** of Sanjeevani! This is the core of the pharmacy platform. It handles all data, powers AI predictions for medicine refills, manages inventory intelligence, and connects all apps (Web & Mobile) together.

It is built completely with **Python** using the super-fast **FastAPI** framework and connects to a **MongoDB** database. 🚀

---

## ✨ Getting Started for Beginners (Local Setup)

Don't worry if this is your first time setting this up. Just follow these steps one by one!

### 1️⃣ What You Need Installed
Before starting, ensure you have these installed on your machine:
* **Python (3.11 or newer)**: [Download here](https://www.python.org/downloads/)
* **MongoDB**: You can run it locally or use a cloud version. 

### 2️⃣ Setup Your Virtual Environment
A virtual environment keeps our project packages isolated so they don't mess with your computer. In your terminal (inside this folder), run:
```bash
# Create the virtual environment
python -m venv .venv

# Activate it (Windows)
.venv\Scripts\activate

# Activate it (Mac/Linux)
source .venv/bin/activate
```
*(You should see `(.venv)` appear in your terminal prompt!)*

### 3️⃣ Install Required Packages
Now, let's install everything the backend needs to run:
```bash
pip install -r requirements.txt
```

### 4️⃣ Set Up Environment Variables
We need to tell the app where the database and secret keys are.
1. Copy `.env.example` and rename it to `.env`.
2. Open `.env` and set the `MONGO_URI` to your MongoDB connection string.
3. Add your `GROQ_API_KEY` and any other required keys.

### 5️⃣ Load Sample Data
Let's populate the database with some testing data (patients, orders, inventory) so the app isn't empty!
```bash
python scripts/load_data.py --orders data/consumer_orders.xlsx --products data/products.xlsx
```

### 6️⃣ Run the API Server! 🎉
Start the server with this command:
```bash
uvicorn app.main:app --reload
```
That's it! 🥳 You can now visit:
* **http://localhost:8000** - The API root
* **http://localhost:8000/api/v1/docs** - The interactive Swagger UI where you can **test all APIs easily!** This is super helpful for frontend and mobile devs!

---

## 🏗️ Project Structure Explained

* 📁 **`app/`** - The core application code.
  * `api/` - Contains all our endpoints (like `/products`, `/orders`, `/dashboard`).
  * `database/` - Everything related to connecting and interacting with MongoDB.
  * `modules/` - The "brain" logic (AI predictions, safety checks, inventory intelligence).
* 📁 **`scripts/`** - Helper scripts for doing tasks like loading data or running batch predictions.
* 📁 **`tests/`** - Where we write tests to make sure everything works perfectly.

---

## 🐳 Docker Setup (The "One-Click" Way)

If you have **Docker** installed, skipping the manual setup is super easy:
```bash
# This will build and run the backend AND MongoDB automatically!
docker-compose up --build
```
Your API will be running on `http://localhost:8000` just like that! To view the database, you can connect to `localhost:27017`.

---

## 🧪 Testing and Analytics

* To run batch AI predictions for refills:
  ```bash
  python scripts/generate_predictions.py
  ```
* To run the automated tests:
  ```bash
  pytest tests/ -v
  ```

---

## 🤝 For the Team
If you're building a new feature:
1. Always add your models in `app/database/models.py`.
2. Try to keep your API endpoints clean and well-documented.
3. For testing APIs visually, always use the awesome `/api/v1/docs` page.

Happy Coding! ✨🔥
