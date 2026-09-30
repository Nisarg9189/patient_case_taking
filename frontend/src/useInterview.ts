import { useCallback, useRef, useState } from 'react'
import { Microphone, QuestionPlayer } from './audio'
import { api, getToken } from './auth'
import type { CaseRecord, Checklist, ConnectionState, ServerEvent } from './types'

export type Phase = 'idle' | 'connecting' | 'speaking' | 'listening' | 'thinking' | 'reconnecting' | 'reviewing' | 'done' | 'error'

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
  const [caseId, setCaseId] = useState<string | null>(null) // the stored case, set when the interview ends
  const [review, setReview] = useState<CaseRecord | null>(null) // the finished case, waiting for the patient's Save
  const [confirmed, setConfirmed] = useState<Record<string, string> | null>(null) // what the patient saved
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
        case 'review_case':
          // the questions are done: the patient checks the case and saves it before it is final
          if (microphone.current) microphone.current.sending = false
          setCaseRecord(event.case)
          setChecklist(event.checklist)
          setReview(event.case)
          setPhase('reviewing')
          break
        case 'done':
          setConnection(null)
          setReview(null)
          setConfirmed(event.review)
          if (event.case) setCaseRecord(event.case)
          if (event.checklist) setChecklist(event.checklist)
          setEnding(event.aborted ? event.reason ?? 'The interview ended early.' : null)
          setCaseId(event.case_id)
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
    setCaseId(null)
    setReview(null)
    setConfirmed(null)

    // audio must be started from the click that called start()
    player.current = new QuestionPlayer()
    await player.current.unlock()

    let token: string
    try {
      token = await getToken()
    } catch (error) {
      await release()
      setNotice(error instanceof Error ? error.message : 'Please sign in again.')
      setPhase('error')
      return
    }

    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/interview`)
    ws.binaryType = 'arraybuffer'
    socket.current = ws
    // the first message says who the patient is (the server closes the socket otherwise)
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }))

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
        // keep the server's own reason (e.g. a sign-in problem) if it sent one
        setNotice((notice) => notice ?? 'The connection to the server was lost.')
        return 'error'
      })
      void release()
    }
  }, [handleEvent, release])

  // the patient checked the finished case and pressed Save: the interview finishes
  const confirmReview = useCallback((sections: Record<string, string>) => {
    socket.current?.send(JSON.stringify({ type: 'review_confirmed', sections }))
    setConfirmed(sections)
    setReview(null)
    setPhase('thinking')
  }, [])

  // the patient's edited summary: {section label: text}; resolves to the time it was saved
  const saveReview = useCallback(
    async (sections: Record<string, string>) => {
      if (!caseId) throw new Error('This interview was not stored, so it cannot be edited.')
      const saved = await api<{ saved_at: string }>(`/api/cases/${caseId}/review`, {
        method: 'PUT',
        body: JSON.stringify({ sections }),
      })
      return saved.saved_at
    },
    [caseId],
  )

  const stop = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: 'stop' }))
  }, [])

  return {
    phase, question, transcript, turns, caseRecord, checklist, notice, connection, ending, level, caseId,
    review, confirmed, start, stop, saveReview, confirmReview,
  }
}
