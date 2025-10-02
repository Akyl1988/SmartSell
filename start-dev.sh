#!/bin/bash

# SmartSell3 Development Startup Script

echo "🚀 Starting SmartSell3 Development Environment"
echo "=============================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install Python dependencies
echo "📥 Installing Python dependencies..."
pip install -r requirements.txt

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "⚙️  Creating .env file..."
    cp .env.example .env
    echo "✏️  Please edit .env file with your configuration"
fi

# Start the FastAPI server in background
echo "🖥️  Starting FastAPI backend on http://localhost:8000..."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Wait a moment for backend to start
sleep 3

# Start frontend if available
if [ -d "frontend" ]; then
    echo "🎨 Starting React frontend on http://localhost:3000..."
    cd frontend

    # Install frontend dependencies if needed
    if [ ! -d "node_modules" ]; then
        echo "📥 Installing frontend dependencies..."
        npm install
    fi

    # Create frontend .env if it doesn't exist
    if [ ! -f ".env" ]; then
        cp .env.example .env
    fi

    npm run dev &
    FRONTEND_PID=$!
    cd ..
else
    echo "⚠️  Frontend directory not found, skipping frontend startup"
fi

echo ""
echo "✅ SmartSell3 is running!"
echo "📋 Available endpoints:"
echo "   • Backend API: http://localhost:8000"
echo "   • API Docs: http://localhost:8000/docs"
echo "   • Health Check: http://localhost:8000/health"
echo "   • Metrics: http://localhost:8000/metrics"
if [ -d "frontend" ]; then
    echo "   • Frontend: http://localhost:3000"
fi
echo ""
echo "🛑 Press Ctrl+C to stop all services"

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Stopping services..."
    kill $BACKEND_PID 2>/dev/null
    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null
    fi
    echo "✅ All services stopped"
    exit 0
}

# Setup signal handlers
trap cleanup SIGINT SIGTERM

# Wait for user to stop
wait
