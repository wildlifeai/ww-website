import { Link } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'

export interface GuideStep {
  icon: LucideIcon
  title: string
  description: string
  /** React-Router `to` path */
  linkTo: string
  linkLabel: string
}

export interface ThreeStepGuideProps {
  steps: GuideStep[]
  /** Optional heading rendered above the cards */
  heading?: string
}

export function ThreeStepGuide({ steps, heading }: ThreeStepGuideProps) {
  return (
    <div>
      {heading && (
        <h2 style={{
          fontSize: '1.375rem',
          fontWeight: 700,
          marginBottom: '1.5rem',
          textAlign: 'center',
        }}>
          {heading}
        </h2>
      )}

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: '1.25rem',
      }}>
        {steps.map((step, i) => (
          <div
            key={i}
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              padding: '1.5rem 1.25rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.75rem',
            }}
          >
            {/* Icon */}
            <div style={{
              width: 48,
              height: 48,
              borderRadius: 12,
              background: 'linear-gradient(135deg,rgba(76,175,80,0.25),rgba(76,175,80,0.07))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--primary)',
              flexShrink: 0,
            }}>
              <step.icon size={24} aria-hidden="true" />
            </div>

            {/* Title */}
            <div style={{ fontWeight: 700, fontSize: '1rem' }}>{step.title}</div>

            {/* Description */}
            <p style={{
              margin: 0,
              fontSize: '0.875rem',
              opacity: 0.7,
              lineHeight: 1.55,
              flex: 1,
            }}>
              {step.description}
            </p>

            {/* Link */}
            <Link
              to={step.linkTo}
              style={{
                fontSize: '0.875rem',
                color: 'var(--primary)',
                fontWeight: 600,
                textDecoration: 'none',
                marginTop: 'auto',
              }}
              onMouseEnter={e => ((e.target as HTMLElement).style.textDecoration = 'underline')}
              onMouseLeave={e => ((e.target as HTMLElement).style.textDecoration = 'none')}
            >
              {step.linkLabel}
            </Link>
          </div>
        ))}
      </div>
    </div>
  )
}

