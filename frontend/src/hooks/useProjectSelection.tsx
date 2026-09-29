import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import { supabase } from '../config/supabase'
import { useAuth } from './useAuth'

export interface Project {
  id: string
  name: string
}

interface ProjectSelectionContextType {
  projects: Project[]
  selectedProjectIds: string[]
  isLoading: boolean
  toggleProject: (id: string) => void
  selectAll: () => void
  clearAll: () => void
  /** Refetch the list, e.g. after accepting a project invitation. */
  reloadProjects: () => void
}

const ProjectSelectionContext = createContext<ProjectSelectionContextType | undefined>(undefined)

export const ProjectSelectionProvider = ({ children }: { children: ReactNode }) => {
  const { user } = useAuth()
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    if (!user) {
      setTimeout(() => {
        setProjects([])
        setSelectedProjectIds([])
        setIsLoading(false)
      }, 0)
      return
    }

    let isMounted = true
    const fetchProjects = async () => {
      setIsLoading(true)
      const { data, error } = await supabase
        .from('projects')
        .select('id, name')
        .is('deleted_at', null)
        .order('name')
      
      if (isMounted) {
        if (!error && data) {
          setProjects(data)
        }
        setIsLoading(false)
      }
    }
    fetchProjects()
    
    return () => { isMounted = false }
  }, [user, reloadKey])

  const toggleProject = (id: string) => {
    setSelectedProjectIds(prev => 
      prev.includes(id) ? prev.filter(pId => pId !== id) : [...prev, id]
    )
  }

  const selectAll = () => setSelectedProjectIds(projects.map(p => p.id))
  const clearAll = () => setSelectedProjectIds([])
  const reloadProjects = () => setReloadKey(k => k + 1)

  const value = {
    projects,
    selectedProjectIds,
    isLoading,
    toggleProject,
    selectAll,
    clearAll,
    reloadProjects,
  }

  return (
    <ProjectSelectionContext.Provider value={value}>
      {children}
    </ProjectSelectionContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export const useProjectSelection = () => {
  const context = useContext(ProjectSelectionContext)
  if (context === undefined) {
    throw new Error('useProjectSelection must be used within a ProjectSelectionProvider')
  }
  return context
}
