/** Offline barcode decoding (E14-02). Uses the browser's BarcodeDetector where available (can return several codes
 *  per image), otherwise the bundled ZXing decoder, loaded only when needed. Nothing leaves the device. */
type Detector = { detect: (src: CanvasImageSource) => Promise<{ rawValue: string }[]> };
const FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "qr_code"];

async function native(): Promise<Detector | null> {
  const BD = (window as unknown as { BarcodeDetector?: { new (o: object): Detector; getSupportedFormats(): Promise<string[]> } }).BarcodeDetector;
  if (!BD) return null;
  try {
    const ok = await BD.getSupportedFormats();
    return new BD({ formats: FORMATS.filter((f) => ok.includes(f)) });
  } catch { return null; }
}

async function zxingDecode(img: HTMLImageElement | HTMLCanvasElement): Promise<string[]> {
  const { BrowserMultiFormatReader } = await import("@zxing/browser");
  const reader = new BrowserMultiFormatReader();
  try {
    const r = img instanceof HTMLImageElement ? await reader.decodeFromImageElement(img) : reader.decodeFromCanvas(img);
    return r ? [r.getText()] : [];
  } catch { return []; }
}

/** Decode all barcodes in a photo the user took or picked (works over plain http too: no camera permission). */
export async function decodePhoto(file: File): Promise<string[]> {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const det = await native();
    if (det) {
      const found = await det.detect(img);
      if (found.length) return [...new Set(found.map((f) => f.rawValue))];
    }
    return zxingDecode(img);
  } finally { URL.revokeObjectURL(url); }
}

/** Live camera scanning. Calls onCode for each newly seen code. Needs HTTPS (or localhost). Returns stop(). */
export async function startCamera(video: HTMLVideoElement, onCode: (code: string) => void): Promise<() => void> {
  const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
  video.srcObject = stream;
  await video.play();
  let stopped = false;
  let last = "", lastAt = 0;
  const emit = (c: string) => { const t = Date.now(); if (c !== last || t - lastAt > 2500) { last = c; lastAt = t; onCode(c); } };
  const det = await native();
  if (det) {
    const loop = async () => {
      if (stopped) return;
      try { (await det.detect(video)).forEach((f) => emit(f.rawValue)); } catch { /* frame not ready */ }
      setTimeout(loop, 250);
    };
    loop();
    return () => { stopped = true; stream.getTracks().forEach((t) => t.stop()); };
  }
  const { BrowserMultiFormatReader } = await import("@zxing/browser");
  const controls = await new BrowserMultiFormatReader().decodeFromVideoElement(video, (res) => { if (res && !stopped) emit(res.getText()); });
  return () => { stopped = true; controls.stop(); stream.getTracks().forEach((t) => t.stop()); };
}

export const cameraAllowed = () => window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
