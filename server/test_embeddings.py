embeddings = [None] * 5
uuids_needing_embeddings = {"B", "D"}
uuids = ["A", "B", "C", "D", "E"]
valid_indices = []
images_to_embed = []
for i, uuid in enumerate(uuids):
    if uuid in uuids_needing_embeddings:
        images_to_embed.append(uuid)
        valid_indices.append(i)

batch_embeddings = [f"emb_{x}" for x in images_to_embed] # Mocking _generate_image_embeddings

for j, idx in enumerate(valid_indices):
    embeddings[idx] = batch_embeddings[j]

print(embeddings)
