# 🐰 KariPom Desktop

[日本語 README](README.md)

https://github.com/user-attachments/assets/52b32f2c-2d1d-4204-99f6-da0b9c77525a

**A character on your screen lip-syncs in real time to ChatGPT's voice!**

KariPom Desktop analyzes audio playing on your PC and makes the on-screen KariPom character move its mouth in sync with the sound. It also blinks and twitches its nose, adding a little fun to voice conversations with ChatGPT.

This standalone PC version grew out of the lip-sync feature of the original KariPom face robot built with Arduino and CoreS3.

KariPom Desktop is a Python application that analyzes audio played on your PC, including ChatGPT's voice, and animates KariPom's mouth accordingly.

Even when no one is speaking, KariPom stays on your desktop, blinking and twitching its nose.

In addition to the standard lip-syncing Face mode, it includes **nine audio visualizers in total** and **25 Lighting effects**. Each of the three buttons at the bottom of the screen—**Character**, **Visualizer**, and **Lighting**—switches to the previous option when you click its left third or the next option when you click its right third. Clicking the middle third does nothing. KariPom's face is always drawn in front of the other display effects, so switching modes does not interrupt the feeling of talking with KariPom.

## ✨ Features

- Real-time lip-sync to audio played on your PC, including ChatGPT's voice
- Blinking
- Nose-twitching animation
- Three Character options: KariPom / Ms. KariPom / None (hide the face)
- Nine audio visualizers, including the standard lip-syncing Face mode
- 25 Lighting effects, including 24 retro arcade-style screensavers
- Three bottom buttons for switching Character, Visualizer, and Lighting (left third: previous; middle third: no action; right third: next)
- Automatic switching at set intervals with Visualizer Random and Lighting Random
- KariPom's face remains in the foreground in every display mode
- Opens ChatGPT in the default browser at startup, then brings the KariPom Desktop window back to the front
- Spoken startup guidance, automatically switching between Japanese and English based on the OS locale
- Designed for macOS, Windows, and Linux
- No Arduino, M5Stack, or other dedicated hardware required

## 💻 Platform Support

- **macOS:** Tested on physical hardware
- **Windows:** Tested on physical hardware
- **Linux:** Tested on physical hardware

## 🚀 Set It Up with AI

You do not need to be a Python expert.

1. Save `karipom_desktop.py` to your PC.
2. Attach `karipom_desktop.py` to ChatGPT.
3. Send ChatGPT the setup request provided in the included `KariPom_Desktop_導入ガイド.pdf` (KariPom Desktop Setup Guide).
4. Work with ChatGPT to configure the application for your OS, Python installation, and audio environment.

If an error appears, show it to ChatGPT and work through the cause one step at a time.

💡 Rather than stopping once the application runs, ask ChatGPT to **create a launcher that will let you start it next time by double-clicking, without using a terminal**. Depending on your environment, this could be a `.command` or `.desktop` launcher on macOS/Linux or a `.bat` file on Windows. The AI helping with setup should determine the appropriate format for your system.

For detailed instructions, see **`KariPom_Desktop_導入ガイド.pdf`**.

## 🎙️ About macOS

On macOS, KariPom Desktop uses Apple's **ScreenCaptureKit** to capture audio playing on your PC directly. You do **not** need BlackHole or another virtual audio device, or a Multi-Output Device configured in Audio MIDI Setup.

On the first launch, macOS asks for permission to record **Screen & System Audio**. Grant the permission. If you accidentally deny it, open **System Settings → Privacy & Security → Screen & System Audio Recording**, allow KariPom Desktop (or Terminal when running the development `.py` file), and restart the application.

**macOS 13 Ventura or later** is supported.

For further details, refer to the included setup guide and the setup assistance provided by ChatGPT.

## 🐰 How It Works

KariPom Desktop does **not** use a direct connection to the ChatGPT API to animate its mouth.

Instead, it analyzes audio actually played on your PC and lip-syncs KariPom to that sound.

This makes it useful not only with ChatGPT but also with various other sources of audio playback on your PC.

## 📄 License

The source code is released under the **MIT License**.

Copyright (c) 2026 Kariage POMPADOUR Entertainment Corporation

### About the KariPom / かりポム Brand

The MIT License defines the terms for using the software code in this repository.

**It does not grant permission to use the “KariPom” or “かりポム” names, logos, character, or other brand elements.**

---

## Welcome to KariPom World!

Start a voice conversation with ChatGPT, and KariPom can come to life on your PC too.
