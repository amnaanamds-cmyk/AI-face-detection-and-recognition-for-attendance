// Thin wrapper around getUserMedia used by face registration and live attendance.
class Camera {
  constructor(video) { this.video = video; this.stream = null; this.canvas = document.createElement('canvas'); }

  async start(width = 1280, height = 720) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera API not available (use https or localhost).');
    this.stream = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: width}, height: {ideal: height}}, audio: false});
    this.video.srcObject = this.stream;
    await new Promise(r => (this.video.readyState >= 2 ? r() : (this.video.onloadeddata = r)));
  }

  stop() { this.stream?.getTracks().forEach(t => t.stop()); this.stream = null; }

  get running() { return !!this.stream; }

  // Returns a JPEG data URL of the current (un-mirrored) frame.
  capture(quality = 0.85, maxWidth = 1280) {
    const v = this.video, scale = Math.min(1, maxWidth / v.videoWidth);
    this.canvas.width = Math.round(v.videoWidth * scale);
    this.canvas.height = Math.round(v.videoHeight * scale);
    this.canvas.getContext('2d').drawImage(v, 0, 0, this.canvas.width, this.canvas.height);
    this.scale = scale;
    return this.canvas.toDataURL('image/jpeg', quality);
  }
}
