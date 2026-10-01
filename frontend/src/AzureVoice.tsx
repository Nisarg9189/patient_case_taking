import { useCallback, useEffect, useRef, useState } from 'react'
import { Microphone, QuestionPlayer } from './audio'
import { getToken } from './auth'
import { BookVisit } from './BookVisit'

interface Line {
  role: 'agent' | 'patient'
  text: string
}

type Phase = 'idle' | 'connecting' | 'live' | 'done' | 'error'

// The interview by the Azure Foundry voice agent (the server proxies Voice Live): the patient can
// talk over the agent, and the agent saves what it hears through tools. Shown only when the
// server has the agent set up.
export function AzureVoice() {
  const [enabled, setEnabled] = useState(false)
  const [phase, setPhase] = useState<Phase>('idle')
  const [lines, setLines] = useState<Line[]>([])
  const [caseId, setCaseId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const socket = useRef<WebSocket | null>(null)
  const microphone = useRef<Microphone | null>(null)
  const player = useRef<QuestionPlayer | null>(null)
  const finished = useRef(false)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((h: { azure_voice_agent?: boolean }) => setEnabled(Boolean(h.azure_voice_agent)))
      .catch(() => setEnabled(false))
  }, [])

  const release = useCallback(async () => {
    await microphone.current?.stop()
    microphone.current = null
    // let the agent finish its goodbye before the speaker is closed
    const speaker = player.current
    player.current = null
    if (speaker) {
      await speaker.finished()
      await speaker.close()
    }
  }, [])

  const start = useCallback(async () => {
    setPhase('connecting')
    setLines([])
    setCaseId(null)
    setNotice(null)
    finished.current = false

    player.current = new QuestionPlayer()
    await player.current.unlock()

    let token: string
    try {
      token = await getToken()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Please sign in again.')
      setPhase('error')
      return
    }

    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/voice-agent`)
    ws.binaryType = 'arraybuffer'
    socket.current = ws
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }))

    // Voice Live takes 24 kHz and listens all the time: it hears the patient over the agent
    microphone.current = new Microphone((pcm) => ws.readyState === WebSocket.OPEN && ws.send(pcm), () => {}, 24000)
    try {
      await microphone.current.start()
      microphone.current.sending = true
    } catch {
      ws.close()
      setNotice('Microphone access is needed. Allow it in the browser and try again.')
      setPhase('error')
      return
    }

    ws.onmessage = (message) => {
      if (message.data instanceof ArrayBuffer) {
        player.current?.play(message.data)
        return
      }
      const event = JSON.parse(message.data)
      if (event.type === 'ready') setPhase('live')
      else if (event.type === 'transcript') setLines((old) => [...old, { role: event.role, text: event.text }])
      else if (event.type === 'interrupted') player.current?.clear()
      else if (event.type === 'error') {
        finished.current = true
        setNotice(event.text)
        setPhase('error')
      } else if (event.type === 'done') {
        finished.current = true
        setCaseId(event.case_id)
        setPhase('done')
      }
    }
    ws.onclose = () => {
      if (!finished.current) {
        setNotice((old) => old ?? 'The connection to the server was lost.')
        setPhase('error')
      }
      void release()
    }
  }, [release])

  const stop = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: 'stop' }))
  }, [])

  if (!enabled) return null

  return (
    <div className="card intro">
      {notice && <div className="notice">{notice}</div>}
      {phase === 'idle' || phase === 'error' ? (
        <>
          <h3>Talk to the Azure voice agent</h3>
          <p>
            A conversation with the clinic's voice assistant: you can interrupt it, and it saves your answers as it
            goes. Your browser will ask for microphone access.
          </p>
          <button className="secondary" onClick={start}>
            {phase === 'error' ? 'Try the voice agent again' : 'Start with the voice agent'}
          </button>
        </>
      ) : phase === 'done' ? (
        <>
          <h3>Thank you</h3>
          <p>The doctor will see what you told the assistant. Now choose the hospital and time for your visit.</p>
          {caseId && <BookVisit caseId={caseId} />}
          <button className="secondary" onClick={start}>
            Start a new conversation
          </button>
        </>
      ) : (
        <>
          <h3>{phase === 'connecting' ? 'Connecting…' : 'The voice agent is listening'}</h3>
          <ol className="turns">
            {lines.map((line, index) => (
              <li key={index}>
                <p className={line.role === 'agent' ? 'turn-question' : 'turn-answer'}>{line.text}</p>
              </li>
            ))}
          </ol>
          <button className="secondary" onClick={stop}>
            End the conversation
          </button>
        </>
      )}
    </div>
  )
}
