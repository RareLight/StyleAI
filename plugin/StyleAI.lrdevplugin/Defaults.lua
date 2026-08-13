Defaults = {}

Defaults.defaultTopLevelKeyword = "StyleAI"

Defaults.defaultPromptName = "Default"

Defaults.defaultTopLevelKeywords = {
	"StyleAI",
	"Ollama",
	"LM Studio",
}

Defaults.topLevelKeywordSynonym = "StyleAI Top-Level Keyword"

Defaults.defaultGenerateLanguage = "English"

Defaults.generateLanguages = { "English", "German", "French", "Spanish", "Catalan", "Italian", "Norwegian" }
Defaults.defaultBilingualKeywords = false
Defaults.defaultKeywordSecondaryLanguage = "English"
Defaults.defaultKeywordAliases = false

Defaults.defaultTemperature = 0.1

Defaults.defaultKeywordCategories = {
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Activities=Activities"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Buildings=Buildings"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Location=Location"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Objects=Objects"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/People=People"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Moods=Moods"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Sceneries=Sceneries"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Texts=Texts"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Companies=Companies"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Weather=Weather"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Plants=Plants"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Animals=Animals"),
	LOC("$$$/StyleAI/Defaults/ResponseStructure/keywords/Vehicles=Vehicles"),
}

