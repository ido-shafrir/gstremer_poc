# gstreamer project

A Python Flask tool to control a GStreamer instance for compositing and streaming multiple RTSP feeds as a WebRTC output.

## Features
- Runs on Yocto and Ubuntu 24.04 LTS
- Handles 6 RTSP video feeds, outputs a single WebRTC stream
- Real-time composite updates (any subset of feeds)
- Flask API to start, stop, and update the output feed
- Configurable via JSON/YAML
- Low latency (<1s), synchronized feeds

## Setup
1. Install dependencies:
   - Python 3.10+
   - Flask
   - PyYAML
   - GStreamer (with Python bindings)
   - aiortc (for WebRTC)

2. Configure feeds and layout in `config.yaml` or `config.json`.

3. Run the Flask app:
   ```powershell
   python app.py
   ```

## Endpoints
- `/start` : Start the output feed
- `/stop` : Stop the output feed
- `/update` : Update the composite layout

## Notes
- Ensure GStreamer plugins for RTSP and WebRTC are installed.
- For best performance, run on hardware with video acceleration.
