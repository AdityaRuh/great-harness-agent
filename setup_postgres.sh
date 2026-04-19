#!/bin/bash
# Great Harness Agent — PostgreSQL Setup
# Run this ONCE on your Mac to set up the database

echo "=== Great Harness Agent — PostgreSQL Setup ==="

# Check if PostgreSQL is installed
if ! command -v psql &> /dev/null; then
    echo "PostgreSQL not found. Installing via Homebrew..."
    brew install postgresql@16
    brew services start postgresql@16
    echo "PostgreSQL installed and started"
else
    echo "PostgreSQL found: $(psql --version)"
fi

# Create database and user
echo "Creating database 'harness'..."
createdb harness 2>/dev/null || echo "Database 'harness' already exists"

# Test connection
psql -d harness -c "SELECT 1 AS connected;" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ Database connection successful"
    
    # Add DATABASE_URL to .env
    DB_URL="postgresql://$(whoami)@localhost:5432/harness"
    if grep -q "DATABASE_URL" .env 2>/dev/null; then
        echo "DATABASE_URL already in .env"
    else
        echo "" >> .env
        echo "# PostgreSQL" >> .env
        echo "DATABASE_URL=$DB_URL" >> .env
        echo "✓ Added DATABASE_URL to .env: $DB_URL"
    fi
    
    echo ""
    echo "=== Setup complete! ==="
    echo "Database URL: $DB_URL"
    echo ""
    echo "Start the server with:"
    echo "  python -m uvicorn app.main:app --port 8000"
    echo ""
    echo "For multi-worker (recommended):"
    echo "  python -m uvicorn app.main:app --port 8000 --workers 2"
else
    echo "✗ Database connection failed"
    echo "Make sure PostgreSQL is running: brew services start postgresql@16"
fi
