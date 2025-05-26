from flask import Flask, request, jsonify, Response
import yaml
import threading
import subprocess
import os
import logging
import traceback
import json

app = Flask(__name__)

CONFIG_PATH = os.environ.get('GST_CONFIG', 'config.yaml')
GST_PROCESS = None
GST_LOCK = threading.Lock()

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# File handler for debug level
file_handler = logging.FileHandler('gstreamer_debug.log')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Stream handler for info level
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
stream_handler.setFormatter(stream_formatter)
logger.addHandler(stream_handler)

# Load configuration
def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

# Build GStreamer pipeline string
def build_pipeline(feeds, composite, webrtc):
    """
    Build a GStreamer pipeline string for the selected feeds and composite layout.
    Args:
        feeds (list): List of all feed dicts (with 'name' and 'url').
        composite (list): List of feed names to include in the composite.
        webrtc (dict): WebRTC output settings.
    Returns:
        str: GStreamer pipeline string.
    Raises:
        ValueError: If no valid feeds are selected.
    """
    # Map feed names to URLs
    feed_map = {f['name']: f['url'] for f in feeds}
    selected_urls = [feed_map[name] for name in composite if name in feed_map]
    if not selected_urls:
        raise ValueError("No valid feeds selected for composite.")
    
    # GStreamer pipeline parts
    pipeline_parts = []
    compositor_inputs = []
    for idx, url in enumerate(selected_urls):
        src = f"rtspsrc location={url} latency=100 ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! videoscale ! video/x-raw,width=640,height=360 ! queue name=q{idx}"
        pipeline_parts.append(src)
        compositor_inputs.append(f"q{idx}.")
    
    # Layout: grid (auto for up to 6 feeds)
    # For simplicity, place all at (0,0) (user can improve layout later)
    compositor = "compositor name=mix sink_0::xpos=0 sink_0::ypos=0 "
    for i in range(1, len(selected_urls)):
        xpos = (i % 3) * 640
        ypos = (i // 3) * 360
        compositor += f"sink_{i}::xpos={xpos} sink_{i}::ypos={ypos} "
    compositor += "! videoconvert ! x264enc tune=zerolatency bitrate=2048 speed-preset=ultrafast ! rtph264pay ! queue ! webrtcbin bundle-policy=max-bundle name=sendrecv "
    
    # WebRTC config (STUN server)
    stun = webrtc.get('stun_server', 'stun:stun.l.google.com:19302')
    webrtc_signaling = f"sendrecv.stun-server={stun}"
    
    # Connect all sources to compositor
    pipeline = " ".join(pipeline_parts)
    pipeline += f" {compositor} {webrtc_signaling}"
    logging.info(f"Generated GStreamer pipeline: {pipeline}")
    return pipeline

# Start GStreamer process
def start_gst(feeds, composite, webrtc):
    global GST_PROCESS
    try:
        pipeline = build_pipeline(feeds, composite, webrtc)
    except Exception as e:
        logging.error(f"Failed to build pipeline: {e}")
        logging.debug(traceback.format_exc())
        return False
    with GST_LOCK:
        if GST_PROCESS:
            logging.warning("GStreamer process is already running.")
            return False
        try:
            GST_PROCESS = subprocess.Popen(pipeline, shell=True)
            logging.info("Started GStreamer process.")
        except Exception as e:
            logging.error(f"Failed to start GStreamer process: {e}")
            logging.debug(traceback.format_exc())
            GST_PROCESS = None
            return False
    return True

# Stop GStreamer process
def stop_gst():
    global GST_PROCESS
    with GST_LOCK:
        if GST_PROCESS:
            try:
                GST_PROCESS.terminate()
                GST_PROCESS.wait(timeout=5)
                logging.info("Stopped GStreamer process.")
            except Exception as e:
                logging.error(f"Error stopping GStreamer process: {e}")
                logging.debug(traceback.format_exc())
            GST_PROCESS = None
            return True
        else:
            logging.warning("No GStreamer process to stop.")
    return False

@app.route('/start', methods=['POST'])
def start():
    """
    Start the GStreamer output feed with the current configuration.
    
    Example (curl):
        curl -X POST http://localhost:5000/start
    
    Returns:
        JSON: {"status": "started"} on success, {"status": "already running"} if already running.
    """
    config = load_config()
    feeds = config['feeds']
    composite = config['composite']
    webrtc = config['webrtc']
    if start_gst(feeds, composite, webrtc):
        return jsonify({'status': 'started'})
    else:
        return jsonify({'status': 'already running or error'}), 400

@app.route('/stop', methods=['POST'])
def stop():
    """
    Stop the GStreamer output feed.
    
    Example (curl):
        curl -X POST http://localhost:5000/stop
    
    Returns:
        JSON: {"status": "stopped"} on success, {"status": "not running"} if not running.
    """
    if stop_gst():
        return jsonify({'status': 'stopped'})
    else:
        return jsonify({'status': 'not running'}), 400

@app.route('/update', methods=['POST'])
def update():
    """
    Update the composite layout of the output feed in real time.
    
    Example (curl):
        curl -X POST http://localhost:5000/update \
            -H "Content-Type: application/json" \
            -d '{"composite": ["cam1", "cam2", "cam3"]}'
    
    Request JSON:
        {
            "composite": ["cam1", "cam2", ...]  # List of feed names to include
        }
    Returns:
        JSON: {"status": "updated", "composite": [...] } on success, {"status": "failed to update"} on failure.
    """
    data = request.json
    composite = data.get('composite')
    config = load_config()
    feeds = config['feeds']
    webrtc = config['webrtc']
    stop_gst()
    if not composite or not isinstance(composite, list):
        logging.error("Invalid composite list provided in update request.")
        return jsonify({'status': 'invalid composite'}), 400
    if start_gst(feeds, composite, webrtc):
        return jsonify({'status': 'updated', 'composite': composite})
    else:
        return jsonify({'status': 'failed to update'}), 500

# WebRTC signaling endpoints
@app.route('/offer', methods=['POST'])
def offer():
    """
    Receives an SDP offer from a WebRTC client and returns an SDP answer from GStreamer.
    Example (curl):
        curl -X POST http://localhost:5000/offer \
            -H "Content-Type: application/json" \
            -d '{"sdp": "...", "type": "offer"}'
    Request JSON:
        {
            "sdp": "...",
            "type": "offer"
        }
    Returns:
        JSON: {"sdp": "...", "type": "answer"}
    """
    data = request.get_json()
    sdp_offer = data.get('sdp')
    if not sdp_offer:
        return jsonify({'error': 'No SDP offer provided'}), 400
    # Save offer to file (for GStreamer to read)
    with open('client_offer.sdp', 'w') as f:
        f.write(sdp_offer)
    # Run gst-launch to generate answer (for demo, use gst_sdpdemux or similar in real app)
    # Here, we just echo back a placeholder answer
    # In production, you would use aiortc or a GStreamer subprocess to handle this
    sdp_answer = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=GStreamer WebRTC\r\nt=0 0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\na=rtcp:9 IN IP4 0.0.0.0\r\na=ice-ufrag:dummy\r\na=ice-pwd:dummy\r\na=fingerprint:sha-256 DUMMY\r\na=setup:actpass\r\na=mid:video0\r\na=sendonly\r\na=rtpmap:96 H264/90000\r\na=ssrc:1 cname:stream\r\n"
    return jsonify({'sdp': sdp_answer, 'type': 'answer'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
