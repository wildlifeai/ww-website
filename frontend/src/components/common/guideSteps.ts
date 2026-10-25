import { ChartColumn, ChartLine, Download, FolderUp, Images, Tags } from 'lucide-react'
import type { GuideStep } from './ThreeStepGuide'

// One wording per step, shared by the signed-in and the public guide.
const UPLOAD = 'Drop a Wildlife Watcher SD card folder or a CamtrapDP ZIP. Deployments are detected and the photos go through the AI pipeline.'
const REVIEW = 'Check the AI labels, correct species and confirm groups of look-alike photos.'
const INSIGHTS = 'Explore charts, maps and deployment tables, and export a CamtrapDP package for R.'

// For a user who can upload to at least one project.
export const DEFAULT_SIGNED_IN_STEPS: GuideStep[] = [
  { icon: FolderUp, title: '1. Upload photos', description: UPLOAD, linkTo: '/upload-data', linkLabel: 'Upload now' },
  { icon: Tags, title: '2. Review', description: REVIEW, linkTo: '/annotations', linkLabel: 'Go to Media' },
  { icon: ChartLine, title: '3. See & share insights', description: INSIGHTS, linkTo: '/insights', linkLabel: 'Go to Insights' },
]

// For a viewer (the demo included): read-only, so no upload and no labelling (decision 1).
export const VIEWER_STEPS: GuideStep[] = [
  { icon: Images, title: '1. Browse photos', description: 'See the photos in your projects with their AI and human labels.', linkTo: '/annotations', linkLabel: 'Go to Media' },
  { icon: ChartLine, title: '2. See insights', description: 'Explore charts, maps and deployment tables.', linkTo: '/insights', linkLabel: 'Go to Insights' },
  { icon: Download, title: '3. Export data', description: 'Download a project as a CamtrapDP package for R.', linkTo: '/toolkit', linkLabel: 'Go to Toolkit' },
]

export const DEFAULT_MARKETING_STEPS: GuideStep[] = [
  { icon: FolderUp, title: '1. Upload photos', description: UPLOAD, linkTo: '/login', linkLabel: 'Sign in to upload' },
  { icon: Tags, title: '2. Review annotations', description: REVIEW, linkTo: '/login', linkLabel: 'Sign in to review' },
  { icon: ChartColumn, title: '3. See & share results', description: INSIGHTS, linkTo: '/login', linkLabel: 'Sign in to see results' },
]
