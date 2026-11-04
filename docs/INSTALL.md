# Install StyleAI for Lightroom Classic

Choose the archive for your computer: `macos-arm64` is for Apple silicon Macs;
`windows-x64` is for 64-bit Intel/AMD Windows. Intel Macs and Windows ARM are
not included in these builds. Lightroom **Classic**, not Lightroom cloud, is
required. The backend is unsigned and macOS builds are not notarized.

1. Quit Lightroom Classic. Download the ZIP from the project's GitHub Releases
   page and extract the entire archive to a permanent folder you can write to,
   for example `Documents/StyleAI`. Do not run it from inside the ZIP.
2. Keep `StyleAI.lrplugin` and the `styleai-server` folder together. Do not move
   or rename either one after adding the plug-in. On macOS, Finder may display
   `StyleAI.lrplugin` as one file; it is a plug-in bundle.
3. Start Lightroom Classic. Choose **File → Plug-in Manager → Add**, select
   `StyleAI.lrplugin`, and enable it. The plug-in starts its backend when needed.
4. In Plug-in Manager, select StyleAI and open **Configure Local Models...**.
   Complete the SigLIP2 setup; its first download needs internet and disk space.
   For generated metadata, also install and configure a compatible local Ollama
   or LM Studio vision model. Learned editing does not require either provider.
5. Select photos and use **File → Plug-in Extras → Prepare Photos...** or
   **Learn From My Edits...** to get started.

If macOS blocks the downloaded unsigned backend, review the downloaded release's
origin, then open Terminal and run the following, replacing the quoted path with
the full path to the extracted `styleai-server` folder (drag the folder into
Terminal to insert its path):

```sh
xattr -dr com.apple.quarantine "/path/to/StyleAI/styleai-server"
```

Restart Lightroom and try again. If Windows SmartScreen blocks the backend,
review the publisher and release source before choosing **More info → Run
anyway**, if offered. Security controls may also quarantine a file; consult your
organization's policy before allowing it. Do not run an unknown executable.

Each catalog must live in its own folder. StyleAI stores its database in
`styleai.db` beside the catalog; the archive does not contain your photos or
catalog. To update, quit Lightroom, keep a backup of your catalog and StyleAI
data, extract the new release into a new permanent folder, and use Plug-in
Manager to remove the old plug-in and add the new one. Retain the old folder
until the new version works. To remove StyleAI, remove it in Plug-in Manager and
delete the extracted application folder; catalog data stays beside the catalog.
Use **Data & Recovery** in Plug-in Manager to export a StyleAI backup.

For troubleshooting, see https://github.com/RareLight/StyleAI/wiki/Troubleshooting.
