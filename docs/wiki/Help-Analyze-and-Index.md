# Guide to AI Indexing and Auto-Tagging

Prepare Photos powers the semantic-search and optional local-metadata capabilities of StyleAI. It can create local visual analysis, generate descriptions with a selected local model, or do both. Preparing photos separately is **not** required before learning from edited training photos.

## Accessing the Tool

Open Lightroom Classic. Select the photos you wish to process in the Library module, then navigate to the top menu bar. Go to **Library > Plug-in Extras > AI Index & Auto-Tag Photos...**

## Workflow Modes

When the dialog opens, the first option you will see is the Workflow Mode. This determines what kind of data the plugin will generate and limits the configuration options to only what you need.

*   **AI Search Embedding Only**: This mode runs the fast SigLIP2 vision model to create a mathematical representation of your image. This enables powerful semantic search capabilities (finding photos by describing them naturally) without the slower, text-generation step of a Large Language Model.
*   **AI Auto-Tagging/Metadata Only**: This mode skips the semantic search embedding and exclusively uses your configured AI language model to write keywords, titles, captions, and alternative text directly into your photo metadata.
*   **Complete Package (Both)**: This runs both models sequentially. It provides the full capabilities of StyleAI, enabling both semantic search and rich text metadata generation.

You can also specify the scope of the operation, such as processing only your selected photos or scanning the entire catalog for new, unprocessed images.

## General Settings

In the General tab, you verify your models and control the AI text generation. 

For the search embeddings, the plugin will indicate whether the SigLIP2 model is cached and ready.

For the auto-tagging, you will select your preferred AI language model from the dropdown. This list is populated by the providers you configured in the Plugin Manager. You can also adjust the temperature setting. A lower temperature produces more factual, rigid descriptions, while a higher temperature allows the AI to be more creative and subjective.

## Keywords & Metadata

The Keywords tab allows you to toggle exactly which Lightroom metadata fields the AI should overwrite or append. 

If you enable keyword generation, you can also control the structural hierarchy. Enabling keyword hierarchy organizes the tags logically (for example, grouping "oak" and "pine" under a "trees" parent category). You can even instruct the AI to respect your existing Lightroom keyword structure to avoid duplicating tags with slight variations.

## Prompt & Context

The Prompt tab is where you instruct the AI on exactly how to describe your images. You can select a pre-saved prompt template from the dropdown or write custom instructions directly in the text field.

The context options determine what existing data is fed to the AI alongside the image. Sending GPS coordinates, folder names, or existing keywords can significantly improve the accuracy of the generated descriptions by giving the model a real-world anchor for its analysis. 

If you enable the photo context dialog, StyleAI will pause before processing each image to ask you for specific, manual context.

## Advanced Maintenance

The Advanced tab controls how the generated data interacts with your Lightroom catalog. You can choose whether to overwrite existing metadata, append to it, or skip photos that already have AI data. You can also enforce a manual review step, where the plugin presents the AI's suggestions and asks for your approval before writing anything to the Lightroom database.
