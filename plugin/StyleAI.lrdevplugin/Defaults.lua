Defaults = {}

Defaults.defaultTopLevelKeyword = "StyleAI"

Defaults.defaultPromptName = "Default"
Defaults.defaultEditPromptName = "Default"

Defaults.defaultTopLevelKeywords = {
	"StyleAI",
	"Ollama",
	"LM Studio",
}

Defaults.topLevelKeywordSynonym = "StyleAI Top-Level Keyword"

Defaults.defaultGenerateLanguage = "English"

Defaults.generateLanguages = { "English", "German", "French", "Spanish", "Italian", "Norwegian" }
Defaults.defaultBilingualKeywords = false
Defaults.defaultKeywordSecondaryLanguage = "English"
Defaults.defaultKeywordAliases = false

Defaults.defaultTemperature = 0.1
Defaults.defaultMaxTokens = 2048

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

Defaults.exportSizes = {
	"512",
	"1024",
	"2048",
	"3072",
	"4096",
}

Defaults.defaultOllamaBaseUrl = "http://localhost:11434"
Defaults.defaultLmStudioBaseUrl = "localhost:1234"

Defaults.defaultBackendServerUrl = "http://127.0.0.1:19819"

Defaults.defaultExportQuality = 50
Defaults.defaultExportSize = "3072"

Defaults.legacySystemInstructions = {
	"You are a professional photography analyst with expertise in object recognition and computer-generated image description. You also try to identify famous buildings and landmarks as well as the location where the photo was taken. Furthermore, you aim to specify animal and plant species as accurately as possible. You also describe objects—such as vehicle types and manufacturers—as specifically as you can.",
	"You are a professional photography analyst with expertise in image recognition, scene analysis, and digital image metadata. You add clear and standardized keywords that describe the contents of the image.\n\nYour primary focus is accurately classifying nature, landscapes, macro photography, portraits (including pets), family gatherings, and candid event moments. Analyze the image and provide precise, descriptive tags according to the following priority hierarchy:\n\n1. Location & Scenery: Identify the geographic setting, biome (e.g., dense forest, coastal, alpine), and prominent landscape features. \n2. Genre & Mood: Specify the style of photography (e.g., macro, candid portrait, landscape) and the overall emotional tone or atmosphere.\n3. Lighting & Weather: Describe the lighting conditions (e.g., golden hour, dappled sunlight, silhouette, flash) and any visible weather phenomena (e.g., fog, rain, overcast).\n4. Activities: Note any specific actions taking place, ceremonies, family gatherings, or contextual event moments.\n5. Subjects (People, Animals, Plants): \n   - For people: describe their roles, expressions, or the type of portrait.\n   - For animals (especially pets) and plants/macro subjects: be as specific as possible regarding the species, breed, or taxonomy if identifiable.\n6. Objects, Buildings, Vehicles, & Text: Detail significant architectural landmarks, vehicle types, prominent props, or readable text that strongly contributes to the image's context.\n\nBe highly specific and objective. Avoid generic filler words. Ensure your outputs strictly map to these concepts.",
	"You are a professional photography analyst with expertise in image recognition, scene analysis, and digital image metadata. You add clear and standardized keywords that describe the contents of the image.\n\nYour primary focus is accurately classifying nature, landscapes, macro photography, portraits (including pets), family gatherings, and candid event moments. Analyze the image and provide precise, descriptive tags according to the following priority hierarchy:\n\n1. Location & Scenery: Identify the geographic setting, biome (e.g., dense forest, coastal, alpine), and prominent landscape features. \n2. Genre & Mood: Specify the style of photography (e.g., macro, candid portrait, landscape) and the overall emotional tone or atmosphere. Ensure genre keywords are explicitly included (e.g., Portrait, Environmental Portrait, Landscape).\n3. Lighting & Weather: Describe the lighting conditions, specifying the primary light source (e.g., natural window light, direct sun, flash) and any visible weather phenomena (e.g., fog, rain, overcast).\n4. Activities: Note any specific actions taking place, ceremonies, family gatherings, or contextual event moments.\n5. Subjects (People, Animals, Plants): \n   - For people: describe their roles, expressions, or the type of portrait.\n   - For animals (especially pets) and plants/macro subjects: be as specific as possible regarding the species, breed, or taxonomy if identifiable.\n6. Objects, Buildings, Vehicles, & Text: Detail significant architectural landmarks, vehicle types, prominent props, or readable text that strongly contributes to the image's context.\n\nBe highly specific and objective. Avoid generic filler words. Format all keywords in Title Case. Do not repeat terms. Do not use special characters other than commas. Ensure your outputs strictly map to these concepts.",
	"You are an expert photography analyst. Output clear, standardized keywords describing the image.\n\nFocus on: nature, landscapes, macro, portraits (incl. pets), family gatherings, candids.\n\nAnalyze and tag based on this priority:\n1. Location/Scenery: Geographic setting, biome, landscape features.\n2. Genre/Mood: Explicitly include genre (e.g., Portrait, Environmental Portrait, Landscape) and emotional tone.\n3. Lighting/Weather: Primary light source (e.g., window light, direct sun, flash) and weather.\n4. Activities: Actions, ceremonies, event moments.\n5. Subjects: People (roles, expressions), animals/plants (specific species/breeds).\n6. Objects/Context: Landmarks, vehicles, prominent props, readable text.\n\nRules:\n- Be specific and objective. No generic filler.\n- Format in Title Case.\n- No duplicate terms.\n- No special characters (commas only)."
}
Defaults.defaultSystemInstruction =
	"You are an expert photography analyst. Output clear, standardized keywords describing the image.\n\nFocus on: nature, landscapes, macro, portraits (incl. pets), family gatherings, candids.\n\nAnalyze and tag based on this priority:\n1. Location/Scenery: Geographic setting, biome, landscape features.\n2. Subjects: People (roles, expressions), animals/plants (specific species/breeds).\n3. Genre/Mood: Explicitly include genre (e.g., Macro, Nature, Portrait, Landscape, etc.) and emotional tone.\n4. Lighting/Weather: Primary light source (e.g., window light, direct sun, flash), light quality, and weather.\n5. Activities: Actions, ceremonies, event moments.\n6. Objects/Context: Landmarks, vehicles, prominent props, readable text.\n\nRules:\n- Be specific and objective. No generic filler.\n- Format in Title Case.\n- No duplicate terms.\n- No special characters (commas only)."

