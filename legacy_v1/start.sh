#!/bin/bash
#
# 海鲜视频混剪与多平台发布系统 - 启动脚本
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
DATA_DIR="$PROJECT_DIR/data"

echo "=========================================="
echo "  海鲜视频混剪与多平台发布系统"
echo "=========================================="
echo ""

# 检查 FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "❌ FFmpeg 未安装，请先安装 FFmpeg"
    echo "   macOS: brew install ffmpeg"
    echo "   Ubuntu: sudo apt-get install ffmpeg"
    exit 1
fi
echo "✅ FFmpeg 已安装: $(ffmpeg -version | head -n1)"

# 创建数据目录
mkdir -p "$DATA_DIR"/{uploads,outputs,temp,db}
echo "✅ 数据目录已就绪"

# 启动后端
echo ""
echo "--- 启动后端服务 (端口 8000) ---"
cd "$BACKEND_DIR"

if [ ! -d "venv" ]; then
    echo "创建 Python 虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

# 启动后端（后台运行）
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$DATA_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "✅ 后端已启动 (PID: $BACKEND_PID)"

# 等待后端启动
sleep 3

# 启动前端
echo ""
echo "--- 启动前端服务 (端口 3000) ---"
cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
    echo "安装前端依赖..."
    npm install
fi

nohup npm run dev > "$DATA_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "✅ 前端已启动 (PID: $FRONTEND_PID)"

echo ""
echo "=========================================="
echo "  系统启动完成！"
echo "=========================================="
echo "  前端: http://localhost:3000"
echo "  API:  http://localhost:8000"
echo "  文档: http://localhost:8000/docs"
echo "=========================================="
echo ""
echo "按 Ctrl+C 停止所有服务"

# 等待中断
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo '服务已停止'; exit 0" INT
wait
