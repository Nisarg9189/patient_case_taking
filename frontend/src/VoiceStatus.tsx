export type VoiceState = 'speaking' | 'listening' | 'thinking' | 'saving'

const LABEL: Record<VoiceState, string> = {
  speaking: 'The assistant is speaking',
  listening: 'Listening to you',
  thinking: 'Thinking…',
  saving: 'Saving your answers…',
}

// What the voice assistant is doing right now: bars while it talks, a pulsing dot while it
// listens, bouncing dots while it thinks or runs one of its tools.
export function VoiceStatus({ state }: { state: VoiceState }) {
  return (
    <div className={`voice-status voice-${state}`} role="status" aria-live="polite">
      <span className="voice-orb" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </span>
      <span>{LABEL[state]}</span>
    </div>
  )
}
