import { useState } from 'react'

export function useDragAndDrop(onFiles: (files: File[]) => void) {
  const [isDragging, setIsDragging] = useState(false)

  const readEntryRecursive = (entry: any): Promise<File[]> => {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file(
          (file: File) => {
            Object.defineProperty(file, 'entryPath', {
              value: entry.fullPath,
              writable: false,
            })
            resolve([file])
          },
          () => resolve([])
        )
      } else if (entry.isDirectory) {
        const reader = entry.createReader()
        const allFiles: File[] = []

        const readBatch = () => {
          reader.readEntries(
            async (entries: any[]) => {
              if (entries.length === 0) {
                resolve(allFiles)
                return
              }
              for (const e of entries) {
                const childFiles = await readEntryRecursive(e)
                allFiles.push(...childFiles)
              }
              readBatch()
            },
            () => resolve(allFiles)
          )
        }

        readBatch()
      } else {
        resolve([])
      }
    })
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)

    const items = e.dataTransfer.items
    if (!items) return

    const files: File[] = []
    for (let i = 0; i < items.length; i++) {
      const entry = (items[i] as any).webkitGetAsEntry?.()
      if (entry) {
        const entryFiles = await readEntryRecursive(entry)
        files.push(...entryFiles)
      }
    }

    onFiles(files)
  }

  return {
    isDragging,
    bind: {
      onDragOver: (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        setIsDragging(true)
      },
      onDragLeave: (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        setIsDragging(false)
      },
      onDrop: handleDrop,
    },
  }
}