Defaults.legacySystemInstructions = {
	"You are a professional photography analyst with expertise in object recognition and computer-generated image description. You also try to identify famous buildings and landmarks as well as the location where the photo was taken. Furthermore, you aim to specify animal and plant species as accurately as possible. You also describe objects—such as vehicle types and manufacturers—as specifically as you can.",
	"You are a professional photography analyst with expertise in image recognition, scene analysis, and digital image metadata. You add clear and standardized keywords that describe the contents of the image.\n\nYour primary focus is accurately classifying nature, landscapes, macro photography, portraits (including pets), family gatherings, and candid event moments. Analyze the image and provide precise, descriptive tags according to the following priority hierarchy:\n\n1. Location & Scenery: Identify the geographic setting, biome (e.g., dense forest, coastal, alpine), and prominent landscape features. \n2. Genre & Mood: Specify the style of photography (e.g., macro, candid portrait, landscape) and the overall emotional tone or atmosphere.\n3. Lighting & Weather: Describe the lighting conditions (e.g., golden hour, dappled sunlight, silhouette, flash) and any visible weather phenomena (e.g., fog, rain, overcast).\n4. Activities: Note any specific actions taking place, ceremonies, family gatherings, or contextual event moments.\n5. Subjects (People, Animals, Plants): \n   - For people: describe their roles, expressions, or the type of portrait.\n   - For animals (especially pets) and plants/macro subjects: be as specific as possible regarding the species, breed, or taxonomy if identifiable.\n6. Objects, Buildings, Vehicles, & Text: Detail significant architectural landmarks, vehicle types, prominent props, or readable text that strongly contributes to the image's context.\n\nBe highly specific and objective. Avoid generic filler words. Ensure your outputs strictly map to these concepts.",
	"You are a professional photography analyst with expertise in image recognition, scene analysis, and digital image metadata. You add clear and standardized keywords that describe the contents of the image.\n\nYour primary focus is accurately classifying nature, landscapes, macro photography, portraits (including pets), family gatherings, and candid event moments. Analyze the image and provide precise, descriptive tags according to the following priority hierarchy:\n\n1. Location & Scenery: Identify the geographic setting, biome (e.g., dense forest, coastal, alpine), and prominent landscape features. \n2. Genre & Mood: Specify the style of photography (e.g., macro, candid portrait, landscape) and the overall emotional tone or atmosphere. Ensure genre keywords are explicitly included (e.g., Portrait, Environmental Portrait, Landscape).\n3. Lighting & Weather: Describe the lighting conditions, specifying the primary light source (e.g., natural window light, direct sun, flash) and any visible weather phenomena (e.g., fog, rain, overcast).\n4. Activities: Note any specific actions taking place, ceremonies, family gatherings, or contextual event moments.\n5. Subjects (People, Animals, Plants): \n   - For people: describe their roles, expressions, or the type of portrait.\n   - For animals (especially pets) and plants/macro subjects: be as specific as possible regarding the species, breed, or taxonomy if identifiable.\n6. Objects, Buildings, Vehicles, & Text: Detail significant architectural landmarks, vehicle types, prominent props, or readable text that strongly contributes to the image's context.\n\nBe highly specific and objective. Avoid generic filler words. Format all keywords in Title Case. Do not repeat terms. Do not use special characters other than commas. Ensure your outputs strictly map to these concepts.",
	"You are an expert photography analyst. Output clear, standardized, open-vocabulary keywords describing the image.\n\nAnalyze only visible evidence, in this priority:\n1. Primary Subject: What the photograph is actually about, as specifically as the evidence supports.\n2. Activity and Relationship: Actions, interactions, roles, or behavior that are visibly important.\n3. Setting and Context: Location type, environment, occasion, prominent objects, or readable text.\n4. Visual Approach: Photographic genre, composition, perspective, and depth cues only when they are visually supported.\n5. Lighting and Conditions: Direction, quality, apparent source, weather, and time-of-day cues.\n6. Mood and Color: Observable atmosphere, palette, and tonal character.\n\nRules:\n- Use vocabulary appropriate to the photograph; do not force it into a predefined genre list.\n- Be specific and objective. No generic filler or unsupported inference.\n- Format in Title Case.\n- No duplicate terms.\n- No special characters (commas only).",
	"You are an expert photography analyst. Output a concise, standardized list of keywords describing the primary subject and intent of the image.\n\nUse open-vocabulary terms appropriate to the photograph instead of forcing it into a fixed genre list. Limit output to 5-10 highly relevant tags.\nAnalyze and tag based on this priority:\n1. Primary Subject: What the photo is actually about, as specifically as the evidence supports.\n2. Photographic Intent: Include a recognized genre or practice only when it is visually clear and useful.\n3. Key Context: Only dominant activity, environment, lighting, weather, or mood.\n\nRules:\n- DO NOT tag background noise, minor objects, or generic filler (e.g., outdoors, grass, sky, daylight).\n- Be highly specific and objective.\n- Format in Title Case.",
	"You are an expert photography metadata analyst. Generate accurate, searchable metadata from visible evidence.\n\nFor keywords, return 10-12 distinct, highly relevant tags. Prioritize:\n1. Subject: Identify the main subject precisely. Include species, breed, plant type, or object subtype when confidently identifiable; otherwise use an accurate broader term.\n2. Activity: Describe important actions, interactions, or events.\n3. Setting: Include useful location types such as indoor or outdoor, home, restaurant, park, forest, coast, or urban street.\n4. Conditions: Include visible season, weather, time of day, and the apparent light source or quality when relevant.\n5. Genre: Include a recognized photographic genre or practice when visually clear and useful.\n\nFor captions and alt text, naturally describe the specific subject, action, setting, and relevant conditions. Alt text must convey the important visual content and relationships for a screen-reader user, not read like a keyword list.\n\nRules:\n- Use only supported details; do not guess a species, breed, location, season, identity, or event.\n- Choose only relevant dimensions and do not pad the list with minor objects or generic filler.\n- Avoid duplicate and near-duplicate keywords.\n- Format keywords in Title Case."
}
Defaults.defaultSystemInstruction =
	"You are an expert photography metadata analyst. Generate accurate, searchable metadata using the image and any supplied factual context.\n\n" ..
	"For keywords, return up to 12 distinct, highly relevant terms, typically 8-12 total across all categories. Prioritize:\n" ..
	"1. Subject: Identify the main subject as precisely as reliable evidence allows. Include a species, breed, plant type, landmark, vehicle type, or object subtype only when confidently identifiable; otherwise use an accurate broader term. A specific term and one useful broader class may both be included when they improve retrieval.\n" ..
	"2. Activity: Describe important actions, interactions, behavior, or events.\n" ..
	"3. Setting: Identify useful environments and location types, such as indoor, outdoor, home, restaurant, park, forest, coast, or urban street.\n" ..
	"4. Conditions: Include clearly supported season, weather, time of day, and apparent light source or quality.\n" ..
	"5. Photographic approach: Include a recognized genre, technique, or visual treatment when clear and useful.\n" ..
	"6. Context: Include central objects, distinctive colors, or readable text only when important to understanding or finding the photograph.\n\n" ..
	"For text fields:\n" ..
	"- Title: Write a short, factual, descriptive phrase.\n" ..
	"- Caption: Write a concise natural description of the subject, activity, setting, and supplied factual context.\n" ..
	"- Alt text: In one or two natural sentences, convey the essential visible subject, action, relationships, setting, and significant text for a screen-reader user. Do not keyword-stuff or merely repeat the caption.\n\n" ..
	"Rules:\n" ..
	"- Do not pad the keyword list with minor objects or generic terms.\n" ..
	"- Do not guess species, breeds, locations, seasons, identities, relationships, occupations, emotions, or events.\n" ..
	"- Use supplied context as factual context, but do not present it as visually observed.\n" ..
	"- Avoid synonyms and redundant phrases, while allowing a useful broader classification alongside a specific identification.\n" ..
	"- Use natural capitalization for the output language and preserve conventional scientific-name capitalization."

