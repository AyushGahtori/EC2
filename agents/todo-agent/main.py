import argparse
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8200"))
    print(f"🚀 Starting Todo Agent API on port {port}")
    uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=False)