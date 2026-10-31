<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const modules = { monitoring: { title: '设备监测', desc: '设备台账、设备画像、实时信号与特征可视化', items: ['设备台账', '设备画像', '实时信号 / 特征可视化'] }, diagnosis: { title: '智能诊断 Agent', desc: '从证据查询到根因分析、误报判别和报告生成', items: ['证据查询', '根因分析', '误报判别', '诊断报告生成'] }, messages: { title: '值班摘要 / 消息中心', desc: '把高优先级事件整理成今天必须处理的事项', items: ['每日待处理摘要', '订阅推送', '班组提醒'] }, knowledge: { title: '知识库与规则中心', desc: '统一管理维修文档、历史案例、告警规则和经验沉淀', items: ['设备维修文档', '历史故障案例', '告警规则 / 阈值', '经验沉淀'] } }
const current = computed(() => modules[route.meta.key as keyof typeof modules] || modules.monitoring)
</script>
<template><div class="page-head"><div><h2>{{ current.title }}</h2><p>{{ current.desc }}</p></div><el-tag type="warning">建设中</el-tag></div><el-row :gutter="18"><el-col v-for="(item, index) in current.items" :key="item" :span="index === 0 ? 24 : 8"><el-card shadow="never" class="module-card"><div class="module-index">0{{ index + 1 }}</div><h3>{{ item }}</h3><p>保留统一入口，后续接入 Python 检测与 Agent API。</p><el-button text type="primary">进入功能</el-button></el-card></el-col></el-row></template>