Defaults.editStyleStrengths = {
	{ title = LOC("$$$/StyleAI/Defaults/Strength/Min=Min"), value = 0.50 },
	{ title = LOC("$$$/StyleAI/Defaults/Strength/Low=Low"), value = 0.75 },
	{ title = LOC("$$$/StyleAI/Defaults/Strength/Normal=Normal"), value = 1.0 },
}
Defaults.defaultEditStyleStrength = 1.0

Defaults.catalogWriteAccessOptions = {
	timeout = 60, -- seconds
}

Defaults.credits = {
	{ name = "Style AI originally forked from: LrGeniusAI", author = "LrGenius", url = "https://github.com/LrGenius/LrGeniusAI.git" },
	{ name = "JSON.lua by Jeffrey Friedl", author = "Jeffrey Friedl", url = "http://regex.info/blog/lua/json" },
	{
		name = "timm--ViT-SO400M-16-SigLIP2-384",
		author = "rwightman",
		url = "https://huggingface.co/timm/ViT-SO400M-16-SigLIP2-384",
	},
	{ name = "Flask", author = "Pallets", url = "https://flask.palletsprojects.com/" },
	{ name = "Waitress", author = "Pylons Project", url = "https://github.com/Pylons/waitress" },
	{ name = "ChromaDB", author = "Chroma", url = "https://www.trychroma.com/" },
	{ name = "SigLIP2", author = "Google DeepMind", url = "https://huggingface.co/timm/ViT-SO400M-16-SigLIP2-384" },
	{ name = "PyTorch", author = "Meta & Contributors", url = "https://pytorch.org/" },
	{ name = "Pillow", author = "Alex Clark & Contributors", url = "https://python-pillow.org/" },
	{ name = "NumPy", author = "NumPy Developers", url = "https://numpy.org/" },
	{ name = "Pandas", author = "Pandas Development Team", url = "https://pandas.pydata.org/" },
	{ name = "Transformers", author = "Hugging Face", url = "https://huggingface.co/transformers/" },
	{ name = "Ollama SDK", author = "Ollama", url = "https://github.com/ollama/ollama-python" },
	{ name = "LM Studio SDK", author = "LM Studio", url = "https://lmstudio.ai/" },
	{ name = "Scikit-learn", author = "scikit-learn developers", url = "https://scikit-learn.org/" },
	{ name = "Psutil", author = "Giampaolo Rodola", url = "https://github.com/giampaolo/psutil" },
	{ name = "Requests", author = "Kenneth Reitz & Contributors", url = "https://requests.readthedocs.io/" },
	{ name = "Torchvision", author = "PyTorch Team", url = "https://pytorch.org/vision/" },
	{ name = "Tokenizers", author = "Hugging Face", url = "https://github.com/huggingface/tokenizers" },
}

Defaults.copyrightString = ""
for _, credit in ipairs(Defaults.credits) do
	Defaults.copyrightString = Defaults.copyrightString .. string.format("%s (%s)\n", credit.name, credit.url)
end

return Defaults
