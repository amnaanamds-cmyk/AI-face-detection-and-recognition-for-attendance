// Thin wrapper around getUserMedia used by face registration and live attendance.
// Works on PCs (webcam) and phones/tablets (front "user" or back "environment" camera).
class Camera {
  constructor(video) {
    this.video = video; this.stream = null; this.canvas = document.createElement('canvas');
    this.facing = 'user';
  }

  async start(width = 1280, height = 720, facing = this.facing) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Camera needs a secure connection. On phones open the https:// address shown by "start.bat --lan" (see Mobile setup).');
    }
    this.stop();
    this.facing = facing;
    const video = {width: {ideal: width}, height: {ideal: height}, facingMode: {ideal: facing}};
    this.stream = await navigator.mediaDevices.getUserMedia({video, audio: false});
    this.video.srcObject = this.stream;
    // Mirror only the selfie camera, like every phone camera app does.
    this.video.closest('.video-wrap')?.classList.toggle('mirror', this.mirrored);
    await new Promise(r => (this.video.readyState >= 2 ? r() : (this.video.onloadeddata = r)));
  }

  async switchCamera(width, height) {
    return this.start(width, height, this.facing === 'user' ? 'environment' : 'user');
  }

  get mirrored() {
    const s = this.stream?.getVideoTracks()[0]?.getSettings?.() || {};
    return (s.facingMode || this.facing) !== 'environment';
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
