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

## Configuring StyleAI

After the model finishes downloading, open Lightroom Classic and navigate to the StyleAI Plugin Manager.

Scroll down to the AI Provider Configuration section. You will find a field labeled Ollama Base URL. 

If Ollama is running on the same computer as Lightroom, you can leave this field at its default value (`http://localhost:11434`). 

If you are running Ollama on a different machine on your local network, enter the IP address and port of that machine (for example, `http://192.168.1.100:11434`). Ensure that you have configured the remote Ollama instance to listen on external network interfaces, as it defaults to localhost only.

With the URL configured, you can close the Plugin Manager. When you open the AI Indexing or AI Edit dialogs, StyleAI will automatically query Ollama and populate the AI Model dropdown with the models you have downloaded.
