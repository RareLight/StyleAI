import os
import shutil
import tempfile

import config
from services import chroma
from services import training

with tempfile.TemporaryDirectory() as d:
    db_path = os.path.join(d, "styleai.db")
    config.DB_PATH = db_path
    
    chroma._ensure_initialized()
    print("chroma initialized")
    
    # Simulate first training operation
    try:
        training._ensure_initialized()
        print("training initialized")
    except Exception as e:
        print(f"Error: {e}")

print("Done")
