from pathlib import Path
import sys
import uvicorn

sys.path.insert(0, str(Path(__file__).parent / 'src'))
from open_llm_vtuber.companion.app import create_app

if __name__ == '__main__':
    uvicorn.run(create_app(), host='127.0.0.1', port=12393, ws_max_size=65536)
