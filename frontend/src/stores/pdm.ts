import { defineStore } from 'pinia'
import { demoEvents, type EventRow } from '@/api'

export const usePdmStore = defineStore('pdm', {
  state: () => ({ asOf: '2026-09-21 14:00', events: demoEvents as EventRow[] }),
  getters: { alarming: (state) => state.events.filter((event) => event.state === '进行中').length },
})
