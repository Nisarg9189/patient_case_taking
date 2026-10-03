// Line icons (24×24, drawn with the current text colour).
import type { ReactNode } from 'react'

function Icon({ children, size = 22 }: { children: ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  )
}

type P = { size?: number }

export const LogoIcon = (p: P) => (
  <Icon {...p}>
    <path d="M12 21s-7-4.35-7-10a4 4 0 0 1 7-2.65A4 4 0 0 1 19 11c0 5.65-7 10-7 10z" />
    <path d="M8 12h2l1-2 2 4 1-2h2" />
  </Icon>
)

export const CalendarIcon = (p: P) => (
  <Icon {...p}>
    <rect x="3" y="4.5" width="18" height="16.5" rx="3" />
    <path d="M16 2.5v4M8 2.5v4M3 10h18" />
    <circle cx="16" cy="16" r="2.2" />
  </Icon>
)

export const ClockIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Icon>
)

export const ClipboardIcon = (p: P) => (
  <Icon {...p}>
    <rect x="5" y="4" width="14" height="17" rx="2.5" />
    <path d="M9 4V3h6v1M9 10h6M9 14h6M9 18h3" />
  </Icon>
)

export const UsersIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="9" cy="8" r="3.5" />
    <path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 14.2A6.5 6.5 0 0 1 21.5 20" />
  </Icon>
)

export const MicIcon = (p: P) => (
  <Icon {...p}>
    <rect x="9" y="2.5" width="6" height="12" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3.5" />
  </Icon>
)

export const SearchIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Icon>
)

export const ChevronLeft = (p: P) => (
  <Icon {...p}>
    <path d="m15 18-6-6 6-6" />
  </Icon>
)

export const ChevronRight = (p: P) => (
  <Icon {...p}>
    <path d="m9 18 6-6-6-6" />
  </Icon>
)

export const MoreIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="5" cy="12" r="1" fill="currentColor" />
    <circle cx="12" cy="12" r="1" fill="currentColor" />
    <circle cx="19" cy="12" r="1" fill="currentColor" />
  </Icon>
)

export const LogoutIcon = (p: P) => (
  <Icon {...p}>
    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l-5-5 5-5M5 12h11" />
  </Icon>
)

export const HomeIcon = (p: P) => (
  <Icon {...p}>
    <path d="M3.5 11.5 12 4l8.5 7.5" />
    <path d="M6 10v9.5h12V10" />
    <path d="M10 19.5v-5h4v5" />
  </Icon>
)

export const HospitalIcon = (p: P) => (
  <Icon {...p}>
    <path d="M4 20.5V6.5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v14" />
    <path d="M16 11h2.5a1.5 1.5 0 0 1 1.5 1.5v8" />
    <path d="M2.5 20.5h19" />
    <path d="M10 8v5M7.5 10.5h5" />
    <path d="M8.5 20.5v-3h3v3" />
  </Icon>
)

export const MapPinIcon = (p: P) => (
  <Icon {...p}>
    <path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0C18.5 15.4 12 21 12 21z" />
    <circle cx="12" cy="10" r="2.4" />
  </Icon>
)

export const PhoneIcon = (p: P) => (
  <Icon {...p}>
    <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z" />
  </Icon>
)

export const CheckIcon = (p: P) => (
  <Icon {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Icon>
)

export const ArrowRightIcon = (p: P) => (
  <Icon {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Icon>
)

export const ShieldIcon = (p: P) => (
  <Icon {...p}>
    <path d="M12 3l7.5 3v5.5c0 4.6-3.1 8.2-7.5 9.5-4.4-1.3-7.5-4.9-7.5-9.5V6L12 3z" />
    <path d="M12 8v4.5M12 15.5v.01" />
  </Icon>
)

export const FileTextIcon = (p: P) => (
  <Icon {...p}>
    <path d="M6 3h8l4 4v14H6z" />
    <path d="M14 3v4h4M9 12h6M9 16h6M9 8h2" />
  </Icon>
)

export const PillIcon = (p: P) => (
  <Icon {...p}>
    <rect x="3" y="8.5" width="18" height="7" rx="3.5" transform="rotate(-35 12 12)" />
    <path d="M9.4 7.8l5.2 7.4" />
  </Icon>
)

export const GlobeIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.6 2.5 4 5.6 4 9s-1.4 6.5-4 9c-2.6-2.5-4-5.6-4-9s1.4-6.5 4-9z" />
  </Icon>
)

export const ShareIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="6" cy="12" r="2.5" />
    <circle cx="18" cy="6" r="2.5" />
    <circle cx="18" cy="18" r="2.5" />
    <path d="M8.2 10.8l7.6-3.6M8.2 13.2l7.6 3.6" />
  </Icon>
)

export const MailIcon = (p: P) => (
  <Icon {...p}>
    <rect x="3" y="5" width="18" height="14" rx="3" />
    <path d="M4 7.5l8 6 8-6" />
  </Icon>
)

export const LockIcon = (p: P) => (
  <Icon {...p}>
    <rect x="4.5" y="10.5" width="15" height="10" rx="3" />
    <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5M12 14.5v2" />
  </Icon>
)

export const UserIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="12" cy="8.5" r="3.8" />
    <path d="M4.5 20c.9-3.6 3.8-5.5 7.5-5.5s6.6 1.9 7.5 5.5" />
  </Icon>
)

export const EyeIcon = (p: P) => (
  <Icon {...p}>
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
    <circle cx="12" cy="12" r="2.8" />
  </Icon>
)

export const EyeOffIcon = (p: P) => (
  <Icon {...p}>
    <path d="M3.5 4.5l17 15M10 6c.6-.1 1.3-.2 2-.2 6 0 9.5 6.2 9.5 6.2a16 16 0 0 1-3 3.6M6.4 7.7A16 16 0 0 0 2.5 12S6 18.2 12 18.2c1.4 0 2.6-.3 3.7-.8" />
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </Icon>
)
