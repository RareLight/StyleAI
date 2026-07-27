# Setting Up Ollama with StyleAI

Ollama is a command-line tool that runs open-weights large language models locally on your own hardware. With StyleAI it provides privacy-focused, offline AI tagging and image analysis.

## Installation

Begin by downloading the Ollama application for your operating system from the official website at ollama.com. Follow the standard installation wizard for Windows or macOS. If you are using Linux, the website provides a single installation command you can run in your terminal.

Once installed, verify the service is running. On macOS and Windows, look for the Ollama icon in your system tray or menu bar. On Linux, ensure the systemd service is active.

## Downloading a Vision Model

StyleAI relies on vision-capable models to understand the content of your photos. You will need to download at least one vision model using your terminal or command prompt.

Open your terminal application and execute a pull command for a supported model. For most systems with average graphics hardware, the Qwen3-VL 4B model provides an excellent balance of speed and accuracy. 

Type the following command and press enter:

`ollama pull qwen3-vl:4b-instruct-q4_K_M`

If your system has more than 10GB of Video RAM, you might consider larger models for improved nuance in image descriptions, such as the 8B variant of Qwen3 or the Gemma3 models. The initial download may take several minutes depending on your internet connection speed. You can view the full directory of available vision models on the Ollama website.

## Using Ollama with StyleAI

After the model finishes downloading, leave Ollama running and open Lightroom
Classic. StyleAI detects the local service automatically.

StyleAI intentionally connects only to Ollama on the same Mac at
`http://127.0.0.1:11434`. Remote hosts and LAN model servers are not supported,
so catalog images and metadata cannot leave the machine through provider
configuration.

When you open the AI Indexing or AI Edit dialogs, StyleAI queries the loopback
Ollama service and populates the AI Model dropdown with downloaded models.
