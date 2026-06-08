# Setting Up LM Studio with StyleAI

LM Studio is a desktop application that lets you discover, download, and run local LLMs. It features a built-in local inference server that is compatible with the OpenAI API format, making it very easy to integrate with StyleAI for private, offline image analysis.

## Installation

Download the LM Studio installer for your operating system from lmstudio.ai. Run the installer and launch the application.

If you are on an Apple Silicon Mac, LM Studio often provides MLX-optimized builds of models. These run significantly faster for vision workloads than standard GGUF builds, so you should prioritize them when searching for models.

## Downloading a Vision Model

StyleAI requires models that understand images (vision-capable models). Use the search bar in LM Studio to find and download a suitable model. 

For a fast baseline that works well on most hardware, search for `qwen3-vl-4b`. For better description quality at a moderate performance cost, `qwen3-vl-8b` or `gemma3-4b` are solid general-purpose defaults.

When downloading, pay attention to the memory requirements listed in LM Studio. Prefer the largest model that fits comfortably within your system's VRAM or unified memory. Choosing a model that exceeds your memory capacity will cause the system to swap to disk, resulting in extremely slow processing times during indexing batches.

## Starting the Local Server

Once your model is downloaded, navigate to the Local Server tab in LM Studio (usually represented by a double-arrow icon on the left sidebar).

Select the vision model you just downloaded from the dropdown menu at the top. Allow the model a moment to load into memory.

Check the server settings on the right panel. Ensure the server is configured to run on the default port (1234). Click the Start Server button. You should see log output indicating that the server is listening for requests.

If you plan to switch between models frequently, you can enable the just-in-time model loading option in LM Studio's settings, which allows StyleAI to request a specific model to load automatically.

## Configuring StyleAI

Leave LM Studio running in the background and open Lightroom Classic. Navigate to the StyleAI Plugin Manager.

Under the AI Provider Configuration section, locate the LM Studio Base URL field. If LM Studio is running on the same computer, the default value (`http://localhost:1234/v1`) is correct. If you are hosting LM Studio on another computer, replace localhost with the appropriate IP address.

Close the Plugin Manager to save your settings. When you use the StyleAI tools, the plugin will now route vision analysis requests through LM Studio.
