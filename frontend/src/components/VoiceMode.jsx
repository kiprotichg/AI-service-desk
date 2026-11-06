import { useEffect, useRef, useState } from "react";
import { CloseIcon, MicIcon, StopIcon, VolumeIcon, VolumeOffIcon } from "./Icons.jsx";
import {
  micErrorMessage, newRecognition, speak, speechSupported, stopSpeaking, ttsSupported,
} from "../lib/speech.js";

const STATUS = {
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
  idle: "Tap the circle and start talking",
};

/**
 * Hands-free voice chat: listens, sends what you said when you stop talking, reads
 * the answer aloud, then listens again. onAsk(text) sends a message and resolves
 * with the reply text (or null if it failed).
 */
export default function VoiceMode({ lang, onAsk, onClose }) {
  const [phase, setPhase] = useState("idle");
  const [heard, setHeard] = useState("");
  const [answer, setAnswer] = useState("");
  const [note, setNote] = useState("");
  const [muted, setMuted] = useState(!ttsSupported);
  const active = useRef(true);
  const recRef = useRef(null);
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const silences = useRef(0);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  function stopListening() {
    const rec = recRef.current;
    recRef.current = null;
    if (rec) { rec.onend = null; rec.onresult = null; rec.onerror = null; rec.abort(); }
  }

  function listen() {
    if (!active.current || !speechSupported) return;
    stopListening();
    stopSpeaking();
    const rec = newRecognition(lang);
    let finals = "";
    let failed = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        if (e.results[i].isFinal) finals += e.results[i][0].transcript;
        else interim += e.results[i][0].transcript;
      }
      setHeard((finals + interim).trim());
    };
    rec.onerror = (e) => { failed = e.error; };
    rec.onend = async () => {
      recRef.current = null;
      if (!active.current) return;
      const said = finals.trim();
      if (!said) {
        if (failed && failed !== "no-speech") {
          setNote(micErrorMessage(failed));
          setPhase("idle");
          return;
        }
        // Quiet: keep listening a little longer, then wait for a tap.
        silences.current += 1;
        if (silences.current < 3) listen();
        else setPhase("idle");
        return;
      }
      silences.current = 0;
      setPhase("thinking");
      const reply = await onAsk(said);
      if (!active.current) return;
      if (!reply) {
        setNote("Couldn't get an answer. Tap the circle to try again.");
        setPhase("idle");
        return;
      }
      setAnswer(reply);
      if (mutedRef.current) { listen(); return; }
      setPhase("speaking");
      await speak(reply, lang);
      if (active.current && recRef.current === null) listen();
    };
    recRef.current = rec;
    setHeard("");
    setNote("");
    try {
      rec.start();
      setPhase("listening");
    } catch {
      setPhase("idle");
    }
  }

  useEffect(() => {
    active.current = true;
    if (speechSupported) listen();
    const onKey = (e) => { if (e.key === "Escape") closeRef.current(); };
    document.addEventListener("keydown", onKey);
    return () => {
      active.current = false;
      document.removeEventListener("keydown", onKey);
      stopListening();
      stopSpeaking();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onCircle() {
    if (phase === "speaking") { stopSpeaking(); listen(); return; }   // interrupt the answer
    if (phase === "listening") { recRef.current?.stop(); return; }   // done talking: send now
    if (phase === "idle") { silences.current = 0; listen(); }
  }

  function toggleMute() {
    setMuted((m) => {
      if (!m && phase === "speaking") { stopSpeaking(); }
      return !m;
    });
  }

  if (!speechSupported) {
    return (
      <div className="voice-mode" role="dialog" aria-label="Voice mode">
        <p className="voice-status">Voice mode needs Google Chrome or Microsoft Edge.</p>
        <button className="voice-end" onClick={onClose}>Close</button>
      </div>
    );
  }

  return (
    <div className="voice-mode" role="dialog" aria-label="Voice mode">
      <button className={`voice-orb ${phase}`} onClick={onCircle}
        aria-label={phase === "speaking" ? "Stop and talk" : phase === "listening" ? "Send now" : "Start talking"}>
        {phase === "speaking" ? <StopIcon width={34} height={34} /> : <MicIcon width={38} height={38} />}
      </button>
      <p className="voice-status" aria-live="polite">{STATUS[phase]}</p>
      {note && <p className="voice-note" role="alert">{note}</p>}
      {heard && phase !== "speaking" && <p className="voice-heard">“{heard}”</p>}
      {answer && phase === "speaking" && <p className="voice-answer">{answer}</p>}
      <div className="voice-controls">
        <button className="voice-btn" onClick={toggleMute} disabled={!ttsSupported}
          aria-pressed={muted} title={muted ? "Read answers aloud" : "Don't read answers aloud"}>
          {muted ? <VolumeOffIcon /> : <VolumeIcon />}
          <span>{muted ? "Sound off" : "Sound on"}</span>
        </button>
        <button className="voice-end" onClick={onClose}>
          <CloseIcon width={18} height={18} /> End voice
        </button>
      </div>
      <p className="voice-fine">Your conversation is saved in this chat as text.</p>
    </div>
  );
}
