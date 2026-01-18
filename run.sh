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

# Ensure CUDA runtime is available for CuPy (libnvrtc.so.12)
if ! ldconfig -p 2>/dev/null | grep -q "libnvrtc.so.12"; then
    echo "CUDA runtime not found (libnvrtc.so.12). Installing..."
    if command -v apt-get > /dev/null; then
        sudo apt-get update
        sudo apt-get install -y libnvrtc12 || sudo apt-get install -y cuda-nvrtc-12-2
    else
        echo "⚠️ apt-get not found; install CUDA 12 runtime manually."
    fi
fi

# Build deps for CuPy source builds (Python headers + toolchain)
if command -v apt-get > /dev/null; then
    if [ ! -f "/usr/include/python3.12/Python.h" ]; then
        echo "Installing Python 3.12 headers and build tools..."
        sudo apt-get update
        sudo apt-get install -y python3.12-dev python3-dev build-essential
    fi
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

# Ensure CUDA libs are on the path for any NVIDIA GPU
export LD_LIBRARY_PATH="/usr/local/cuda/lib64:/usr/local/cuda/targets/$(uname -m)-linux/lib:${LD_LIBRARY_PATH}"

# Helper to sanity-check CuPy on the current GPU
check_cupy() {
python - <<'PY'
import sys
try:
    import cupy as cp
    devs = cp.cuda.runtime.getDeviceCount()
    cp.asarray([1,2,3]).sum().item()
    cp.cuda.Stream.null.synchronize()
    print(f"CUPY_OK devices={devs}")
except Exception as e:
    print(f"CUPY_FAIL {e}")
    sys.exit(1)
PY
}

# If CuPy is broken or missing, fix it; prefer the CUDA 12 wheel, keep only one CuPy package installed.
if ! check_cupy; then
    echo "Fixing CuPy installation for CUDA..."
    pip uninstall -y cupy cupy-cuda12x || true
    pip install -U --no-cache-dir cupy-cuda12x
    if ! check_cupy; then
        echo "CuPy CUDA wheel still failing; falling back to source build (CPU-only if CUDA unavailable)."
        pip uninstall -y cupy-cuda12x || true
        pip install -U pip setuptools wheel numpy
        pip install -v --no-binary=cupy cupy
        check_cupy || echo "⚠️ CuPy still failing; server may run on CPU."
    fi
fi

# Run your app
python main.py
