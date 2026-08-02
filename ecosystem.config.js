module.exports = {
    apps: [
        {
            name: "algoedge-backend",
            script: "venv\\Scripts\\python.exe",
            args: "-m uvicorn backend.main:app --host 0.0.0.0 --port 8000",
            cwd: "./",
            interpreter: "none",
            autorestart: true,
            watch: false,
            env: {
                NODE_ENV: "production",
            }
        },
        {
            name: "algoedge-frontend",
            script: "./node_modules/vite/bin/vite.js",
            args: "preview --host 0.0.0.0 --port 80",
            cwd: "./frontend",
            interpreter: "node",
            autorestart: true,
            watch: false,
            env: {
                NODE_ENV: "production",
            }
        }
    ]
};