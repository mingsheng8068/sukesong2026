import axios from 'axios'

export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api', timeout: 10000 })

export type EventRow = { id: string; equipment: string; line: string; kind: string; state: string; z: number; score: number; start: string }

export const demoEvents: EventRow[] = [
  { id: 'EV-IM011-0920T23', equipment: '注塑机 EQ-IM-011', line: 'L03', kind: '持续偏离', state: '进行中', z: 3.11, score: 35.5, start: '09-20 23:00' },
  { id: 'EV-AGV031-0921T09', equipment: 'AGV搬运车 EQ-AGV-031', line: 'L07', kind: '持续偏离', state: '进行中', z: 2.59, score: 30.2, start: '09-21 09:00' },
  { id: 'EV-RB021-0920T16', equipment: '装配机器人 EQ-RB-021', line: 'L05', kind: '疑似传感器尖峰', state: '已结束', z: 4.82, score: 8.0, start: '09-20 16:00' },
]
