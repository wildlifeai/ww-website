import { ChartColumn, ChartLine, FolderUp, Tags } from 'lucide-react'
import type { GuideStep } from './ThreeStepGuide'

// Default steps used across the app — callers can override per context.
export const DEFAULT_SIGNED_IN_STEPS: GuideStep[] = [
  {
    icon: FolderUp,
    title: '1. Upload photos',
    description:
      'Drop a Wildlife Watcher SD card folder or a CamtrapDP ZIP. The system auto-detects deployments and routes images through the analysis pipeline.',
    linkTo: '/upload-data',
    linkLabel: 'Upload now →',
  },
  {
    icon: Tags,
    title: '2. Review annotations',
    description:
      'Browse ML detections, correct species labels, confirm clusters, and work through the active-learning review queue.',
    linkTo: '/annotations',
    linkLabel: 'Go to Annotations →',
  },
  {
    icon: ChartLine,
    title: '3. See & share insights',
    description:
      'Explore charts, maps, and deployment tables. Export a CamtrapDP package for R or share the report with your team.',
    linkTo: '/insights',
    linkLabel: 'Go to Insights →',
  },
]

export const DEFAULT_MARKETING_STEPS: GuideStep[] = [
  {
    icon: FolderUp,
    title: '1. Upload photos',
    description:
      'Drop a Wildlife Watcher SD card folder or a CamtrapDP ZIP. The system auto-detects deployments and routes images through the analysis pipeline.',
    linkTo: '/login',
    linkLabel: 'Sign in to upload →',
  },
  {
    icon: Tags,
    title: '2. Review annotations',
    description:
      'Browse ML detections, correct species labels, confirm clusters, and work through the active-learning review queue.',
    linkTo: '/login',
    linkLabel: 'Sign in to review →',
  },
  {
    icon: ChartColumn,
    title: '3. See & share results',
    description:
      'Explore charts, maps, and deployment tables. Export a CamtrapDP package for R or share the report with your team.',
    linkTo: '/login',
    linkLabel: 'Sign in to see results →',
  },
]
