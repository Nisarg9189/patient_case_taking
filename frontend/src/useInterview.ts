import { useCallback, useRef, useState } from 'react'
import { Microphone, QuestionPlayer } from './audio'
import type { CaseRecord, Checklist, ConnectionState, ServerEvent } from './types'

export type Phase = 'idle' | 'connecting' | 'speaking' | 'listening' | 'thinking' | 'reconnecting' | 'done' | 'error'

export interface Turn {
  question: string
  answer: string
}

export function useInterview() {
  const [phase, setPhase] = useState<Phase>('idle')
  const [question, setQuestion] = useState('')
  const [transcript, setTranscript] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [caseRecord, setCaseRecord] = useState<CaseRecord | null>(null)
  const [checklist, setChecklist] = useState<Checklist>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [connection, setConnection] = useState<{ state: ConnectionState; text: string } | null>(null)
  const [ending, setEnding] = useState<string | null>(null)
  const [level, setLevel] = useState(0)

  const socket = useRef<WebSocket | null>(null)
  const microphone = useRef<Microphone | null>(null)
  const player = useRef<QuestionPlayer | null>(null)

  const release = useCallback(async () => {
    await microphone.current?.stop()
    await player.current?.close()
    microphone.current = null
    player.current = null
    setLevel(0)
  }, [])

  const handleEvent = useCallback(
    async (event: ServerEvent) => {
      switch (event.type) {
        case 'question':
          setConnection(null)
          setQuestion(event.text)
          setTranscript('')
          setPhase('speaking')
          break
        case 'question_audio_end':
          // the server only starts listening once the question has been heard
          await player.current?.finished()
          socket.current?.send(JSON.stringify({ type: 'playback_done' }))
          break
        case 'listening':
          if (microphone.current) microphone.current.sending = true
          setPhase('listening')
          break
        case 'transcript':
          setTranscript(event.text)
          break
        case 'stopped_listening':
          if (microphone.current) microphone.current.sending = false
          setPhase('thinking')
          break
        case 'answer':
          setConnection(null)
          setTurns((previous) => [...previous, { question: event.question, answer: event.text }])
          break
        case 'case':
          setCaseRecord(event.case)
          setChecklist(event.checklist)
          break
        case 'done':
          setConnection(null)
          if (event.case) setCaseRecord(event.case)
          if (event.checklist) setChecklist(event.checklist)
          setEnding(event.aborted ? event.reason ?? 'The interview ended early.' : null)
          setPhase('done')
          socket.current?.close()
          await release()
          break
        case 'error':
          setNotice(event.text)
          break
        case 'status':
          setConnection({ state: event.state, text: event.text })
          if (event.state === 'reconnecting') {
            if (microphone.current) microphone.current.sending = false
            setPhase('reconnecting')
          }
          break
      }
    },
    [release],
  )

  const start = useCallback(async () => {
    setPhase('connecting')
    setTurns([])
    setCaseRecord(null)
    setChecklist({})
    setNotice(null)
    setConnection(null)
    setEnding(null)

    // audio must be started from the click that called start()
    player.current = new QuestionPlayer()
    await player.current.unlock()

    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/interview`)
    ws.binaryType = 'arraybuffer'
    socket.current = ws

    microphone.current = new Microphone(
      (pcm) => ws.readyState === WebSocket.OPEN && ws.send(pcm),
      setLevel,
    )
    try {
      await microphone.current.start()
    } catch {
      ws.close()
      await release()
      setNotice('Microphone access is needed. Allow it in the browser and try again.')
      setPhase('error')
      return
    }

    // events are handled one at a time, in order (question_audio_end waits for playback)
    let queue = Promise.resolve()
    ws.onmessage = (message) => {
      if (message.data instanceof ArrayBuffer) {
        player.current?.play(message.data)
        return
      }
      const event = JSON.parse(message.data) as ServerEvent
      queue = queue.then(() => handleEvent(event))
    }
    ws.onclose = () => {
      setPhase((current) => {
        if (current === 'done' || current === 'idle') return current
        setNotice('The connection to the server was lost.')
        return 'error'
      })
      void release()
    }
  }, [handleEvent, release])

  const stop = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: 'stop' }))
  }, [])

  return { phase, question, transcript, turns, caseRecord, checklist, notice, connection, ending, level, start, stop }
}
