<script setup lang="ts">
import { computed } from "vue";
import { getStatusPresentation } from "../status-display";

const props = defineProps<{ value: string }>();
// 所有页面复用同一状态字典，避免同一状态出现不同文案或颜色。
const presentation = computed(() => getStatusPresentation(props.value));
</script>
<template>
  <el-tag
    effect="plain"
    round
    :title="presentation.label === '未知状态' ? value : undefined"
    :style="{
      color: presentation.color,
      backgroundColor: presentation.background,
      borderColor: presentation.border,
    }"
    ><span
      class="status-dot"
      :style="{ backgroundColor: presentation.color }"
    />{{ presentation.label }}</el-tag
  >
</template>

<style scoped>
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 5px;
  border-radius: 50%;
  vertical-align: 1px;
}
</style>
