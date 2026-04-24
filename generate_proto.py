"""Generate Python gRPC bindings from proto/chibu_agent.proto."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PROTO_FILE = ROOT / "proto" / "chibu_agent.proto"
OUT_DIR = ROOT / "chibu" / "grpc_server"


def generate() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={ROOT / 'proto'}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        str(PROTO_FILE),
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("STDERR:", result.stderr)
        sys.exit(result.returncode)

    # Fix relative imports in generated files
    pb2_grpc = OUT_DIR / "chibu_agent_pb2_grpc.py"
    if pb2_grpc.exists():
        content = pb2_grpc.read_text()
        content = content.replace(
            "import chibu_agent_pb2",
            "from chibu.grpc_server import chibu_agent_pb2",
        )
        pb2_grpc.write_text(content)

    print("Proto generation complete.")
    print(f"  → {OUT_DIR / 'chibu_agent_pb2.py'}")
    print(f"  → {OUT_DIR / 'chibu_agent_pb2_grpc.py'}")


if __name__ == "__main__":
    generate()
