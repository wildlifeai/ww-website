import { createContext, useContext, useState, useEffect, useRef, type ReactNode } from 'react'
import { supabase } from '../config/supabase'
import { useAuth } from './useAuth'
import {
  EMPTY_SELECTION,
  clearProjects,
  projectQuery,
  selectAllProjects,
  selectionAfterLoad,
  toggleProjectSelection,
  type SelectionState,
} from '../lib/projectSelection'
import { shouldRefreshOnReturn, subscribeToTabReturn } from '../lib/tabReturnRefresh'

export interface Project {
  id: string
  name: string
}

interface ProjectSelectionContextType {
  projects: Project[]
  selectedProjectIds: string[]
  isLoading: boolean
  /**
   * The project ids a page queries, or null while the list loads or when nothing is selected.
   * Never query without a project filter: an empty selection means none, not all (#214).
   */
  queryProjectIds: string[] | null
  /** The list has loaded and nothing is ticked: show `NoProjectSelected` and query nothing. */
  noProjectSelected: boolean
  toggleProject: (id: string) => void
  selectAll: () => void
  clearAll: () => void
  /** Refetch the list, e.g. after accepting a project invitation or creating a project. */
  reloadProjects: () => void
}

const ProjectSelectionContext = createContext<ProjectSelectionContextType | undefined>(undefined)

export const ProjectSelectionProvider = ({ children }: { children: ReactNode }) => {
  const { user } = useAuth()
  const [selection, setSelection] = useState<SelectionState>(EMPTY_SELECTION)
  const [isLoading, setIsLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)
  const lastLoadAt = useRef<number | null>(null)

  useEffect(() => {
    if (!user) {
      const timer = setTimeout(() => {
        setSelection(EMPTY_SELECTION)
        setIsLoading(false)
      }, 0)
      return () => clearTimeout(timer)
    }

    let isMounted = true
    const fetchProjects = async () => {
      // No loading state on a reload: pages keep their data until the list changes.
      lastLoadAt.current = Date.now()
      const { data, error } = await supabase
        .from('projects')
        .select('id, name')
        .is('deleted_at', null)
        .order('name')
      
      if (isMounted) {
        // Select every project on load, so an empty selection only ever means none.
        setSelection(prev => selectionAfterLoad(prev, user.id, error ? null : data))
        setIsLoading(false)
      }
    }
    fetchProjects()
    
    return () => { isMounted = false }
  }, [user, reloadKey])

  // A project created in the mobile app, or in another tab, shows up on return to this one (#299).
  const signedIn = !!user
  useEffect(() => {
    if (!signedIn) return
    return subscribeToTabReturn(() => {
      if (shouldRefreshOnReturn(lastLoadAt.current, Date.now())) setReloadKey(k => k + 1)
    })
  }, [signedIn])

  const { projects, selectedProjectIds } = selection
  // Until the list for this user has loaded, the empty selection is not a choice the user made.
  const listLoading = isLoading || (!!user && selection.userId !== user.id)

  const toggleProject = (id: string) => setSelection(prev => toggleProjectSelection(prev, id))
  const selectAll = () => setSelection(selectAllProjects)
  const clearAll = () => setSelection(clearProjects)
  const reloadProjects = () => setReloadKey(k => k + 1)
  const { ids: queryProjectIds, none: noProjectSelected } = projectQuery(selectedProjectIds, listLoading)

  const value = {
    projects,
    selectedProjectIds,
    isLoading: listLoading,
    queryProjectIds,
    noProjectSelected,
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
