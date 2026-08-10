import json

data = {"tasks": []}

tasks_raw = data.get("tasks")
if tasks_raw:
    print("tasks_raw is truthy")
else:
    print("tasks_raw is falsy")
    tasks = ["embeddings"]

print(tasks)
