# Install StyleAI for Lightroom Classic

Choose the archive for your computer: `macos-arm64` is for Apple silicon Macs;
`windows-x64` is for 64-bit Intel/AMD Windows. Intel Macs and Windows ARM are
not included in these builds. Lightroom **Classic**, not Lightroom cloud, is
required. The macOS backend needs either Apple notarization or your approval
for that build in macOS. The Windows backend is unsigned.

Use the complete platform ZIP for both installation and upgrades. Lightroom
starts the service from the `styleai-server` folder beside the plug-in.

1. Quit Lightroom Classic. Download the ZIP from the project's GitHub Releases
   page and extract the entire archive to a permanent folder you can write to,
   for example `Documents/StyleAI`. Do not run it from inside the ZIP. A locally
   built ZIP uses the same layout as a GitHub release.
2. Keep `StyleAI.lrplugin` and the `styleai-server` folder together. Do not move
   or rename either one after adding the plug-in. On macOS, Finder may display
   `StyleAI.lrplugin` as one file; it is a plug-in bundle.
3. Start Lightroom Classic. Choose **File → Plug-in Manager → Add**, select
   `StyleAI.lrplugin`, and enable it. The plug-in starts its backend when needed.
4. In Plug-in Manager, select StyleAI and open **Configure Local Models...**.
   Setup checks for SigLIP2 in your existing Hugging Face cache. If the
   model is missing, download it here and allow at least 12 GB free on the
   model-cache drive. The full snapshot is about 9.1 GB.
5. For **Learn From My Edits**, install [ExifTool](https://exiftool.org/)
   separately so the backend can extract embedded RAW/DNG previews. Make sure
   `exiftool -ver` prints a version. On Windows, put `exiftool.exe` on `PATH`
   before starting Lightroom; on macOS the backend also checks
   `/opt/homebrew/bin` and `/usr/local/bin`.
6. For generated metadata, choose Ollama, LM Studio, or AX Engine in
   **Status & Setup → Local metadata API** in Plug-in Manager. For AX Engine,
   set the **AX model folder** there. Learned editing does not require a local
   language model.
7. Select photos and use **File → Plug-in Extras → Prepare Photos...** or
   **Learn From My Edits...** to get started.

## Upgrade an existing installation

1. In Plug-in Manager, find the current path to `StyleAI.lrplugin`. Extract
   the new ZIP to a separate folder. Keep a catalog backup and use **Data &
   Recovery** in Plug-in Manager to export a StyleAI backup if needed.
2. Quit Lightroom Classic. If the plug-in and backend already live in a
   dedicated release folder, rename that folder to keep it as a backup.
   Put the newly extracted folder at the original path. This keeps Lightroom's
   registered plug-in path and replaces the plug-in and backend together.
   For an older installation with the plug-in and backend in separate locations,
   add the new `StyleAI.lrplugin` in Plug-in Manager and disable the old entry.
3. Reopen the same Lightroom catalog. In **Plug-in Manager → StyleAI**,
   check that the background service and vision model show **Ready**.
   Use **Repair Background Service** if the service needs another start attempt.

StyleAI opens the same `styleai.db` beside the active catalog. Keep each
catalog in its own folder. The release folder contains only application files.
SigLIP2 is shared through your user model cache and is reused when its model
files are already present. Ollama, LM Studio, and AX Engine keep their own
weights in their configured locations.

## If the service is blocked

On macOS, after Gatekeeper blocks `styleai-server`, verify that you trust the
release. In **System Settings → Privacy & Security**, choose **Open Anyway**
for `styleai-server` if offered. Then retry from **Plug-in Manager → StyleAI →
Repair Background Service**. [Apple's Gatekeeper instructions](https://support.apple.com/en-us/102445)

For a trusted ZIP built on your Mac, you can extract it directly to a new
staging folder with macOS's built-in tool. Then follow the in-place upgrade
steps above with the whole staged folder.

```sh
ditto -x -k "/path/to/your/local-build.zip" "/path/to/new/StyleAI"
```

On Windows, verify the publisher and release source before choosing **More
info → Run anyway** if SmartScreen offers it. If the service still fails,
check that the complete `styleai-server` folder is beside the plug-in and
that port `127.0.0.1:19819` is free.

## Sign a local macOS build

Use `security find-identity -v -p codesigning` to find the Apple Development
identity created by Xcode. From the repository, use its identifier to sign
the frozen backend and its native libraries.

```sh
STYLEAI_MACOS_CODESIGN_IDENTITY="<identity hash>" bash scripts/build-local-release.sh
```

Apple Development signing is for local development. Gatekeeper's standard
approval for downloaded software requires Developer ID signing and
notarization. Extract your own local build directly from the build output
using the command above. [Apple's Developer ID guide](https://developer.apple.com/developer-id/)

## Build a notarized macOS ZIP

For distribution, build with a Developer ID Application certificate in
Keychain and a profile created with `xcrun notarytool store-credentials`.
Set `STYLEAI_MACOS_CODESIGN_IDENTITY` to the certificate identity and
`STYLEAI_MACOS_NOTARY_PROFILE` to the profile name, then run
`bash scripts/build-local-release.sh`. The build signs the frozen backend
with hardened runtime and releases the ZIP locally after Apple accepts that
archive. Gatekeeper can retrieve the notarization ticket online. The GitHub
workflow currently creates unnotarized artifacts. [Apple's notarization guide](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

For troubleshooting, see https://github.com/RareLight/StyleAI/wiki/Troubleshooting.
