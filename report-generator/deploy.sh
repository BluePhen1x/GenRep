#!/bin/bash
# One-click deployment script for GenRep

set -e

echo "🚀 GenRep - Production Deployment"
echo "===================================="

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    exit 1
fi

# Check .env
if [ ! -f .env ]; then
    echo "📝 Creating .env file..."
    cat > .env << EOF
OPENROUTER_API_KEY=your_key_here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
MODEL_NAME=openai/gpt-4o
REDIS_URL=redis://redis:6379/0
OPENMANUS_PATH=/app/OpenManus
EOF
    echo "⚠️ Please edit .env and add your API key!"
    exit 1
fi

# Build and start
echo "🐳 Building and starting containers..."
docker-compose -f deploy/docker-compose.yml up -d --build

echo ""
echo "✅ Deployment complete!"
echo "🌐 Open: http://localhost:8000"
echo ""
echo "📊 To view logs:"
echo "   docker-compose -f deploy/docker-compose.yml logs -f"
echo ""
echo "🛑 To stop:"
echo "   docker-compose -f deploy/docker-compose.yml down"
