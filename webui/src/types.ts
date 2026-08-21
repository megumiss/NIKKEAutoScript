export type Instance = { name: string; state: number; mod: string; current_task?: string; next_task?: string; remark?: string }
export type Field = { key: string; widget: string; title: string; help: string; value: any; display: string; options: any[]; mode?: string; data_endpoint?: string; path_picker?: any; special_data?: any }
export type LogEntry = { id: number; html: string; rank: number }
export type LogFileRef = { date: string; source: string }
export type Announcement = { id: string; date: string; title: string; type: string; content: string; read: boolean }
export interface WebLink { name: string; url: string; direct?: boolean; i18n?: Record<string, string> }
