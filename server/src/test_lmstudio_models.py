import lmstudio as lms

client = lms.Client("localhost:1234")
try:
    models = client.system.list_downloaded_models()
    for m in models:
        print(f"Model key: {m.model_key}")
        print(f"Type: {m.type}")
        print(f"Dir: {dir(m)}")
        print("---")
except Exception as e:
    print(f"Error: {e}")
