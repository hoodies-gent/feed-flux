#!/bin/bash

echo -e "\033[1;34m=======================================\033[0m"
echo -e "\033[1;34m      FeedFlux Startup Sequence        \033[0m"
echo -e "\033[1;34m=======================================\033[0m"

echo -e "\n\033[1;36m[+] Running System Pre-flight Checks...\033[0m"
if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null ; then
    echo -e "\033[1;31m[!] Error: Port 3000 is currently occupied by another process.\033[0m"
    echo "Please stop the process (e.g., another Next.js app) or run 'kill -9 \$(lsof -ti:3000)' before continuing."
    exit 1
fi

if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null ; then
    echo -e "\033[1;31m[!] Error: Port 8000 is currently occupied by another process.\033[0m"
    echo "Please stop the process (e.g., another FastAPI/Django app) or run 'kill -9 \$(lsof -ti:8000)' before continuing."
    exit 1
fi

if command -v docker &> /dev/null
then
    echo -e "\n\033[1;32m[+] Docker detected! Starting FeedFlux via Docker Compose...\033[0m"
    docker compose up --build -d

    echo -e "\n\033[1;32m[✓] All services are booting up in the background!\033[0m"
    echo -e "\033[1;34m--------------------------------------------------------\033[0m"
    echo -e "🔗 Frontend Interface: \033[4;36mhttp://localhost:3000\033[0m"
    echo -e "🔗 Backend API Engine: \033[4;36mhttp://localhost:8000\033[0m"
    echo -e "\033[1;34m--------------------------------------------------------\033[0m"
    echo -e "📄 To follow live logs, run: \033[1;33mdocker compose logs -f\033[0m"
    echo -e "⏹️  To stop the system, run:  \033[1;31mdocker compose down\033[0m\n"
else
    echo -e "\n\033[1;33m[!] Docker Desktop not found locally. Engaging Native Fallback Mode...\033[0m"
    
    echo -e "\n\033[1;36m[1/2] Initializing Python Backend Environment...\033[0m"
    cd backend
    if [ ! -d "venv" ]; then
        echo "Creating virtual environment..."
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install -r requirements.txt
    
    # Start backend Uvicorn
    echo "Starting Uvicorn..."
    uvicorn api:app --host 0.0.0.0 --port 8000 &
    BACKEND_PID=$!
    cd ..

    echo -e "\n\033[1;36m[2/2] Initializing Node.js Frontend Environment...\033[0m"
    cd frontend
    npm install
    
    echo -e "\n\033[1;32m[✓] FeedFlux is live natively!\033[0m"
    echo -e "🌐 Frontend running at: http://localhost:3000"
    echo -e "⚙️  Backend running at:  http://localhost:8000"
    echo -e "\033[1;33m(Press Ctrl+C to stop both servers)\033[0m\n"
    
    # trap keyboard interrupt signal to kill the backend process
    trap "echo -e '\nStopping Backend...'; kill $BACKEND_PID; exit" INT TERM
    
    # start frontend
    npm run dev
fi
