# Install LoFi Logic on iPhone or iPad for free from Windows

This route uses a GitHub-hosted Mac only to compile the app. The resulting IPA
is **unsigned**: it contains no Apple account, certificate, password, device ID,
or provisioning profile. AltStore Classic signs it locally with a free Apple
Account when you install it.

## What you need

- An iPhone or iPad running iOS/iPadOS 17 or newer.
- A Windows 10/11 PC.
- A free GitHub account and a repository containing this project.
- A free Apple Account. It may be a separate account used only for sideloading.
- AltServer and AltStore Classic.

Apple's free provisioning expires after seven days. AltStore can refresh the
app before it expires whenever the Apple device and this PC can reach AltServer over
the same Wi-Fi network (or while connected by USB). No paid Apple Developer
membership is required.

## 1. Build the unsigned IPA on GitHub

1. Open the project repository on GitHub.
2. Select **Actions**.
3. Select **Build unsigned iPhone + iPad IPA** in the left sidebar.
4. Select **Run workflow**, then confirm **Run workflow**.
5. Wait for the **Build AltStore IPA** job to finish with a green check.
6. Open that workflow run and download the
   **LoFiLogic-iOS-unsigned** artifact.
7. Unzip the downloaded artifact. It contains:
   - `LoFiLogic-iOS-unsigned.ipa`
   - `LoFiLogic-iOS-unsigned.ipa.sha256`

The workflow also validates pull requests that change the iPhone/iPad app or its
shared presets. After the workflow is on the repository's default branch,
later release builds can be started manually with **Run workflow**. Every run
uploads an Xcode build log as a separate artifact so first-build problems are
diagnosable from Windows.

## 2. Install AltStore Classic from Windows

Follow AltStore's current Windows instructions:

<https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows>

In summary:

1. Install Apple's desktop downloads of iTunes and iCloud as directed by the
   AltStore guide. Do not substitute the Microsoft Store editions unless you
   also follow AltStore's alternate setup.
2. Install and run AltServer as administrator.
3. Connect the unlocked iPhone or iPad by USB and choose **Trust** when prompted.
4. Enable Wi-Fi sync for the device in iTunes.
5. From the AltServer tray icon, install AltStore on the device.
6. On the device, trust the developer profile if iOS/iPadOS requests it.
7. On iOS 16 or newer, enable **Settings → Privacy & Security → Developer
   Mode** and follow the restart prompt.

AltStore states that the Apple Account credentials supplied to AltServer are
sent to Apple for authentication. A separate free Apple Account can be used if
you prefer not to use the account attached to iCloud on the phone.

## 3. Install LoFi Logic

1. Make `LoFiLogic-iOS-unsigned.ipa` available to the device through Files,
   iCloud Drive, or another local transfer method.
2. Open AltStore on the iPhone or iPad.
3. In **My Apps**, select the **+** button and choose the IPA.
4. Keep AltServer running until installation finishes.
5. Open LoFi Logic from the Home Screen.

## Keeping it working

- Open AltStore while the Apple device and PC are on the same Wi-Fi network and
  AltServer is running. Use **Refresh All** if background refresh has not run.
- Refresh at least once within each seven-day window.
- A free account allows three active sideloaded apps; AltStore itself uses one
  of those slots.
- Rebuilding the IPA is necessary only when LoFi Logic changes. Weekly refresh
  re-signs the already-installed build; it does not rerun GitHub Actions.

Removing an image from a LoFi Logic roll never deletes the original file from
the Files app or connected storage.

## Troubleshooting

- **The manual Run workflow button is missing:** the workflow file must first
  be present on the repository's default branch. A pull request that adds it
  can still build and provide the initial IPA automatically.
- **The workflow is red:** download **LoFiLogic-iOS-build-log** from the
  failed run and inspect `ios-build.log`.
- **AltStore cannot find AltServer:** connect by USB, ensure AltServer is
  running, and allow it through Windows Firewall on private networks.
- **The app no longer opens:** refresh it in AltStore. If the seven-day period
  already elapsed, reinstall/refresh AltStore through AltServer first.