Defaults.legacyEditSystemInstructions = {
	"You are a senior Lightroom Classic retoucher. Return only a structured Lightroom edit recipe that matches the schema exactly. No prose, no markdown, no unsupported controls. Build edits in this order: white balance and exposure foundation, tonal shaping, color refinement, detail/effects. Use masks only when materially beneficial and only for subject, sky, or background. Prefer subtle, natural, premium output unless explicitly asked for a stylized look."
}
Defaults.defaultEditSystemInstruction =
	"You are a senior Lightroom Classic retoucher. You are generating a complete edit recipe from scratch based on the user's creative intent. Return only a structured Lightroom edit recipe matching the schema exactly. No prose, no markdown, no unsupported controls. Build edits in this order: white balance and exposure foundation, tonal shaping, color refinement, detail/effects. Use masks only when materially beneficial and only for subject, sky, or background. Provide balanced, natural edits unless a highly stylized look is requested."
Defaults.defaultEditIntent = "Natural professional Lightroom edit"
Defaults.editIntentCustomValue = "custom"
Defaults.defaultEditIntentPresetValue = "natural_pro"
Defaults.editIntentPresets = {
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/NaturalPro=General - Natural Professional"),
		value = "natural_pro",
		instruction = "Natural professional Lightroom edit with balanced contrast, realistic color, and clean detail.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/MoodyDramatic=General - Moody Dramatic"),
		value = "moody_dramatic",
		instruction = "Moody dramatic treatment with deeper shadows, restrained saturation, and cinematic tonal separation while preserving realism.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/CinematicLandscape=Landscape - Cinematic"),
		value = "cinematic_landscape",
		instruction = "Cinematic landscape look with controlled dynamic range, subtle color contrast, and tasteful depth without overprocessing.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/VibrantNaturalLandscape=Landscape - Vibrant Natural"),
		value = "landscape_vibrant_natural",
		instruction = "Vibrant but natural landscape look with clear tonal separation, protected highlights, and controlled saturation.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/SkinSafePortrait=Portrait - Skin Safe"),
		value = "portrait_skin_safe",
		instruction = "Portrait-focused edit with skin-tone safety, gentle contrast, natural texture, and flattering highlights.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/EditorialPortrait=Portrait - Editorial"),
		value = "portrait_editorial",
		instruction = "Editorial portrait style with clean skin tones, polished midtone contrast, soft highlight roll-off, and restrained color shifts.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/SoftAiryWedding=Wedding - Soft Airy"),
		value = "wedding_soft_airy",
		instruction = "Soft airy wedding style with bright mids, warm-neutral white balance, gentle contrast, and elegant highlight rendering.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/RichFilmicWedding=Wedding - Rich Filmic"),
		value = "wedding_rich_filmic",
		instruction = "Rich filmic wedding style with subtle warm skin tones, gentle black-point lift, and cinematic but natural color depth.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/BrightNeutralRealEstate=Real Estate - Bright Neutral"),
		value = "real_estate_bright_neutral",
		instruction = "Real-estate edit with bright neutral interiors, straight tonal balance, clean whites, and minimal stylization.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/CleanCommercial=Commercial - Clean Product"),
		value = "clean_commercial",
		instruction = "Clean commercial look: neutral white balance, crisp detail, controlled contrast, and true-to-product colors.",
	},
	{
		title = LOC("$$$/StyleAI/Defaults/EditIntent/PunchyDocumentaryStreet=Street - Punchy Documentary"),
		value = "street_punchy_doc",
		instruction = "Punchy documentary street look with decisive contrast, neutral color fidelity, and clear subject separation.",
	},
	{ title = LOC("$$$/StyleAI/Defaults/EditIntent/Custom=Custom"), value = "custom", instruction = "" },
}
Defaults.editStyleStrengths = {
	{ title = LOC("$$$/StyleAI/Defaults/Strength/Min=Min"), value = 0.50 },
	{ title = LOC("$$$/StyleAI/Defaults/Strength/Low=Low"), value = 0.75 },
	{ title = LOC("$$$/StyleAI/Defaults/Strength/Normal=Normal"), value = 1.0 },
	{ title = LOC("$$$/StyleAI/Defaults/Strength/High=High"), value = 1.25 },
	{ title = LOC("$$$/StyleAI/Defaults/Strength/Max=Max"), value = 1.50 },
}
Defaults.defaultEditStyleStrength = 1.0
Defaults.defaultCompositionMode = "subtle"
Defaults.compositionModes = {
	{ title = LOC("$$$/StyleAI/Defaults/CompositionMode/None=No crop"), value = "none" },
	{ title = LOC("$$$/StyleAI/Defaults/CompositionMode/Subtle=Subtle crop"), value = "subtle" },
	{ title = LOC("$$$/StyleAI/Defaults/CompositionMode/Aggressive=Aggressive crop"), value = "aggressive" },
}

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
	{ name = "Google GenAI SDK", author = "Google", url = "https://ai.google.dev/" },
	{ name = "OpenAI SDK", author = "OpenAI", url = "https://github.com/openai/openai-python" },
	{ name = "Ollama SDK", author = "Ollama", url = "https://github.com/ollama/ollama-python" },
	{ name = "LM Studio SDK", author = "LM Studio", url = "https://lmstudio.ai/" },
	{ name = "InsightFace", author = "DeepInsight", url = "https://github.com/deepinsight/insightface" },
	{ name = "ONNX Runtime", author = "Microsoft", url = "https://onnxruntime.ai/" },
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
