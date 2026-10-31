<script setup lang="ts">
import { Monitor, Bell, Document, Setting, Cpu, DataLine, ChatDotRound, Reading } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const menu = [{ path: '/cockpit', label: '驾驶舱（总览）', icon: Monitor }, { path: '/monitoring', label: '设备监测', icon: DataLine }, { path: '/alerts', label: '异常检测与告警', icon: Bell }, { path: '/diagnosis', label: '智能诊断 Agent', icon: Cpu }, { path: '/maintenance', label: '维护决策与工单', icon: Document }, { path: '/messages', label: '值班摘要 / 消息中心', icon: ChatDotRound }, { path: '/knowledge', label: '知识库与规则中心', icon: Reading }]
</script>
<template>
  <el-container class="shell">
    <el-aside width="238px" class="sidebar"><div class="brand"><div class="brand-mark">P</div><div><strong>PdM Agent</strong><small>设备告警处理工作台</small></div></div><el-menu :default-active="route.path" router class="nav"><el-menu-item v-for="item in menu" :key="item.path" :index="item.path"><el-icon><component :is="item.icon" /></el-icon>{{ item.label }}</el-menu-item></el-menu><div class="sidebar-foot"><el-icon><Setting /></el-icon> 回放时点：2026-09-21 14:00</div></el-aside>
    <el-container><el-header class="topbar"><div><span class="eyebrow">AI + 智能制造 · 数据驱动 · 智能诊断 · 预防为主</span><h1>{{ route.path === '/cockpit' ? '驾驶舱' : route.path.includes('/events/') ? '事件详情' : menu.find((item) => route.path.startsWith(item.path))?.label || '业务模块' }}</h1></div><el-tag type="success" effect="light">离线演示模式</el-tag></el-header><el-main class="content"><router-view /></el-main></el-container>
  </el-container>
</template>
