// Voice input (Web Speech API) and read-aloud (speechSynthesis). Both are built
// into Chrome and Edge; Chrome sends the audio to Google's speech service, so
// voice typing needs an internet connection. Firefox has no speech recognition.
import { useEffect, useRef, useState } from "react";

const Recognition = typeof window !== "undefined"
  && (window.SpeechRecognition || window.webkitSpeechRecognition);

export const speechSupported = Boolean(Recognition);
export const ttsSupported = typeof window !== "undefined" && "speechSynthesis" in window;

export const MIC_LANGS = [
  { code: "en-KE", label: "English (Kenya)" },
  { code: "en-US", label: "English (US)" },
  { code: "en-GB", label: "English (UK)" },
  { code: "sw-KE", label: "Kiswahili" },
];

export function micErrorMessage(code) {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access is blocked. Allow it from the icon in the address bar.";
    case "audio-capture": return "No microphone was found.";
    case "network": return "Voice typing needs an internet connection.";
    case "no-speech": return "Didn't hear anything. Try again.";
    case "aborted": return "";
    default: return "Voice input stopped. Try again.";
  }
}

export function newRecognition(lang, { continuous = false } = {}) {
  const rec = new Recognition();
  rec.lang = lang;
  rec.continuous = continuous;
  rec.interimResults = true;
  return rec;
}

const addWords = (base, words) =>
  !words ? base : `${base}${base && !/\s$/.test(base) ? " " : ""}${words.trimStart()}`;

/**
 * Dictation into the message box: words appear as you speak, after what's already
 * typed. Call start(currentText); stop() or silence ends it.
 */
export function useDictation({ lang, onText }) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recRef = useRef(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  useEffect(() => () => recRef.current?.abort(), []);

  function start(baseText) {
    if (!Recognition || recRef.current) return;
    const rec = newRecognition(lang, { continuous: true });
    let finals = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        const r = e.results[i];
        if (r.isFinal) finals += r[0].transcript;
        else interim += r[0].transcript;
      }
      onTextRef.current(addWords(baseText, finals + interim));
    };
    rec.onerror = (e) => setError(micErrorMessage(e.error));
    rec.onend = () => { recRef.current = null; setListening(false); };
    recRef.current = rec;
    setError("");
    try {
      rec.start();
      setListening(true);
    } catch {
      recRef.current = null;
      setError("Voice input couldn't start. Try again.");
    }
  }

  const stop = () => recRef.current?.stop();
  return { listening, error, start, stop, clearError: () => setError("") };
}

/** Split text into sentence-sized pieces: Chrome cuts off very long utterances. */
export function speechChunks(text) {
  const parts = (text || "").replace(/\s+/g, " ").match(/[^.!?]+[.!?]*\s*/g) || [];
  const out = [];
  for (const p of parts) {
    const last = out[out.length - 1];
    if (last && last.length + p.length < 180) out[out.length - 1] = last + p;
    else out.push(p);
  }
  return out.map((s) => s.trim()).filter(Boolean);
}

/** Read text aloud; resolves when finished (or cancelled). */
export function speak(text, lang) {
  return new Promise((resolve) => {
    if (!ttsSupported) { resolve(); return; }
    const synth = window.speechSynthesis;
    synth.cancel();
    const voice = synth.getVoices().find((v) => v.lang === lang)
      || synth.getVoices().find((v) => v.lang?.startsWith(lang.split("-")[0]));
    const chunks = speechChunks(text);
    if (!chunks.length) { resolve(); return; }
    chunks.forEach((chunk, i) => {
      const u = new SpeechSynthesisUtterance(chunk);
      u.lang = lang;
      if (voice) u.voice = voice;
      if (i === chunks.length - 1) { u.onend = () => resolve(); u.onerror = () => resolve(); }
      synth.speak(u);
    });
  });
}

export const stopSpeaking = () => { if (ttsSupported) window.speechSynthesis.cancel(); };
