#!/bin/bash
# One-click deployment script for ReportAI

set -e

echo "🚀 ReportAI - Production Deployment"
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

# Build and start
echo "🐳 Building and starting containers..."
if [ -f "report-generator/deploy/docker-compose.yml" ]; then
    docker-compose -f report-generator/deploy/docker-compose.yml up -d --build
elif [ -f "deploy/docker-compose.yml" ]; then
    docker-compose -f deploy/docker-compose.yml up -d --build
else
    echo "❌ docker-compose.yml not found."
    exit 1
fi

echo ""
echo "✅ Deployment complete!"
echo "🌐 Open: http://localhost:8000"
echo ""
echo "📊 To view logs:"
echo "   docker-compose -f report-generator/deploy/docker-compose.yml logs -f"
echo ""
echo "🛑 To stop:"
echo "   docker-compose -f report-generator/deploy/docker-compose.yml down"
