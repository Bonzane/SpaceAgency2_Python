#!/bin/bash
# filepath: run.sh

# Function to extract a port value from config.txt
get_port() {
    grep "$1" config.txt | awk '{ print $2 }'
}

# Read ports from config.txt
CONTROL_PORT=$(get_port "server_settings.control_port")
STREAMING_PORT=$(get_port "server_settings.streaming_port")

echo "Opening ports:"
echo " - TCP port: $CONTROL_PORT"
echo " - UDP port: $STREAMING_PORT"

# Use ufw to open the ports (requires sudo and ufw enabled)
if command -v ufw > /dev/null; then
    echo "Using ufw to open ports..."
    sudo ufw allow $CONTROL_PORT/tcp
    sudo ufw allow $STREAMING_PORT/udp
else
    echo "⚠️ 'ufw' not found; skipping port opening. Please open ports manually if needed."
fi

# Set up virtual environment if missing
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# Run your app
python main.py
