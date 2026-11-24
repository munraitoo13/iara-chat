let sharedAudioCtx: AudioContext | null = null;

interface WebkitWindow extends Window {
  webkitAudioContext?: typeof AudioContext;
}

function getAudioContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (sharedAudioCtx) return sharedAudioCtx;

  const AudioCtx = window.AudioContext || (window as WebkitWindow).webkitAudioContext;
  if (!AudioCtx) return null;

  sharedAudioCtx = new AudioCtx();
  return sharedAudioCtx;
}

function scheduleSoftNote(
  ctx: AudioContext,
  frequencyHz: number,
  startOffsetSec: number,
  durationSec: number,
  peakGain: number,
): void {
  const start = ctx.currentTime + startOffsetSec;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();

  osc.type = "sine";
  osc.frequency.setValueAtTime(frequencyHz, start);

  // Smooth fade-in and exponential decay for a soft marimba/chime feel
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.linearRampToValueAtTime(peakGain, start + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + durationSec);

  osc.connect(gain);
  gain.connect(ctx.destination);

  osc.start(start);
  osc.stop(start + durationSec + 0.02);
}

/**
 * Synthesize a minimalist, calm two-note notification chime using Web Audio API.
 * Returns true if the browser allowed playback, false if blocked by autoplay policy.
 */
export async function playGentleChime(): Promise<boolean> {
  const ctx = getAudioContext();
  if (!ctx) return false;

  if (ctx.state === "suspended") {
    try {
      await ctx.resume();
    } catch {
      return false;
    }
  }

  if (ctx.state !== "running") {
    return false;
  }

  scheduleSoftNote(ctx, 659.25, 0, 0.24, 0.065); // E5
  scheduleSoftNote(ctx, 987.77, 0.11, 0.34, 0.055); // B5
  return true;
}
