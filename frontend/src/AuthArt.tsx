// Healthcare line art for the sign-in page: a heartbeat line, a stethoscope, a hospital, a speech
// bubble and small crosses. Drawn in the current colour; the page shows it faintly.

const plus = (x: number, y: number, s: number) => `M${x - s} ${y}H${x + s}M${x} ${y - s}V${y + s}`

export function AuthArt({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 480 640" preserveAspectRatio="xMidYMid slice" fill="none" stroke="currentColor"
         strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {/* heartbeat */}
      <path d="M-10 330H110l22-34 26 78 32-150 30 190 24-84H500" strokeWidth={3} />
      {/* stethoscope */}
      <circle cx="330" cy="62" r="6" />
      <circle cx="402" cy="62" r="6" />
      <path d="M330 68C330 122 362 142 366 172M402 68C402 122 370 142 366 172M366 172C366 232 300 226 300 268" />
      <circle cx="300" cy="288" r="20" />
      <circle cx="300" cy="288" r="8" />
      {/* a speech bubble with three dots */}
      <rect x="44" y="132" width="150" height="66" rx="20" />
      <path d="M76 198l-10 24 34-24" />
      <circle cx="92" cy="165" r="3" />
      <circle cx="119" cy="165" r="3" />
      <circle cx="146" cy="165" r="3" />
      {/* a hospital */}
      <path d="M36 612V510h72V470h92v142zM108 612h-0" />
      <path d="M154 500v34M137 517h34" />
      <path d="M60 540h20M60 570h20M128 570h20M128 595h20" />
      <path d="M20 612h200" />
      {/* a ring, and crosses */}
      <circle cx="388" cy="470" r="64" strokeDasharray="2 9" />
      <circle cx="388" cy="470" r="30" />
      <path d={plus(388, 470, 12)} />
      <path d={[plus(70, 62, 9), plus(190, 250, 7), plus(430, 250, 9), plus(250, 420, 7), plus(60, 400, 8), plus(300, 600, 8), plus(440, 600, 7)].join('')} />
    </svg>
  )
}
