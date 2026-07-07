# AlgoEdge Trading Bot - VPS Deployment Guide

This guide provides step-by-step instructions for deploying the AlgoEdge Trading Bot (Backend + Frontend) to a Virtual Private Server (VPS) for 24/7 live trading.

> [!WARNING]
> **CRITICAL OS REQUIREMENT**: The `MetaTrader5` Python library is built natively for Windows. To execute live trades, your VPS **MUST** be running **Windows Server** (e.g., Windows Server 2022). Linux VPS environments (Ubuntu/Debian) will **not** work natively for the MT5 module.

## 1. Prerequisites

Before starting, ensure your Windows VPS has the following installed:
1. **Python 3.11+**: Ensure "Add Python to PATH" is checked during installation.
2. **Node.js (v18+)**: Required for building and serving the React frontend.
3. **Redis**: Required for backtest caching and real-time WebSocket state management. Since official Redis is Linux-only, install **[Memurai](https://www.memurai.com/)** (a native Windows port of Redis) or run Redis via Docker Desktop.
4. **PostgreSQL**: (Optional but recommended) For storing config and trade history securely. Alternatively, you can continue using the built-in SQLite engine (`sqlite+aiosqlite:///algoedge.db`).
5. **MetaTrader 5 Terminal**: Install MT5 and log in to your broker account. Ensure "Allow Algorithmic Trading" is enabled in the MT5 options.

---

## 2. Backend Setup

1. **Clone the Repository** and navigate to the project root:
   ```cmd
   git clone https://github.com/your-repo/AlgoEdge.git
   cd AlgoEdge
   ```

2. **Create a Virtual Environment**:
   ```cmd
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```cmd
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables**:
   Create a `.env` file in the root directory:
   ```env
   # Database
   # For SQLite: sqlite+aiosqlite:///algoedge.db
   # For Postgres: postgresql+asyncpg://user:pass@localhost:5432/algoedge
   DATABASE_URL=sqlite+aiosqlite:///algoedge.db
   
   # Redis
   REDIS_URL=redis://localhost:6379/0
   
   # Security
   SECRET_KEY=your_secure_random_string
   ALGORITHM=HS256
   
   # Server
   PORT=8000
   DEBUG=False
   ```

5. **Start the Backend Server**:
   ```cmd
   uvicorn backend.main:app --host 0.0.0.0 --port 8000
   ```
   *The database schema will automatically migrate on the first boot.*

---

## 3. Frontend Setup

1. **Navigate to the frontend directory**:
   ```cmd
   cd frontend
   ```

2. **Install Node Modules**:
   ```cmd
   npm install
   ```

3. **Configure Environment**:
   Create a `.env` file in the `frontend` folder:
   ```env
   VITE_API_URL=http://<YOUR_VPS_PUBLIC_IP>:8000
   VITE_WS_URL=ws://<YOUR_VPS_PUBLIC_IP>:8000
   ```

4. **Build for Production**:
   ```cmd
   npm run build
   ```

5. **Serve the Frontend**:
   You can serve the `dist` folder using a production-ready server like Nginx, or quickly serve it using Python's http module:
   ```cmd
   npm install -g serve
   serve -s dist -l 3000
   ```

---

## 4. Running as a Background Service

To ensure the bot runs 24/7 even when you disconnect from the VPS Remote Desktop:

1. Use **PM2** (via Node.js) to keep both servers alive automatically:
   ```cmd
   npm install -g pm2
   ```

2. Create a `ecosystem.config.js` file in the root of your project:
   ```javascript
   module.exports = {
     apps: [
       {
         name: "AlgoEdge-Backend",
         script: ".venv\\Scripts\\uvicorn.exe",
         args: "backend.main:app --host 0.0.0.0 --port 8000",
         cwd: "C:\\path\\to\\AlgoEdge",
         interpreter: "none"
       },
       {
         name: "AlgoEdge-Frontend",
         script: "serve",
         args: "-s dist -l 3000",
         cwd: "C:\\path\\to\\AlgoEdge\\frontend",
       }
     ]
   }
   ```

3. Start everything:
   ```cmd
   pm2 start ecosystem.config.js
   pm2 save
   ```

## 5. Security Recommendations
- **Firewall**: Expose port `3000` (Frontend) to the public, but strongly consider keeping port `8000` (Backend) behind a reverse proxy (like Nginx) to enforce SSL/TLS encryption.
- **Passwords**: Immediately change the default admin credentials inside the dashboard on your first login.
- **Backups**: If using SQLite, periodically back up the `algoedge.db` file.
