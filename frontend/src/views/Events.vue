<script setup lang="ts">
import { usePdmStore } from '@/stores/pdm'
import type { EventRow } from '@/api'
const store = usePdmStore()
const openEvent = (row: EventRow) => { window.location.href = `/events/${row.id}` }
</script>
<template><div class="page-head"><div><h2>事件队列</h2><p>进行中的事件优先，分数由异常程度、持续时间、影响范围和设备重要性组成</p></div><el-date-picker v-model="store.asOf" type="datetime" value-format="YYYY-MM-DD HH:mm" /></div><el-card shadow="never" class="panel"><el-table :data="store.events" stripe @row-click="openEvent"><el-table-column prop="id" label="事件" min-width="210"/><el-table-column prop="equipment" label="设备" min-width="220"/><el-table-column prop="kind" label="类型" width="150"/><el-table-column prop="start" label="开始" width="130"/><el-table-column label="状态" width="120"><template #default="{ row }"><el-tag :type="row.state === '进行中' ? 'danger' : 'info'">{{ row.state }}</el-tag></template></el-table-column><el-table-column label="峰值 z" width="110"><template #default="{ row }">{{ row.z }} σ</template></el-table-column><el-table-column prop="score" label="总分" width="100" sortable/></el-table></el-card></template>
